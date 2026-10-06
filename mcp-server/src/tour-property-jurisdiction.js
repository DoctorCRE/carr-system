// Typed adapter for the three reviewed Tour Slice 3 persistence seams, plus
// the 0565 registration door that mints a property TOGETHER WITH its first
// rights-bound identifier assertion (there is no bare create-property path).
// It intentionally exposes no unreviewed identity, lineage, jurisdiction, map,
// route, publication, or promotion capabilities.

import { authorizationClassForActor, organizationTenantForActor } from "./identity.js";
import { requiredTimestamp } from "./tour-operations-contract.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const AUTHORITY_FIELDS = new Set([
  "tenant", "tenant_id", "organization_tenant_id", "actor", "actor_id", "reviewer",
  "identity", "authorization", "authorization_class", "sponsor", "human_slug",
]);
const IDENTIFIER_SCHEMES = new Set(["carr_property", "county_parcel", "building", "listing", "provider", "legacy"]);
const CONFIDENCE = new Set(["low", "medium", "high", "unknown"]);
const ASSERTION_REVIEW = new Set(["unreviewed", "reviewed", "conflicted", "superseded", "withdrawn"]);
const COORDINATE_ROLES = new Set(["parcel_centroid", "building_centroid", "address_point", "geocoder_candidate", "entrance", "driveway", "parking_access", "other"]);
const PROVIDER_ROLES = new Set(["geocoder_candidate", "address_point", "building_centroid", "parcel_centroid"]);
const PRECISION_CLASSES = new Set(["unknown", "approximate", "parcel", "building", "address", "entrance", "surveyed"]);
const COORDINATE_REVIEW = new Set(["unreviewed", "reviewed", "rejected", "superseded"]);

const IDENTIFIER_FIELDS = new Set([
  "idempotency_key", "property_id", "identifier_scheme", "identifier_value", "normalized_identifier",
  "source_evidence_id", "rights_receipt_id", "observed_at", "confidence", "review_state", "assertion_digest",
]);
const REGISTRATION_FIELDS = new Set([...IDENTIFIER_FIELDS].filter(field => field !== "property_id"));
const COORDINATE_FIELDS = new Set([
  "idempotency_key", "property_id", "coordinate_role", "latitude", "longitude", "precision_class",
  "source_evidence_id", "rights_receipt_id", "provider", "observed_at", "review_state", "access_notes",
]);
const VERIFICATION_FIELDS = new Set([
  "idempotency_key", "property_id", "coordinate_candidate_id", "verified_at", "evidence_reference",
  "native_navigation_proof", "receipt_digest",
]);
const NATIVE_PROOF_FIELDS = new Set(["platform", "tested_at", "travel_mode", "evidence_digest"]);

function fail(ToolError, payload) { throw new ToolError(payload); }

function exactFields(args, allowed, ToolError) {
  const keys = args && typeof args === "object" && !Array.isArray(args) ? Object.keys(args) : [];
  const authority = keys.filter(key => AUTHORITY_FIELDS.has(key));
  if (authority.length) fail(ToolError, { error: "caller_authority_field_forbidden", fields: authority });
  if (!args || typeof args !== "object" || Array.isArray(args))
    fail(ToolError, { error: "tour_input_invalid", field: "payload" });
  const unknown = keys.filter(key => !allowed.has(key));
  if (unknown.length) fail(ToolError, { error: "tour_input_unknown_field", fields: unknown });
}

function text(value, field, ToolError, nullable = false) {
  if (nullable && (value === null || value === undefined)) return null;
  if (typeof value !== "string" || !value.trim()) fail(ToolError, { error: "tour_input_invalid", field });
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

function timestamp(value, field, ToolError) {
  const candidate = text(value, field, ToolError);
  if (!requiredTimestamp(candidate)) fail(ToolError, { error: "tour_input_invalid", field });
  return candidate;
}

function oneOf(value, field, allowed, ToolError) {
  if (!allowed.has(value)) fail(ToolError, { error: "tour_input_invalid", field });
  return value;
}

function boundedNumber(value, field, min, max, ToolError) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max)
    fail(ToolError, { error: "tour_input_invalid", field });
  return value;
}

function actorId(actor, ToolError) {
  if (!actor || typeof actor.id !== "string" || !actor.id.trim())
    fail(ToolError, { error: "tour_actor_context_required" });
  return actor.id;
}

