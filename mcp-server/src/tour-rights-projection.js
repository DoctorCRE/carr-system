// Typed application adapter for the Tour rights/evidence/public-projection
// bounded database application seams. The database remains the authority for
// rights currentness, append-only lineage, projection completeness, and the
// canonical seal digest. This module contributes no publication or promotion
// capability.

import { organizationTenantForActor } from "./identity.js";
import {
  PUBLIC_TOUR_FIELD_KEYS,
  REQUIRED_PUBLIC_PROPERTY_FIELDS,
  isClientTourFieldKey,
  requiredTimestamp,
  snapshotCanonicalJsonValue,
  snapshotPublicValue,
} from "./tour-operations-contract.js";
import { clientSafeMetric, isClientSafeText } from "./tour-client-value-safety.js";

function clientValueSafe(fieldKey, value) {
  if (fieldKey === "size" || fieldKey === "asking_economics") return clientSafeMetric(value) !== undefined;
  return isClientSafeText(value);
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const AUTHORITY_FIELDS = new Set([
  "tenant", "tenant_id", "organization_tenant_id", "actor", "actor_id",
  "reviewer", "identity", "authorization", "authorization_class", "sponsor",
  "sponsoring_human_id", "sponsoring_human_slug", "human_slug",
]);

const RIGHTS_FIELDS = new Set([
  "idempotency_key", "provider", "sku", "policy_key", "receipt_version",
  "receipt_digest", "terms_url", "reviewed_at", "intended_use",
  "allowed_field_classes", "allowed_use_classes", "effective_at", "expires_at",
  "supersedes_receipt_id",
]);
const REVOCATION_FIELDS = new Set([
  "idempotency_key", "rights_receipt_id", "revoked_at", "receipt_digest",
]);
const EVIDENCE_FIELDS = new Set([
  "idempotency_key", "stable_locator", "evidence_class", "retrieved_at",
  "retrieval_status", "content_digest", "rights_receipt_id", "rights_provider",
  "rights_policy_key", "data_classification",
]);
const ASSERTION_FIELDS = new Set([
  "idempotency_key", "property_id", "field_key", "value", "source_evidence_id",
  "rights_receipt_id", "observed_at", "effective_from", "effective_to",
  "confidence", "data_classification", "review_state",
]);
const DRAFT_FIELDS = new Set([
  "idempotency_key", "tour_id", "projection_version", "route_version", "as_of",
]);
const SEAL_FIELDS = new Set([
  "idempotency_key", "projection_id", "selected_facts", "receipt_digest",
]);
const READ_FIELDS = new Set(["projection_id"]);

function fail(ToolError, payload) { throw new ToolError(payload); }

function guardAuthority(args, ToolError) {
  const fields = Object.keys(args || {}).filter(key => AUTHORITY_FIELDS.has(key));
  if (fields.length) fail(ToolError, { error: "caller_authority_field_forbidden", fields });
}

function exactFields(args, allowed, ToolError) {
  guardAuthority(args, ToolError);
  if (!args || typeof args !== "object" || Array.isArray(args))
    fail(ToolError, { error: "tour_input_invalid", field: "payload" });
  const fields = Object.keys(args).filter(key => !allowed.has(key));
  if (fields.length) fail(ToolError, { error: "tour_input_unknown_field", fields });
}

function isPostgresText(value) {
  for (let index = 0; index < value.length; index++) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit === 0) return false;
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      if (index + 1 >= value.length) return false;
      const next = value.charCodeAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) return false;
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function text(value, field, ToolError) {
  if (typeof value !== "string" || !value.trim() || !isPostgresText(value))
    fail(ToolError, { error: "tour_input_invalid", field });
  return value.trim();
}

function uuid(value, field, ToolError) {
  const candidate = text(value, field, ToolError);
  if (!UUID.test(candidate)) fail(ToolError, { error: "tour_input_invalid", field });
  return candidate;
}

function digest(value, field, ToolError) {
  const candidate = text(value, field, ToolError);
  if (!DIGEST.test(candidate)) fail(ToolError, { error: "tour_input_invalid", field });
  return candidate;
}

