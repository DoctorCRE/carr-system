// Specialist, facts-only property search and immutable selection-cart seams.
// This module is intentionally internal/authenticated and has no provider,
// map, routing, publication, or client-share authority.

import { organizationTenantForActor } from "./identity.js";

export const TOUR_SEARCH_COUNTIES = Object.freeze([
  "Escambia", "Santa Rosa", "Okaloosa", "Walton", "Bay",
]);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const PROPERTY_REF = /^property:public:[A-Za-z0-9_-]{16,128}$/;
const COUNTY = new Set(TOUR_SEARCH_COUNTIES);
const AVAILABILITY = new Set(["available", "coming_soon", "under_contract", "unknown"]);
const SORT = new Set(["updated_desc", "address_asc", "size_asc", "size_desc"]);
const AUTHORITY_FIELDS = new Set([
  "tenant", "tenant_id", "organization_tenant_id", "actor", "actor_id",
  "identity", "authorization", "authorization_class", "reviewer", "sponsor", "human_slug",
]);
const SEARCH_FIELDS = new Set([
  "query", "counties", "property_types", "min_square_feet", "max_square_feet",
  "availability", "entrance_verified", "public_projection_ready", "photos_available",
  "sort", "cursor", "limit",
]);
const CART_FIELDS = new Set([
  "idempotency_key", "tour_id", "base_selection_version_id", "property_ids",
  "expected_selection_version", "selection_digest",
]);
const READ_CART_FIELDS = new Set(["tour_id"]);

function fail(ToolError, payload) { throw new ToolError(payload); }
function exact(args, allowed, ToolError) {
  if (!args || typeof args !== "object" || Array.isArray(args)) fail(ToolError, { error: "tour_input_invalid", field: "payload" });
  const keys = Object.keys(args);
  const authority = keys.filter(key => AUTHORITY_FIELDS.has(key));
  if (authority.length) fail(ToolError, { error: "caller_authority_field_forbidden", fields: authority });
  const unknown = keys.filter(key => !allowed.has(key));
  const missing = [...allowed].filter(key => !Object.hasOwn(args, key));
  if (unknown.length) fail(ToolError, { error: "tour_input_unknown_field", fields: unknown });
  if (missing.length) fail(ToolError, { error: "tour_input_missing_field", fields: missing });
}
function text(value, field, ToolError, maximum, nullable = false) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || !value.trim() || value.trim().length > maximum || /[\u0000-\u001F\u007F]/.test(value))
    fail(ToolError, { error: "tour_input_invalid", field });
  return value.trim();
}
function uuid(value, field, ToolError, nullable = false) {
  if (nullable && value === null) return null;
  const candidate = text(value, field, ToolError, 64);
  if (!UUID.test(candidate)) fail(ToolError, { error: "tour_input_invalid", field });
  return candidate;
}
function digest(value, field, ToolError) {
  const candidate = text(value, field, ToolError, 80);
  if (!DIGEST.test(candidate)) fail(ToolError, { error: "tour_input_invalid", field });
  return candidate;
}
function tenant(actor, ToolError) {
  if (!actor || typeof actor.id !== "string" || !actor.id.trim()) fail(ToolError, { error: "tour_actor_context_required" });
  const value = organizationTenantForActor(actor);
  if (typeof value !== "string" || !value) fail(ToolError, { error: "tour_tenant_context_required" });
  return value;
}
function booleanOrNull(value, field, ToolError) {
  if (value !== null && typeof value !== "boolean") fail(ToolError, { error: "tour_input_invalid", field });
  return value;
}
function integerOrNull(value, field, ToolError) {
  if (value === null) return null;
  if (!Number.isInteger(value) || value < 0 || value > 100_000_000) fail(ToolError, { error: "tour_input_invalid", field });
  return value;
}
function stringList(value, field, ToolError, { allowed, maximum, slug = false }) {
  if (!Array.isArray(value) || value.length > maximum || new Set(value).size !== value.length)
    fail(ToolError, { error: "tour_input_invalid", field });
  for (const item of value) {
    if (typeof item !== "string" || !item || (allowed && !allowed.has(item)) || (slug && !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(item)))
      fail(ToolError, { error: "tour_input_invalid", field });
  }
  return [...value];
}
function propertyIds(value, ToolError) {
  if (!Array.isArray(value) || value.length > 100 || new Set(value).size !== value.length || value.some(item => !UUID.test(item)))
    fail(ToolError, { error: "tour_selection_invalid" });
  return [...value];
}
function cursor(value, ToolError) {
  if (value === null) return null;
  const candidate = text(value, "cursor", ToolError, 9);
  if (!/^[0-9]{1,9}$/.test(candidate)) fail(ToolError, { error: "tour_input_invalid", field: "cursor" });
  return candidate;
}
function metric(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const allowed = new Set(["value", "unit", "min", "max", "currency", "period", "label"]);
  const output = {};
  for (const [key, item] of Object.entries(value)) {
    if (allowed.has(key) && (item === null || ["string", "number", "boolean"].includes(typeof item))) output[key] = item;
  }
  return Object.keys(output).length ? output : undefined;
}
function projectSearchItem(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !UUID.test(value.property_id || "") || !COUNTY.has(value.county) || value.state !== "FL") return undefined;
  // The SQL search admits only reviewed CARR property/jurisdiction rows. It
  // does not return a source URL, use rights, or a coordinate precision class;
  // disclose those gaps instead of inventing provider authority.
  const output = { property_id: value.property_id, source_label: "CARR reviewed property register",
    rights_status: "unknown", coordinate_precision: "unknown" };
  if (PROPERTY_REF.test(value.property_ref || "")) output.property_ref = value.property_ref;
  for (const key of ["name", "address", "county", "state", "property_type", "availability", "updated_at", "fact_as_of", "caveat"]) {
    if (typeof value[key] === "string") output[key] = value[key];
  }
  for (const key of ["entrance_verified", "public_projection_ready", "photos_available"]) {
    if (typeof value[key] === "boolean") output[key] = value[key];
  }
  if (Number.isInteger(value.photo_count) && value.photo_count >= 0) output.photo_count = value.photo_count;
  for (const key of ["size", "asking_economics"]) {
    const safe = metric(value[key]);
    if (safe) output[key] = safe;
  }
  return output;
}
export function projectTourPropertySearch(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const output = { items: Array.isArray(value.items) ? value.items.map(projectSearchItem).filter(Boolean) : [] };
  if (typeof value.cursor === "string" && /^[0-9]{1,9}$/.test(value.cursor)) output.cursor = value.cursor;
  if (typeof value.has_more === "boolean") output.has_more = value.has_more;
  if (Number.isInteger(value.count) && value.count >= 0) output.count = value.count;
  return output;
}
function projectCart(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !UUID.test(value.tour_id || "")) return null;
  const output = { tour_id: value.tour_id };
  if (UUID.test(value.selection_version_id || "")) output.selection_version_id = value.selection_version_id;
  if (Number.isInteger(value.selection_version) && value.selection_version >= 0) output.selection_version = value.selection_version;
  if (Array.isArray(value.property_ids)) output.property_ids = value.property_ids.filter(item => UUID.test(item)).slice(0, 100);
  if (typeof value.updated_at === "string") output.updated_at = value.updated_at;
  return output;
}
const schema = (properties, required) => ({ type: "object", additionalProperties: false, properties, required });