function verifiedHumanActorId(actor, ToolError) {
  const id = actorId(actor, ToolError);
  if (actor?.human !== true || authorizationClassForActor(actor) !== "verified_partner")
    fail(ToolError, { error: "tour_human_verification_required" });
  return id;
}

function tenantFor(actor, ToolError) {
  actorId(actor, ToolError);
  const tenant = organizationTenantForActor(actor);
  if (typeof tenant !== "string" || !tenant) fail(ToolError, { error: "tour_tenant_context_required" });
  return tenant;
}

function nativeNavigationProof(value, ToolError) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    fail(ToolError, { error: "tour_native_navigation_proof_invalid" });
  const keys = Object.keys(value).sort();
  if (keys.join(",") !== [...NATIVE_PROOF_FIELDS].sort().join(","))
    fail(ToolError, { error: "tour_native_navigation_proof_invalid" });
  const platform = oneOf(value.platform, "native_navigation_proof.platform", new Set(["google_maps", "apple_maps"]), ToolError);
  return {
    platform,
    tested_at: timestamp(value.tested_at, "native_navigation_proof.tested_at", ToolError),
    travel_mode: oneOf(value.travel_mode, "native_navigation_proof.travel_mode", new Set(["driving", "walking"]), ToolError),
    evidence_digest: digest(value.evidence_digest, "native_navigation_proof.evidence_digest", ToolError),
  };
}

function schema(properties, required) {
  return { type: "object", additionalProperties: false, properties, required };
}

const idempotencyProperty = { idempotency_key: { type: "string", description: "UUID; reuse only for the exact same write retry" } };

export function tourPropertyJurisdictionTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "append-tour-property-identifier-assertion": {
      write: true,
      authorityOnly: true,
      description: "Authority-only append of one rights-bound canonical property identifier assertion. Tenant and authority are server-derived. This does not create a property, choose an identity, merge lineage, or publish anything.",
      inputSchema: schema({ ...idempotencyProperty,
        property_id: { type: "string" }, identifier_scheme: { type: "string" }, identifier_value: { type: "string" }, normalized_identifier: { type: "string" },
        source_evidence_id: { type: "string" }, rights_receipt_id: { type: "string" }, observed_at: { type: "string" },
        confidence: { type: "string" }, review_state: { type: "string" }, assertion_digest: { type: "string" },
      }, ["idempotency_key", "property_id", "identifier_scheme", "identifier_value", "normalized_identifier", "source_evidence_id", "rights_receipt_id", "observed_at", "confidence", "review_state", "assertion_digest"]),
      handler: async (c, actor, args) => {
        exactFields(args, IDENTIFIER_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const normalized = text(args.normalized_identifier, "normalized_identifier", ToolError);
        if (normalized !== normalized.toLowerCase()) fail(ToolError, { error: "tour_identifier_not_normalized" });
        const payload = {
          organization_tenant_id: tenantFor(actor, ToolError),
          property_id: uuid(args.property_id, "property_id", ToolError),
          identifier_scheme: oneOf(args.identifier_scheme, "identifier_scheme", IDENTIFIER_SCHEMES, ToolError),
          identifier_value: text(args.identifier_value, "identifier_value", ToolError),
          normalized_identifier: normalized,
          source_evidence_id: uuid(args.source_evidence_id, "source_evidence_id", ToolError),
          rights_receipt_id: uuid(args.rights_receipt_id, "rights_receipt_id", ToolError),
          observed_at: timestamp(args.observed_at, "observed_at", ToolError),
          confidence: oneOf(args.confidence, "confidence", CONFIDENCE, ToolError),
          review_state: oneOf(args.review_state, "review_state", ASSERTION_REVIEW, ToolError),
          assertion_digest: digest(args.assertion_digest, "assertion_digest", ToolError),
        };
        return withEnvelope(c, actor, "append-tour-property-identifier-assertion", args, async () => {
          const result = await c.query(
            "select ops.append_tour_property_identifier_assertion($1::jsonb) as property_identifier_assertion_id /* tour-slice3:identifier */",
            [JSON.stringify(payload)]);
          const id = result.rows[0]?.property_identifier_assertion_id;
          if (!id) fail(ToolError, { error: "tour_write_refused", entity: "property_identifier_assertion" });
          await writeEvent(c, actor, "append-tour-property-identifier-assertion", "tour_property_identifier_assertion", id, {
            field: payload.identifier_scheme,
            new: { property_id: payload.property_id, source_evidence_id: payload.source_evidence_id, rights_receipt_id: payload.rights_receipt_id, review_state: payload.review_state },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, property_identifier_assertion_id: id };
        });
      },
    },

    "register-tour-property": {
      write: true,
      authorityOnly: true,
      description: "Authority-only registration of one canonical Tour property together with its first rights-bound identifier assertion (0565). Tenant and authority are server-derived. Refuses a duplicate live identifier in the tenant. This does not choose an identity among aliases, merge lineage, assert an address or coordinate, put the property on a Tour, or publish anything.",
      inputSchema: schema({ ...idempotencyProperty,
        identifier_scheme: { type: "string" }, identifier_value: { type: "string" }, normalized_identifier: { type: "string" },
        source_evidence_id: { type: "string" }, rights_receipt_id: { type: "string" }, observed_at: { type: "string" },
        confidence: { type: "string" }, review_state: { type: "string" }, assertion_digest: { type: "string" },
      }, ["idempotency_key", "identifier_scheme", "identifier_value", "normalized_identifier", "source_evidence_id", "rights_receipt_id", "observed_at", "confidence", "review_state", "assertion_digest"]),
      handler: async (c, actor, args) => {
        exactFields(args, REGISTRATION_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const normalized = text(args.normalized_identifier, "normalized_identifier", ToolError);
        if (normalized !== normalized.toLowerCase()) fail(ToolError, { error: "tour_identifier_not_normalized" });
        const payload = {
          organization_tenant_id: tenantFor(actor, ToolError),
          identifier_scheme: oneOf(args.identifier_scheme, "identifier_scheme", IDENTIFIER_SCHEMES, ToolError),
          identifier_value: text(args.identifier_value, "identifier_value", ToolError),
          normalized_identifier: normalized,
          source_evidence_id: uuid(args.source_evidence_id, "source_evidence_id", ToolError),
          rights_receipt_id: uuid(args.rights_receipt_id, "rights_receipt_id", ToolError),
          observed_at: timestamp(args.observed_at, "observed_at", ToolError),
          confidence: oneOf(args.confidence, "confidence", CONFIDENCE, ToolError),
          review_state: oneOf(args.review_state, "review_state", ASSERTION_REVIEW, ToolError),
          assertion_digest: digest(args.assertion_digest, "assertion_digest", ToolError),
        };
        return withEnvelope(c, actor, "register-tour-property", args, async () => {
          const result = await c.query(
            "select ops.register_tour_property($1::jsonb) as registration /* tour-registration:0565 */",
            [JSON.stringify(payload)]);
          const registration = result.rows[0]?.registration;
          const propertyId = registration?.property_id;
          const assertionId = registration?.property_identifier_assertion_id;
          if (!UUID.test(propertyId || "") || !UUID.test(assertionId || ""))
            fail(ToolError, { error: "tour_write_refused", entity: "tour_property" });
          await writeEvent(c, actor, "register-tour-property", "tour_property", propertyId, {
            field: payload.identifier_scheme,
            new: { property_identifier_assertion_id: assertionId, source_evidence_id: payload.source_evidence_id, rights_receipt_id: payload.rights_receipt_id, review_state: payload.review_state },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, property_id: propertyId, property_identifier_assertion_id: assertionId };
        });
      },
    },

    "append-tour-coordinate-candidate": {
      write: true,
      description: "Append one rights-bound, unpromoted coordinate candidate. Provider coordinates are confined to non-canonical candidate roles. This is not a map, navigation, public, or client-promotion approval.",
      inputSchema: schema({ ...idempotencyProperty,
        property_id: { type: "string" }, coordinate_role: { type: "string" }, latitude: { type: "number" }, longitude: { type: "number" },
        precision_class: { type: "string" }, source_evidence_id: { type: "string" }, rights_receipt_id: { type: "string" },
        provider: { type: ["string", "null"] }, observed_at: { type: "string" }, review_state: { type: "string" }, access_notes: { type: ["string", "null"] },
      }, ["idempotency_key", "property_id", "coordinate_role", "latitude", "longitude", "precision_class", "source_evidence_id", "rights_receipt_id", "provider", "observed_at", "review_state", "access_notes"]),
      handler: async (c, actor, args) => {
        exactFields(args, COORDINATE_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const coordinateRole = oneOf(args.coordinate_role, "coordinate_role", COORDINATE_ROLES, ToolError);
        const provider = text(args.provider, "provider", ToolError, true);
        if (provider && !PROVIDER_ROLES.has(coordinateRole))
          fail(ToolError, { error: "tour_provider_coordinate_role_invalid", coordinate_role: coordinateRole });
        const payload = {
          organization_tenant_id: tenantFor(actor, ToolError),
          property_id: uuid(args.property_id, "property_id", ToolError),
          coordinate_role: coordinateRole,
          latitude: boundedNumber(args.latitude, "latitude", -90, 90, ToolError),
          longitude: boundedNumber(args.longitude, "longitude", -180, 180, ToolError),
          precision_class: oneOf(args.precision_class, "precision_class", PRECISION_CLASSES, ToolError),
          source_evidence_id: uuid(args.source_evidence_id, "source_evidence_id", ToolError),
          rights_receipt_id: uuid(args.rights_receipt_id, "rights_receipt_id", ToolError),
          provider: provider || "",
          observed_at: timestamp(args.observed_at, "observed_at", ToolError),
          review_state: oneOf(args.review_state, "review_state", COORDINATE_REVIEW, ToolError),
          access_notes: text(args.access_notes, "access_notes", ToolError, true) || "",
        };
        return withEnvelope(c, actor, "append-tour-coordinate-candidate", args, async () => {
          const result = await c.query(
            "select ops.append_tour_coordinate_candidate($1::jsonb) as coordinate_candidate_id /* tour-slice3:coordinate */",
            [JSON.stringify(payload)]);
          const id = result.rows[0]?.coordinate_candidate_id;
          if (!id) fail(ToolError, { error: "tour_write_refused", entity: "coordinate_candidate" });
          await writeEvent(c, actor, "append-tour-coordinate-candidate", "tour_property_coordinate_candidate", id, {
            field: "candidate", new: { property_id: payload.property_id, coordinate_role: payload.coordinate_role, precision_class: payload.precision_class, review_state: payload.review_state },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, coordinate_candidate_id: id };
        });
      },
    },

    "append-tour-entrance-verification-receipt": {
      write: true,
      authorityOnly: true,
      description: "Authority-only append of one immutable human entrance-verification receipt. The database requires a reviewed entrance, driveway, or parking-access candidate. This is not route, map, publication, or client-promotion authorization.",
      inputSchema: schema({ ...idempotencyProperty,
        property_id: { type: "string" }, coordinate_candidate_id: { type: "string" }, verified_at: { type: "string" },
        evidence_reference: { type: "string" }, native_navigation_proof: { type: "object" }, receipt_digest: { type: "string" },
      }, ["idempotency_key", "property_id", "coordinate_candidate_id", "verified_at", "evidence_reference", "native_navigation_proof", "receipt_digest"]),
      handler: async (c, actor, args) => {
        exactFields(args, VERIFICATION_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const payload = {
          organization_tenant_id: tenantFor(actor, ToolError),
          property_id: uuid(args.property_id, "property_id", ToolError),
          coordinate_candidate_id: uuid(args.coordinate_candidate_id, "coordinate_candidate_id", ToolError),
          verifier_actor_id: verifiedHumanActorId(actor, ToolError),
          verified_at: timestamp(args.verified_at, "verified_at", ToolError),
          evidence_reference: text(args.evidence_reference, "evidence_reference", ToolError),
          native_navigation_proof: nativeNavigationProof(args.native_navigation_proof, ToolError),
          receipt_digest: digest(args.receipt_digest, "receipt_digest", ToolError),
        };
        return withEnvelope(c, actor, "append-tour-entrance-verification-receipt", args, async () => {
          const result = await c.query(
            "select ops.append_tour_entrance_verification_receipt($1::jsonb) as verification_receipt_id /* tour-slice3:entrance-receipt */",
            [JSON.stringify(payload)]);
          const id = result.rows[0]?.verification_receipt_id;
          if (!id) fail(ToolError, { error: "tour_write_refused", entity: "entrance_verification_receipt" });
          await writeEvent(c, actor, "append-tour-entrance-verification-receipt", "tour_coordinate_entrance_verification_receipt", id, {
            field: "verification", new: { property_id: payload.property_id, coordinate_candidate_id: payload.coordinate_candidate_id, verified_at: payload.verified_at },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, verification_receipt_id: id };
        });
      },
    },
  };
}