function timestamp(value, field, ToolError, nullable = false) {
  if (nullable && (value === null || value === undefined)) return null;
  const candidate = text(value, field, ToolError);
  if (!requiredTimestamp(candidate)) fail(ToolError, { error: "tour_input_invalid", field });
  return candidate;
}

function databaseTimestamp(value, field, ToolError, nullable = false) {
  if (nullable && (value === null || value === undefined)) return null;
  const candidate = text(value, field, ToolError);
  if (requiredTimestamp(candidate)) return candidate;
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?[+-]\d{2}:\d{2}$/.test(candidate))
    fail(ToolError, { error: "tour_input_invalid", field });
  const parsed = new Date(candidate);
  if (Number.isNaN(parsed.getTime())) fail(ToolError, { error: "tour_input_invalid", field });
  return parsed.toISOString().replace(".000Z", "Z");
}

function positiveInteger(value, field, ToolError) {
  if (!Number.isInteger(value) || value < 1)
    fail(ToolError, { error: "tour_input_invalid", field });
  return value;
}

const ownEnumerableDataDescriptor = (record, field) => {
  const descriptor = Object.getOwnPropertyDescriptor(record, field);
  return descriptor?.enumerable && Object.hasOwn(descriptor, "value") ? descriptor : null;
};

function snapshotRecord(record, fields) {
  if (!record || Array.isArray(record) || typeof record !== "object") return null;
  const prototype = Object.getPrototypeOf(record);
  if (prototype !== Object.prototype && prototype !== null) return null;
  const snapshot = Object.create(null);
  for (let index = 0; index < fields.length; index++) {
    const descriptor = ownEnumerableDataDescriptor(record, fields[index]);
    if (!descriptor) return null;
    snapshot[fields[index]] = descriptor.value;
  }
  return snapshot;
}

function safeArrayValues(value) {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype) return null;
  const ownKeys = Reflect.ownKeys(value);
  for (let keyIndex = 0; keyIndex < ownKeys.length; keyIndex++) {
    const key = ownKeys[keyIndex];
    if (typeof key !== "string") return null;
    if (key === "length") continue;
    if (!/^(?:0|[1-9][0-9]*)$/.test(key)) return null;
    const index = Number(key);
    if (!Number.isSafeInteger(index) || index >= value.length || String(index) !== key) return null;
  }
  const values = new Array(value.length);
  for (let index = 0; index < value.length; index++) {
    const descriptor = ownEnumerableDataDescriptor(value, String(index));
    if (!descriptor) return null;
    values[index] = descriptor.value;
  }
  return values;
}