export function tourPropertySearchTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "search-tour-properties": {
      writerConnection: true,
      description: "Search facts-only property candidates in the initial five Florida counties. Results contain no broker conclusion and do not mutate a Tour.",
      inputSchema: schema({
        query: { type: ["string", "null"], maxLength: 200 },
        counties: { type: "array", uniqueItems: true, maxItems: 5, items: { type: "string", enum: TOUR_SEARCH_COUNTIES } },
        property_types: { type: "array", uniqueItems: true, maxItems: 12, items: { type: "string", pattern: "^[a-z0-9][a-z0-9_-]{0,63}$" } },
        min_square_feet: { type: ["integer", "null"], minimum: 0, maximum: 100000000 },
        max_square_feet: { type: ["integer", "null"], minimum: 0, maximum: 100000000 },
        availability: { type: "array", uniqueItems: true, maxItems: 4, items: { type: "string", enum: [...AVAILABILITY] } },
        entrance_verified: { type: ["boolean", "null"] },
        public_projection_ready: { type: ["boolean", "null"] },
        photos_available: { type: ["boolean", "null"] },
        sort: { type: "string", enum: [...SORT] },
        cursor: { type: ["string", "null"], pattern: "^[0-9]{1,9}$" },
        limit: { type: "integer", minimum: 1, maximum: 100 },
      }, [...SEARCH_FIELDS]),
      handler: async (client, actor, args) => {
        exact(args, SEARCH_FIELDS, ToolError);
        const minimum = integerOrNull(args.min_square_feet, "min_square_feet", ToolError);
        const maximum = integerOrNull(args.max_square_feet, "max_square_feet", ToolError);
        if (minimum !== null && maximum !== null && minimum > maximum) fail(ToolError, { error: "tour_search_range_invalid" });
        const filters = {
          query: args.query === null ? null : text(args.query, "query", ToolError, 200),
          counties: stringList(args.counties, "counties", ToolError, { allowed: COUNTY, maximum: COUNTY.size }),
          property_types: stringList(args.property_types, "property_types", ToolError, { maximum: 12, slug: true }),
          min_square_feet: minimum,
          max_square_feet: maximum,
          availability: stringList(args.availability, "availability", ToolError, { allowed: AVAILABILITY, maximum: AVAILABILITY.size }),
          entrance_verified: booleanOrNull(args.entrance_verified, "entrance_verified", ToolError),
          public_projection_ready: booleanOrNull(args.public_projection_ready, "public_projection_ready", ToolError),
          photos_available: booleanOrNull(args.photos_available, "photos_available", ToolError),
          sort: SORT.has(args.sort) ? args.sort : fail(ToolError, { error: "tour_input_invalid", field: "sort" }),
          cursor: cursor(args.cursor, ToolError),
          limit: Number.isInteger(args.limit) && args.limit >= 1 && args.limit <= 100 ? args.limit : fail(ToolError, { error: "tour_input_invalid", field: "limit" }),
        };
        const result = await client.query(
          "select ops.search_tour_properties($1::text,$2::text,$3::jsonb) as search /* tour-property-search:search */",
          [tenant(actor, ToolError), actor.id, JSON.stringify(filters)],
        );
        const search = projectTourPropertySearch(result.rows[0]?.search);
        if (!search) fail(ToolError, { error: "tour_property_search_unavailable" });
        return { ok: true, search };
      },
    },
    "append-tour-selection-cart-version": {
      write: true,
      description: "Append one immutable internal selection-cart version. This does not create a route, projection, share, or client publication.",
      inputSchema: schema({
        idempotency_key: { type: "string" }, tour_id: { type: "string" }, base_selection_version_id: { type: ["string", "null"] },
        property_ids: { type: "array", uniqueItems: true, maxItems: 100, items: { type: "string" } },
        expected_selection_version: { type: "integer", minimum: 0 }, selection_digest: { type: "string" },
      }, [...CART_FIELDS]),
      handler: async (client, actor, args) => {
        exact(args, CART_FIELDS, ToolError);
        const payload = [
          tenant(actor, ToolError), uuid(args.tour_id, "tour_id", ToolError),
          uuid(args.base_selection_version_id, "base_selection_version_id", ToolError, true),
          JSON.stringify(propertyIds(args.property_ids, ToolError)),
          Number.isInteger(args.expected_selection_version) && args.expected_selection_version >= 0
            ? args.expected_selection_version : fail(ToolError, { error: "tour_input_invalid", field: "expected_selection_version" }),
          digest(args.selection_digest, "selection_digest", ToolError),
        ];
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        return withEnvelope(client, actor, "append-tour-selection-cart-version", args, async () => {
          const result = await client.query(
            "select ops.append_tour_selection_cart_version($1::text,$2::uuid,$3::uuid,$4::jsonb,$5::integer,$6::text) as selection_version_id /* tour-property-search:cart */",
            payload,
          );
          const id = result.rows[0]?.selection_version_id;
          if (!UUID.test(id || "")) fail(ToolError, { error: "tour_write_refused", entity: "selection_cart_version" });
          await writeEvent(client, actor, "append-tour-selection-cart-version", "tour_selection_cart_version", id, {
            field: "selection_version", new: { tour_id: payload[1], selected_count: args.property_ids.length }, idempotency_key: args.idempotency_key,
          });
          return { ok: true, selection_version_id: id, selected_count: args.property_ids.length };
        });
      },
    },
    "read-tour-selection-cart": {
      writerConnection: true,
      description: "Read the latest authenticated internal selection cart for a Tour.",
      inputSchema: schema({ tour_id: { type: "string" } }, [...READ_CART_FIELDS]),
      handler: async (client, actor, args) => {
        exact(args, READ_CART_FIELDS, ToolError);
        const result = await client.query(
          "select ops.read_tour_selection_cart($1::text,$2::uuid,$3::text) as cart /* tour-property-search:cart-read */",
          [tenant(actor, ToolError), uuid(args.tour_id, "tour_id", ToolError), actor.id],
        );
        const cart = projectCart(result.rows[0]?.cart);
        if (!cart) fail(ToolError, { error: "tour_selection_cart_not_found" });
        return { ok: true, cart };
      },
    },
  };
}