function publicProjection(value, ToolError) {
  const input = snapshotRecord(value, [
    "projection_id", "tour_id", "projection_version", "route_version", "as_of",
    "projection_digest", "facts",
  ]);
  if (!input)
    fail(ToolError, { error: "tour_public_projection_invalid" });
  const projection = {
    projection_id: uuid(input.projection_id, "projection_id", ToolError),
    tour_id: uuid(input.tour_id, "tour_id", ToolError),
    projection_version: positiveInteger(input.projection_version, "projection_version", ToolError),
    route_version: positiveInteger(input.route_version, "route_version", ToolError),
    as_of: databaseTimestamp(input.as_of, "as_of", ToolError),
    projection_digest: digest(input.projection_digest, "projection_digest", ToolError),
    facts: null,
  };
  const factRows = safeArrayValues(input.facts);
  if (!factRows || factRows.length === 0)
    fail(ToolError, { error: "tour_public_projection_invalid", field: "facts" });
  const seen = new Set();
  projection.facts = [];
  let withheld = 0;
  for (let index = 0; index < factRows.length; index++) {
    const fact = snapshotRecord(factRows[index], [
      "property_id", "field_assertion_id", "display_field_key", "value",
      "source_evidence_id", "rights_receipt_id", "observed_at", "effective_from",
      "effective_to",
    ]);
    if (!fact)
      fail(ToolError, { error: "tour_public_projection_invalid", field: `facts[${index}]` });
    const displayFieldKey = text(fact.display_field_key, `facts[${index}].display_field_key`, ToolError);
    // A projection sealed before the client allowlist (V5-J303) may hold a
    // recognised public-shape fact that is now internal (access, caveat,
    // photos, ...). It is withheld, never returned; an unrecognised key still
    // fails the whole read closed below.
    if (PUBLIC_TOUR_FIELD_KEYS.has(displayFieldKey) && !isClientTourFieldKey(displayFieldKey)) {
      withheld += 1;
      continue;
    }
    let publicValue;
    try { publicValue = snapshotPublicValue(displayFieldKey, fact.value); }
    catch { fail(ToolError, { error: "tour_public_projection_invalid", field: `facts[${index}].display_field_key` }); }
    if (!PUBLIC_TOUR_FIELD_KEYS.has(displayFieldKey))
      fail(ToolError, { error: "tour_public_projection_invalid", field: `facts[${index}].display_field_key` });
    // The same client value rule the seal, the browser share and the PDF
    // apply: a legacy fact whose allowed key carries unsafe text is withheld.
    if (!clientValueSafe(displayFieldKey, publicValue)) {
      withheld += 1;
      continue;
    }
    const effectiveFrom = databaseTimestamp(fact.effective_from, `facts[${index}].effective_from`, ToolError);
    const effectiveTo = databaseTimestamp(fact.effective_to, `facts[${index}].effective_to`, ToolError, true);
    if (effectiveTo && Date.parse(effectiveTo) <= Date.parse(effectiveFrom))
      fail(ToolError, { error: "tour_public_projection_invalid", field: `facts[${index}].effective_to` });
    const projected = {
      property_id: uuid(fact.property_id, `facts[${index}].property_id`, ToolError),
      field_assertion_id: uuid(fact.field_assertion_id, `facts[${index}].field_assertion_id`, ToolError),
      display_field_key: displayFieldKey,
      value: publicValue,
      source_evidence_id: uuid(fact.source_evidence_id, `facts[${index}].source_evidence_id`, ToolError),
      rights_receipt_id: uuid(fact.rights_receipt_id, `facts[${index}].rights_receipt_id`, ToolError),
      observed_at: databaseTimestamp(fact.observed_at, `facts[${index}].observed_at`, ToolError),
      effective_from: effectiveFrom,
      effective_to: effectiveTo,
    };
    if (Date.parse(projected.observed_at) > Date.parse(projection.as_of) ||
        Date.parse(projected.effective_from) > Date.parse(projection.as_of) ||
        (projected.effective_to && Date.parse(projected.effective_to) <= Date.parse(projection.as_of)))
      fail(ToolError, { error: "tour_public_projection_invalid", field: `facts[${index}]` });
    const key = `${projected.property_id}\u001f${projected.display_field_key}`;
    if (seen.has(key))
      fail(ToolError, { error: "tour_public_projection_invalid", field: `facts[${index}]`, reason: "duplicate_property_field" });
    seen.add(key);
    projection.facts.push(projected);
  }
  if (projection.facts.length === 0)
    fail(ToolError, { error: "tour_public_projection_invalid", field: "facts" });
  if (withheld) projection.withheld_internal_fact_count = withheld;
  const properties = [];
  const propertySet = new Set();
  for (let index = 0; index < projection.facts.length; index++) {
    const propertyId = projection.facts[index].property_id;
    if (!propertySet.has(propertyId)) {
      propertySet.add(propertyId);
      properties.push(propertyId);
    }
  }
  for (let propertyIndex = 0; propertyIndex < properties.length; propertyIndex++) {
    const propertyId = properties[propertyIndex];
    for (let fieldIndex = 0; fieldIndex < REQUIRED_PUBLIC_PROPERTY_FIELDS.length; fieldIndex++) {
      const fieldKey = REQUIRED_PUBLIC_PROPERTY_FIELDS[fieldIndex];
      if (!seen.has(`${propertyId}\u001f${fieldKey}`))
        fail(ToolError, { error: "tour_public_projection_invalid", field: "facts", reason: "incomplete_property" });
    }
  }
  return projection;
}

function stringArray(value, field, ToolError) {
  const values = safeArrayValues(value);
  if (!values || values.length === 0) fail(ToolError, { error: "tour_input_invalid", field });
  const normalized = new Array(values.length);
  const seen = new Set();
  for (let index = 0; index < values.length; index++) {
    normalized[index] = text(values[index], field, ToolError);
    if (seen.has(normalized[index])) fail(ToolError, { error: "tour_input_invalid", field });
    seen.add(normalized[index]);
  }
  return normalized;
}

function oneOf(value, field, allowed, ToolError) {
  if (!allowed.includes(value)) fail(ToolError, { error: "tour_input_invalid", field });
  return value;
}

function actorId(actor, ToolError) {
  if (!actor || typeof actor.id !== "string" || !actor.id.trim())
    fail(ToolError, { error: "tour_actor_context_required" });
  return actor.id;
}

function tenantFor(actor, ToolError) {
  actorId(actor, ToolError);
  const tenant = organizationTenantForActor(actor);
  if (typeof tenant !== "string" || !tenant) fail(ToolError, { error: "tour_tenant_context_required" });
  return tenant;
}

function validateRights(args, ToolError) {
  exactFields(args, RIGHTS_FIELDS, ToolError);
  uuid(args.idempotency_key, "idempotency_key", ToolError);
  const effectiveAt = timestamp(args.effective_at, "effective_at", ToolError);
  const expiresAt = timestamp(args.expires_at, "expires_at", ToolError, true);
  if (expiresAt && Date.parse(expiresAt) <= Date.parse(effectiveAt))
    fail(ToolError, { error: "tour_input_invalid", field: "expires_at" });
  const version = positiveInteger(args.receipt_version, "receipt_version", ToolError);
  const supersedes = args.supersedes_receipt_id == null
    ? null : uuid(args.supersedes_receipt_id, "supersedes_receipt_id", ToolError);
  if ((version === 1 && supersedes !== null) || (version > 1 && supersedes === null))
    fail(ToolError, { error: "tour_input_invalid", field: "supersedes_receipt_id" });
  let termsUrl;
  try { termsUrl = new URL(text(args.terms_url, "terms_url", ToolError)); }
  catch { fail(ToolError, { error: "tour_input_invalid", field: "terms_url" }); }
  if (termsUrl.protocol !== "https:") fail(ToolError, { error: "tour_input_invalid", field: "terms_url" });
  return {
    provider: text(args.provider, "provider", ToolError),
    sku: args.sku == null ? null : text(args.sku, "sku", ToolError),
    policy_key: text(args.policy_key, "policy_key", ToolError),
    receipt_version: version,
    receipt_digest: digest(args.receipt_digest, "receipt_digest", ToolError),
    terms_url: termsUrl.toString(),
    reviewed_at: timestamp(args.reviewed_at, "reviewed_at", ToolError),
    intended_use: text(args.intended_use, "intended_use", ToolError),
    allowed_field_classes: stringArray(args.allowed_field_classes, "allowed_field_classes", ToolError),
    allowed_use_classes: stringArray(args.allowed_use_classes, "allowed_use_classes", ToolError),
    effective_at: effectiveAt,
    expires_at: expiresAt,
    supersedes_receipt_id: supersedes,
  };
}

function validateEvidence(args, ToolError) {
  exactFields(args, EVIDENCE_FIELDS, ToolError);
  uuid(args.idempotency_key, "idempotency_key", ToolError);
  return {
    stable_locator: text(args.stable_locator, "stable_locator", ToolError),
    evidence_class: oneOf(args.evidence_class, "evidence_class", ["direct_source", "linked_artifact", "public_mirror", "inference"], ToolError),
    retrieved_at: timestamp(args.retrieved_at, "retrieved_at", ToolError),
    retrieval_status: oneOf(args.retrieval_status, "retrieval_status", ["read", "partial", "inaccessible", "failed"], ToolError),
    content_digest: digest(args.content_digest, "content_digest", ToolError),
    rights_receipt_id: uuid(args.rights_receipt_id, "rights_receipt_id", ToolError),
    rights_provider: text(args.rights_provider, "rights_provider", ToolError),
    rights_policy_key: text(args.rights_policy_key, "rights_policy_key", ToolError),
    data_classification: oneOf(args.data_classification, "data_classification", ["public", "client_authorized", "internal", "restricted"], ToolError),
  };
}

function validateAssertion(args, ToolError) {
  exactFields(args, ASSERTION_FIELDS, ToolError);
  const idempotencyKey = uuid(args.idempotency_key, "idempotency_key", ToolError);
  let value;
  try { value = snapshotCanonicalJsonValue(args.value); }
  catch { fail(ToolError, { error: "tour_input_invalid", field: "value" }); }
  const effectiveFrom = timestamp(args.effective_from, "effective_from", ToolError);
  const effectiveTo = timestamp(args.effective_to, "effective_to", ToolError, true);
  if (effectiveTo && Date.parse(effectiveTo) < Date.parse(effectiveFrom))
    fail(ToolError, { error: "tour_input_invalid", field: "effective_to" });
  return {
    idempotency_key: idempotencyKey,
    property_id: uuid(args.property_id, "property_id", ToolError),
    field_key: text(args.field_key, "field_key", ToolError),
    value,
    source_evidence_id: uuid(args.source_evidence_id, "source_evidence_id", ToolError),
    rights_receipt_id: uuid(args.rights_receipt_id, "rights_receipt_id", ToolError),
    observed_at: timestamp(args.observed_at, "observed_at", ToolError),
    effective_from: effectiveFrom,
    effective_to: effectiveTo,
    confidence: oneOf(args.confidence, "confidence", ["low", "medium", "high", "unknown"], ToolError),
    data_classification: oneOf(args.data_classification, "data_classification", ["public", "client_authorized", "internal", "restricted"], ToolError),
    review_state: oneOf(args.review_state, "review_state", ["unreviewed", "reviewed", "conflicted", "superseded", "withdrawn"], ToolError),
  };
}

function validateSelectedFacts(value, ToolError) {
  const values = safeArrayValues(value);
  if (!values || values.length === 0)
    fail(ToolError, { error: "tour_selected_facts_invalid" });
  const seen = new Set();
  const selected = new Array(values.length);
  for (let index = 0; index < values.length; index++) {
    const item = snapshotRecord(values[index], ["display_field_key", "field_assertion_id", "property_id"]);
    if (!item || Reflect.ownKeys(values[index]).length !== 3)
      fail(ToolError, { error: "tour_selected_facts_invalid", index });
    const fact = {
      property_id: uuid(item.property_id, `selected_facts[${index}].property_id`, ToolError),
      field_assertion_id: uuid(item.field_assertion_id, `selected_facts[${index}].field_assertion_id`, ToolError),
      display_field_key: text(item.display_field_key, `selected_facts[${index}].display_field_key`, ToolError),
    };
    // Default deny: only the client allowlist can be sealed into a projection
    // a client will see. The database trigger refuses the same set.
    if (!isClientTourFieldKey(fact.display_field_key))
      fail(ToolError, { error: "tour_selected_facts_invalid", index, field: "display_field_key", reason: "field_not_client_allowlisted" });
    const key = `${fact.property_id}\u001f${fact.display_field_key}`;
    if (seen.has(key)) fail(ToolError, { error: "tour_selected_facts_invalid", index, reason: "duplicate_property_field" });
    seen.add(key);
    selected[index] = fact;
  }
  return selected;
}

function schema(properties, required) {
  return { type: "object", additionalProperties: false, properties, required };
}

const idempotencyProperty = { idempotency_key: { type: "string", description: "UUID; reuse only when retrying the same intended write" } };

export function tourRightsProjectionTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "append-tour-rights-receipt": {
      write: true,
      authorityOnly: true,
      description: "Append one immutable, versioned provider/policy rights receipt for Tour evidence and fact use. Tenant and reviewer actor are derived by the server. This does not publish or promote a Tour.",
      inputSchema: schema({ ...idempotencyProperty,
        provider: { type: "string" }, sku: { type: ["string", "null"] }, policy_key: { type: "string" },
        receipt_version: { type: "integer", minimum: 1 }, receipt_digest: { type: "string" }, terms_url: { type: "string" },
        reviewed_at: { type: "string" }, intended_use: { type: "string" },
        allowed_field_classes: { type: "array", items: { type: "string" }, minItems: 1 },
        allowed_use_classes: { type: "array", items: { type: "string" }, minItems: 1 },
        effective_at: { type: "string" }, expires_at: { type: ["string", "null"] },
        supersedes_receipt_id: { type: ["string", "null"] },
      }, ["idempotency_key", "provider", "policy_key", "receipt_version", "receipt_digest", "terms_url", "reviewed_at", "intended_use", "allowed_field_classes", "allowed_use_classes", "effective_at"]),
      handler: async (c, actor, args) => {
        const validated = validateRights(args, ToolError);
        const tenant = tenantFor(actor, ToolError);
        return withEnvelope(c, actor, "append-tour-rights-receipt", args, async () => {
          const payload = { organization_tenant_id: tenant, ...validated, reviewer: actorId(actor, ToolError), revoked_at: null, status: "active" };
          const result = await c.query(
            "select ops.append_tour_rights_receipt($1::jsonb) as rights_receipt_id /* tour-rights-projection:append-rights */",
            [JSON.stringify(payload)]);
          const id = result.rows[0]?.rights_receipt_id;
          if (!id) fail(ToolError, { error: "tour_write_refused", entity: "rights_receipt" });
          await writeEvent(c, actor, "append-tour-rights-receipt", "tour_rights_receipt", id, {
            field: "status", new: { status: "active", provider: payload.provider, policy_key: payload.policy_key, receipt_version: payload.receipt_version },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, rights_receipt_id: id };
        });
      },
    },

    "revoke-tour-rights-receipt": {
      write: true,
      authorityOnly: true,
      description: "Append an immutable revocation successor for one Tour rights receipt. Tenant and revoking actor are server-derived. This does not mutate the predecessor.",
      inputSchema: schema({ ...idempotencyProperty, rights_receipt_id: { type: "string" }, revoked_at: { type: "string" }, receipt_digest: { type: "string" } }, ["idempotency_key", "rights_receipt_id", "revoked_at", "receipt_digest"]),
      handler: async (c, actor, args) => {
        exactFields(args, REVOCATION_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const predecessor = uuid(args.rights_receipt_id, "rights_receipt_id", ToolError);
        const revokedAt = timestamp(args.revoked_at, "revoked_at", ToolError);
        const receiptDigest = digest(args.receipt_digest, "receipt_digest", ToolError);
        const tenant = tenantFor(actor, ToolError);
        return withEnvelope(c, actor, "revoke-tour-rights-receipt", args, async () => {
          const result = await c.query(
            "select ops.revoke_tour_rights_receipt($1::text,$2::uuid,$3::timestamptz,$4::text,$5::text) as rights_receipt_id /* tour-rights-projection:revoke-rights */",
            [tenant, predecessor, revokedAt, actorId(actor, ToolError), receiptDigest]);
          const id = result.rows[0]?.rights_receipt_id;
          if (!id) fail(ToolError, { error: "tour_write_refused", entity: "rights_receipt_revocation" });
          await writeEvent(c, actor, "revoke-tour-rights-receipt", "tour_rights_receipt", id, {
            field: "status", old: { rights_receipt_id: predecessor, status: "active" },
            new: { status: "revoked", revoked_at: revokedAt }, idempotency_key: args.idempotency_key,
          });
          return { ok: true, rights_receipt_id: id, supersedes_receipt_id: predecessor, status: "revoked" };
        });
      },
    },

    "append-tour-source-evidence": {
      write: true,
      description: "Append one immutable Tour source-evidence record bound to an exact provider, policy, and rights receipt. Tenant is server-derived.",
      inputSchema: schema({ ...idempotencyProperty,
        stable_locator: { type: "string" }, evidence_class: { type: "string" }, retrieved_at: { type: "string" },
        retrieval_status: { type: "string" }, content_digest: { type: "string" }, rights_receipt_id: { type: "string" },
        rights_provider: { type: "string" }, rights_policy_key: { type: "string" }, data_classification: { type: "string" },
      }, ["idempotency_key", "stable_locator", "evidence_class", "retrieved_at", "retrieval_status", "content_digest", "rights_receipt_id", "rights_provider", "rights_policy_key", "data_classification"]),
      handler: async (c, actor, args) => {
        const validated = validateEvidence(args, ToolError);
        const tenant = tenantFor(actor, ToolError);
        return withEnvelope(c, actor, "append-tour-source-evidence", args, async () => {
          const result = await c.query(
            "select ops.append_tour_source_evidence($1::jsonb) as source_evidence_id /* tour-rights-projection:append-evidence */",
            [JSON.stringify({ organization_tenant_id: tenant, ...validated })]);
          const id = result.rows[0]?.source_evidence_id;
          if (!id) fail(ToolError, { error: "tour_write_refused", entity: "source_evidence" });
          await writeEvent(c, actor, "append-tour-source-evidence", "tour_source_evidence", id, {
            field: "source", new: { stable_locator: validated.stable_locator, content_digest: validated.content_digest, rights_receipt_id: validated.rights_receipt_id },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, source_evidence_id: id };
        });
      },
    },

    "append-tour-field-assertion": {
      write: true,
      authorityOnly: true,
      description: "Append one immutable, authority-reviewed, provenance-bound Tour property field assertion. Tenant is server-derived; this does not select the fact for public display.",
      inputSchema: schema({ ...idempotencyProperty,
        property_id: { type: "string" }, field_key: { type: "string" }, value: {}, source_evidence_id: { type: "string" },
        rights_receipt_id: { type: "string" }, observed_at: { type: "string" }, effective_from: { type: "string" },
        effective_to: { type: ["string", "null"] }, confidence: { type: "string" },
        data_classification: { type: "string" }, review_state: { type: "string" },
      }, ["idempotency_key", "property_id", "field_key", "value", "source_evidence_id", "rights_receipt_id", "observed_at", "effective_from", "confidence", "data_classification", "review_state"]),
      handler: async (c, actor, args) => {
        const validatedRequest = validateAssertion(args, ToolError);
        const { idempotency_key: idempotencyKey, ...validated } = validatedRequest;
        const tenant = tenantFor(actor, ToolError);
        return withEnvelope(c, actor, "append-tour-field-assertion", validatedRequest, async () => {
          const result = await c.query(
            "select ops.append_tour_field_assertion($1::jsonb) as field_assertion_id /* tour-rights-projection:append-assertion */",
            [JSON.stringify({ organization_tenant_id: tenant, ...validated })]);
          const id = result.rows[0]?.field_assertion_id;
          if (!id) fail(ToolError, { error: "tour_write_refused", entity: "field_assertion" });
          await writeEvent(c, actor, "append-tour-field-assertion", "tour_field_assertion", id, {
            field: validated.field_key, new: { property_id: validated.property_id, source_evidence_id: validated.source_evidence_id, rights_receipt_id: validated.rights_receipt_id, review_state: validated.review_state },
            idempotency_key: idempotencyKey,
          });
          return { ok: true, field_assertion_id: id };
        });
      },
    },

    "create-tour-public-projection-draft": {
      write: true,
      description: "Create an empty facts-only Tour public-projection draft for one immutable route membership set. Tenant is server-derived. Draft creation grants no publication authority.",
      inputSchema: schema({ ...idempotencyProperty, tour_id: { type: "string" }, projection_version: { type: "integer", minimum: 1 }, route_version: { type: "integer", minimum: 1 }, as_of: { type: "string" } }, ["idempotency_key", "tour_id", "projection_version", "route_version", "as_of"]),
      handler: async (c, actor, args) => {
        exactFields(args, DRAFT_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const tourId = uuid(args.tour_id, "tour_id", ToolError);
        const projectionVersion = positiveInteger(args.projection_version, "projection_version", ToolError);
        const routeVersion = positiveInteger(args.route_version, "route_version", ToolError);
        const asOf = timestamp(args.as_of, "as_of", ToolError);
        const tenant = tenantFor(actor, ToolError);
        return withEnvelope(c, actor, "create-tour-public-projection-draft", args, async () => {
          const result = await c.query(
            "select ops.create_tour_public_projection_draft($1::text,$2::uuid,$3::integer,$4::integer,$5::timestamptz) as projection_id /* tour-rights-projection:create-draft */",
            [tenant, tourId, projectionVersion, routeVersion, asOf]);
          const id = result.rows[0]?.projection_id;
          if (!id) fail(ToolError, { error: "tour_write_refused", entity: "public_projection" });
          await writeEvent(c, actor, "create-tour-public-projection-draft", "tour_public_projection", id, {
            field: "status", new: { status: "draft", tour_id: tourId, projection_version: projectionVersion, route_version: routeVersion, as_of: asOf },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, projection_id: id, status: "draft" };
        });
      },
    },

    "seal-tour-public-projection": {
      write: true,
      authorityOnly: true,
      description: "Atomically insert the complete selected public fact set and its immutable approval seal. The database rechecks selected membership, reviewed public assertions, current rights, safe values, and computes the canonical digest. Tenant and sealing actor are server-derived. This is not publication or promotion.",
      inputSchema: schema({ ...idempotencyProperty, projection_id: { type: "string" }, selected_facts: { type: "array", minItems: 1, items: { type: "object", additionalProperties: false, properties: { property_id: { type: "string" }, field_assertion_id: { type: "string" }, display_field_key: { type: "string" } }, required: ["property_id", "field_assertion_id", "display_field_key"] } }, receipt_digest: { type: "string" } }, ["idempotency_key", "projection_id", "selected_facts", "receipt_digest"]),
      handler: async (c, actor, args) => {
        exactFields(args, SEAL_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const projectionId = uuid(args.projection_id, "projection_id", ToolError);
        const selectedFacts = validateSelectedFacts(args.selected_facts, ToolError);
        const receiptDigest = digest(args.receipt_digest, "receipt_digest", ToolError);
        const tenant = tenantFor(actor, ToolError);
        return withEnvelope(c, actor, "seal-tour-public-projection", args, async () => {
          const result = await c.query(
            "select ops.seal_tour_public_projection($1::text,$2::uuid,$3::jsonb,$4::text,$5::text) as projection_digest /* tour-rights-projection:seal */",
            [tenant, projectionId, JSON.stringify(selectedFacts), actorId(actor, ToolError), receiptDigest]);
          const projectionDigest = result.rows[0]?.projection_digest;
          if (!DIGEST.test(projectionDigest || "")) fail(ToolError, { error: "tour_write_refused", entity: "projection_seal" });
          await writeEvent(c, actor, "seal-tour-public-projection", "tour_public_projection", projectionId, {
            field: "status", old: { status: "draft" }, new: { status: "approved", projection_digest: projectionDigest, selected_fact_count: selectedFacts.length },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, projection_id: projectionId, projection_digest: projectionDigest, status: "approved" };
        });
      },
    },

    "read-tour-public-projection": {
      writerConnection: true,
      description: "Read one tenant-scoped approved Tour public projection with its reviewed public values and bounded provenance. Draft, unsealed, quarantined, and internal material are absent.",
      inputSchema: schema({ projection_id: { type: "string" } }, ["projection_id"]),
      handler: async (c, actor, args) => {
        exactFields(args, READ_FIELDS, ToolError);
        const projectionId = uuid(args.projection_id, "projection_id", ToolError);
        const tenant = tenantFor(actor, ToolError);
        const result = await c.query(
          "select ops.read_tour_public_projection($1::text,$2::uuid) as projection /* tour-rights-projection:read-approved */",
          [tenant, projectionId]);
        const projection = result.rows[0]?.projection;
        if (!projection) fail(ToolError, { error: "tour_public_projection_not_found", projection_id: projectionId });
        return { ok: true, projection: publicProjection(projection, ToolError) };
      },
    },
  };
}
