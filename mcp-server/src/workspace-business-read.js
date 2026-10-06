import { networkStatement, projectNetwork } from './relationship-network.js';
import { vendorRelationshipJoin, enrichRelationship } from "./vendor-relationship.js";
// Journey 1 business workspace: the Clients and Vendors READ model.
//
// ONE SOURCE FOR COUNT, LIST AND FILTER SEMANTICS. Every list answer — the
// rows, the truthful total, and the page window — comes out of a SINGLE
// statement whose `filtered` CTE is written once per dataset. The total is
// `count(*) from filtered`, not a second query against a second predicate, so
// "12 clients" and the twelve rows below it can never disagree, and a page past
// the end still reports the true total instead of collapsing to zero.
//
// WHAT THIS MODULE REFUSES TO DO:
//   * It never writes. There is no insert, update, delete or verb dispatch here.
//   * It never treats a recorded field as a lifecycle fact. `client.status` and
//     `client.etl_status` are published as RECORDED VALUES with recorded labels;
//     nothing in this file promotes them to a v5 typed transition, an accepted
//     representation, or an assignment.
//   * It never invents a value. A null column is published as null, and the
//     browser renders "Not recorded". A recorded code with no row in its lookup
//     table is published with a null label and counted into a PARTIAL signal —
//     it is not silently relabelled or dropped.
//   * It never accepts an owner from the caller. "My work" is bound to the
//     authenticated actor's own UUID, resolved server-side from the session
//     slug through the actor table; the wire has no owner parameter at all.
//     `owner_label` travels as recorded text for display and is never the thing
//     the filter runs on.
//   * Merged and deleted records do not appear: merged business rows
//     (client.merged_into / vendor.merged_into), merged parties and deleted
//     parties are excluded from the list, the total AND the record read, so a
//     tombstone is a 404 rather than a quietly different record.
//
// Tenant and audience are the same boundary the Command Center read uses:
// carr-internal, and one of the two verified partner actors.

import { neon } from "@neondatabase/serverless";
import { organizationTenantForActor } from "./identity.js";

export const BUSINESS_API_PREFIX = "/api/v1/business/";
export const CLIENTS_ROUTE = "/clients";
export const VENDORS_ROUTE = "/vendors";
export const BUSINESS_ASSET_PATH = "/business.html";
export const DATASETS = ["clients", "vendors"];
export const SCOPES = ["team", "mine"];
export const DEFAULT_SCOPE = "team";
export const SORTS = ["name", "recent", "vertical", "deal_type", "last_deal_desc", "last_deal_asc", "territory"];
export const DEFAULT_SORT = "name";
export const PIPELINE_FILTERS = ["any", "active", "other", "unknown"];
export const PAGE_SIZE = 25;
export const MAX_PAGE = 200;
export const MAX_QUERY_LENGTH = 80;
export const FRESHNESS_WINDOW_MS = 60_000;

const VALID_ACTORS = new Set(["joe", "dell"]);
const DEPENDENCY_CODES = new Set(["DEPENDENCY_UNAVAILABLE", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "08000", "57P01"]);
// A MISSING GRANT IS NOT A BUG IN THIS CODE, AND MUST NOT READ LIKE ONE. This
// module reads base tables through a reader role documented as views-only, so
// "the role cannot see this" and "the object is not there" are the two most
// likely first-deploy failures. They get their own answer, naming the CLASS of
// gap and nothing else: no SQL, no statement text, no table or column name, no
// driver message. INTERNAL_ERROR stays for genuine surprises.
const PROVISIONING_CODES = new Map([
  ["42501", "read_access"],       // insufficient_privilege
  ["42P01", "read_source"],       // undefined_table
  ["42703", "read_source"],       // undefined_column
  ["3F000", "read_source"],       // invalid_schema_name
  ["3D000", "read_source"],       // invalid_catalog_name
  ["28000", "read_credential"],   // invalid_authorization_specification
  ["28P01", "read_credential"],   // invalid_password
]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// Lookup slugs are bounded defensively. Values are parameterized regardless;
// this only keeps a nonsense filter from reaching the database at all.
const SLUG = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const PAGE = /^[1-9][0-9]{0,3}$/;

const LIST_KEYS = {
  clients: ["contract", "viewer", "scope", "q", "status", "type", "pipeline", "sort", "page", "owner"],
  vendors: ["contract", "viewer", "scope", "q", "category", "stage", "disposition", "sort", "page", "owner", "territory"],
};
const RECORD_KEYS = ["viewer", "contract"];

export function businessError(code, detail = null) {
  const error = new Error(code);
  error.code = code;
  if (detail) error.detail = detail;
  return error;
}

function classifyReadError(error) {
  const provisioning = PROVISIONING_CODES.get(error?.code);
  // The class travels; the driver's message deliberately does not.
  if (provisioning) return businessError("DEPENDENCY_NOT_PROVISIONED", { dependency: provisioning });
  if (DEPENDENCY_CODES.has(error?.code)) return businessError("DEPENDENCY_UNAVAILABLE");
  if (error?.code === "INTERNAL_ERROR") return error;
  return businessError("INTERNAL_ERROR");
}

/** `/api/v1/business/clients` and `/api/v1/business/clients/<uuid>` and nothing else. */
export function parseBusinessApiPath(pathname) {
  if (typeof pathname !== "string" || !pathname.startsWith(BUSINESS_API_PREFIX)) return null;
  const parts = pathname.slice(BUSINESS_API_PREFIX.length).split("/");
  if (parts.length === 1 && parts[0] === "leases") return { dataset: "leases", id: null };
  if (parts.length > 2 || !DATASETS.includes(parts[0])) return null;
  if (parts.length === 1) return { dataset: parts[0], id: null };
  return UUID.test(parts[1]) ? { dataset: parts[0], id: parts[1].toLowerCase() } : null;
}

export function isBusinessApiPath(pathname) {
  return pathname === "/api/v1/business/relationships" || parseBusinessApiPath(pathname) !== null;
}

function requireExactKeys(searchParams, allowed, viewerSlug) {
  for (const key of searchParams.keys()) {
    if (!allowed.includes(key)) throw businessError("QUERY_INVALID", { parameter: key, reason: "unsupported" });
  }
  // `viewer` may be echoed by a bookmark, but it is a label, never a selector:
  // it must equal the authenticated actor or the read refuses outright.
  if (searchParams.has("viewer") && searchParams.get("viewer") !== viewerSlug) {
    throw businessError("AUTHORIZATION_REFUSED", { parameter: "viewer" });
  }
}

function oneOf(searchParams, key, allowed, fallback) {
  const raw = searchParams.get(key);
  if (raw === null || raw === "") return fallback;
  if (!allowed.includes(raw)) throw businessError("QUERY_INVALID", { parameter: key, reason: "unsupported_value" });
  return raw;
}

function slugFilter(searchParams, key) {
  const raw = searchParams.get(key);
  if (raw === null || raw === "" || raw === "any") return null;
  if (!SLUG.test(raw)) throw businessError("QUERY_INVALID", { parameter: key, reason: "malformed" });
  return raw;
}

/**
 * Control characters never belong in a search box and are the shape most likely
 * to be a paste accident or a probe rather than a client's name. Written as a
 * code-point scan rather than a character class so no control byte has to
 * appear in this source file to describe one.
 */
export function hasControlCharacter(value) {
  const text = String(value);
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

function searchTerm(searchParams) {
  const raw = searchParams.get("q");
  if (raw === null) return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (trimmed.length > MAX_QUERY_LENGTH) throw businessError("QUERY_INVALID", { parameter: "q", reason: "too_long", limit: MAX_QUERY_LENGTH });
  if (hasControlCharacter(trimmed)) throw businessError("QUERY_INVALID", { parameter: "q", reason: "malformed" });
  return trimmed;
}

function pageNumber(searchParams) {
  const raw = searchParams.get("page");
  if (raw === null || raw === "") return 1;
  if (!PAGE.test(raw)) throw businessError("QUERY_INVALID", { parameter: "page", reason: "malformed" });
  const value = Number(raw);
  if (value > MAX_PAGE) throw businessError("QUERY_INVALID", { parameter: "page", reason: "out_of_bounds", limit: MAX_PAGE });
  return value;
}

/** A literal ILIKE term: the reader's `%`, `_` and `\` are data, not wildcards. */
export function likeTerm(value) {
  return `%${String(value).replace(/([\\%_])/g, "\\$1")}%`;
}

/**
 * The ONE parser. Both the list statement and the total it reports are built
 * from this normalized query, and the payload echoes it back so the browser can
 * render exactly the filter set the server counted.
 */
function boundedTerritory(params) {
  const value = params.get("territory") || "";
  if (value.length > 80 || hasControlCharacter(value)) throw businessError("QUERY_INVALID", { parameter: "territory" });
  return value || null;
}

export function parseBusinessQuery(dataset, searchParams, viewerSlug) {
  if (!DATASETS.includes(dataset)) throw businessError("QUERY_INVALID", { parameter: "dataset" });
  const params = searchParams instanceof URLSearchParams ? searchParams : new URLSearchParams(searchParams || "");
  requireExactKeys(params, LIST_KEYS[dataset], viewerSlug);
  const base = {
    dataset,
    ...(oneOf(params,"contract",["vendor-directory.v1"],null) ? {contract:"vendor-directory.v1"} : {}),
    scope: oneOf(params, "scope", SCOPES, DEFAULT_SCOPE),
    q: searchTerm(params),
    owner: oneOf(params, "owner", ["all", "joe", "dell"], "all"),
    territory: boundedTerritory(params),
    sort: oneOf(params, "sort", dataset === "clients" ? SORTS.filter(sort => sort !== "territory") : SORTS, DEFAULT_SORT),
    page: pageNumber(params),
    page_size: PAGE_SIZE,
  };
  return dataset === "clients"
    ? { ...base, status: slugFilter(params, "status"), type: slugFilter(params, "type"), pipeline: oneOf(params, "pipeline", PIPELINE_FILTERS, "any") }
    : { ...base, category: slugFilter(params, "category"), stage: slugFilter(params, "stage"), disposition: slugFilter(params, "disposition") };
}

export function parseBusinessRecordQuery(searchParams, viewerSlug) {
  const params = searchParams instanceof URLSearchParams ? searchParams : new URLSearchParams(searchParams || "");
  requireExactKeys(params, RECORD_KEYS, viewerSlug);
  const contract=oneOf(params,"contract",["vendor-directory.v1"],null);
  return contract ? {contract} : {};
}

// --------------------------------------------------------------- statements
//
// `$1` is ALWAYS the authenticated actor slug. Nothing else in this module is
// allowed to occupy that slot, which is what makes the owner subquery below
// impossible to point at another partner.
//
// EVERY BASE RELATION IS WRITTEN `public.<table>`, HERE AND IN THE FACET AND
// OWNER QUERIES. Not for search_path safety alone: the repository's own grant
// audit (tools/test-handler-reads-are-granted.py) recognises relations only by
// their qualified name, so a bare `from client` reads to it as no relation at
// all — the read would look grant-clean while depending on eight tables nobody
// had checked. Qualifying them is what lets that audit see this module's real
// dependency list. It changes no result: these are the same relations, named in
// full. Parameterisation is untouched; nothing below interpolates a value.

const VIEWER_OWNER = "(select a.id from public.actor a where a.slug = $1::text)";

const ORDER = {
  name: "lower(f.name) asc, f.id asc",
  recent: "f.updated_at desc, f.id asc",
  vertical: "lower(f.vertical) asc nulls last, lower(f.name), f.id",
  deal_type: "lower(f.deal_type) asc nulls last, lower(f.name), f.id",
  last_deal_desc: "f.last_deal_at desc nulls last, lower(f.name), f.id",
  last_deal_asc: "f.last_deal_at asc nulls last, lower(f.name), f.id",
  territory: "lower(f.territory) asc nulls last, lower(f.name), f.id",
};

/** Append a filter value and return its placeholder; `$1` stays the actor slug. */
function bind(values, value) {
  values.push(value);
  return `$${values.length + 1}`;
}

/**
 * Rows, the truthful total and the page window from one predicate. `filtered`
 * is evaluated once; the count reads it whole and `ordered` reads its page, so
 * an out-of-range page still reports the real total rather than looking empty.
 */
function pageStatement({ columns, from, where, values, sort, page }) {
  const limitIndex = values.length + 1;
  const text = `with filtered as (
    select ${columns}
      from ${from}
     where ${where.join("\n       and ")}
  ), ordered as (
    select f.*, row_number() over (order by ${ORDER[sort]}) as rn
      from filtered f
     order by ${ORDER[sort]}
     limit $${limitIndex} offset $${limitIndex + 1}
  )
  select (select count(*) from filtered)::int as total_count,
         ${VIEWER_OWNER} is not null as viewer_owner_resolved,
         coalesce((select json_agg(o.row_payload order by o.rn) from ordered o), '[]'::json) as rows`;
  return { text, values: [...values, PAGE_SIZE, (page - 1) * PAGE_SIZE] };
}

function recordStatement({ columns, from, where, values }) {
  return {
    text: `with filtered as (
    select ${columns}
      from ${from}
     where ${where.join("\n       and ")}
     limit 2
  )
  select (select count(*) from filtered)::int as match_count,
         ${VIEWER_OWNER} is not null as viewer_owner_resolved,
         (select f.row_payload from filtered f limit 1) as record`,
    values,
  };
}

// ----------------------------------------------------------------- clients

const CLIENT_FROM = `public.client c
       join public.party p on p.id = c.party_id
       left join public.client_status cs on cs.slug = c.status
       left join public.client_type ct on ct.slug = c.client_type`;

// Merged business record, merged party and deleted party are all excluded, in
// the list, the total and the single-record read alike.
const CLIENT_LIVE = ["c.merged_into is null", "p.merged_into is null", "p.deleted_at is null"];

const CLIENT_LIST_PAYLOAD = `json_build_object(
      'id', c.id,
      'ref', c.roster_ref,
      'name', p.name,
      'party_kind', p.kind,
      'city', p.city,
      'state', p.state,
      'recorded_status', c.status,
      'recorded_status_label', cs.label,
      'recorded_status_active_pipeline', cs.is_active_pipeline,
      'recorded_etl_status', c.etl_status,
      'recorded_client_type', c.client_type,
      'recorded_client_type_label', ct.label,
      'vertical', c.vertical,
      'last_deal_at', (select max(d.closed_on) from public.deal d where d.client_id=c.id),
      'deal_type', c.deal_type_label,
      'owner_label', c.owner_label,
      'owned_by_viewer', (c.owner_id is not null and c.owner_id = ${VIEWER_OWNER}),
      'updated_at', c.updated_at
    )`;

const CLIENT_RECORD_PAYLOAD = `json_build_object(
      'id', c.id,
      'ref', c.roster_ref,
      'name', p.name,
      'party_kind', p.kind,
      'city', p.city,
      'state', p.state,
      'county', p.county,
      'title', p.title,
      'specialty', p.specialty,
      'npi', p.npi,
      'phone', p.phone,
      'cell', p.cell,
      'email', p.email,
      'contact_state', p.contact_state,
      'contact_state_reason', p.contact_state_reason,
      'contact_state_until', p.contact_state_until,
      'contact_state_cadence', p.contact_state_cadence,
      'recorded_status', c.status,
      'recorded_status_label', cs.label,
      'recorded_status_active_pipeline', cs.is_active_pipeline,
      'recorded_status_note', cs.note,
      'recorded_etl_status', c.etl_status,
      'recorded_client_type', c.client_type,
      'recorded_client_type_label', ct.label,
      'vertical', c.vertical,
      'last_deal_at', (select max(d.closed_on) from public.deal d where d.client_id=c.id),
      'deal_type', c.deal_type_label,
      'subtype', c.subtype,
      'acquisition_source', c.acquisition_source,
      'acquisition_detail', c.acquisition_detail,
      'contact_label', c.contact_label,
      'deal_type_label', c.deal_type_label,
      'specialty_type_label', c.specialty_type_label,
      'possible_duplicate_label', c.possible_duplicate_label,
      'notes', c.notes,
      'owner_label', c.owner_label,
      'owned_by_viewer', (c.owner_id is not null and c.owner_id = ${VIEWER_OWNER}),
      'record_version', c.version,
      'created_at', c.created_at,
      'updated_at', c.updated_at
    )`;

function clientPredicates(query) {
  const where = [...CLIENT_LIVE];
  const values = [];
  if (query.scope === "mine") where.push(`c.owner_id = ${VIEWER_OWNER}`);
  if (query.owner && query.owner !== "all") where.push(`c.owner_id = (select a.id from public.actor a where a.slug = ${bind(values, query.owner)}::text)`);
  if (query.q) {
    const term = bind(values, likeTerm(query.q));
    where.push(`(p.name ilike ${term} escape '\\' or c.roster_ref ilike ${term} escape '\\' or p.ref ilike ${term} escape '\\')`);
  }
  if (query.status) where.push(`c.status = ${bind(values, query.status)}::text`);
  if (query.type) where.push(`c.client_type = ${bind(values, query.type)}::text`);
  if (query.pipeline === "active") where.push("cs.is_active_pipeline = true");
  if (query.pipeline === "other") where.push("cs.is_active_pipeline = false");
  // "unknown" is the honest third state: a recorded status with no lookup row,
  // or no recorded status at all. It is never folded into "not active".
  if (query.pipeline === "unknown") where.push("cs.is_active_pipeline is null");
  return { where, values };
}

// ----------------------------------------------------------------- vendors

// The vendor category lookup is keyed by slug; the vendor row carries both a
// legacy `category` and a normalized `category_slug`. Resolving through
// coalesce() reads either shape, and an unmatched value simply produces a null
// label that the partial signal below reports rather than hides.
const VENDOR_CATEGORY_KEY = "coalesce(v.category_slug, v.category)";
const VENDOR_FROM = `public.vendor v
       join public.party p on p.id = v.party_id
       left join public.vendor_category vc on vc.slug = ${VENDOR_CATEGORY_KEY}
       left join public.vendor_stage vs on vs.slug = v.stage
       left join public.vendor_disposition vd on vd.slug = v.disposition
       left join public.vendor_relationship_level vrl on vrl.level = v.relationship_level
       ${vendorRelationshipJoin}`;

const VENDOR_LIVE = ["v.merged_into is null", "p.merged_into is null", "p.deleted_at is null"];

const VENDOR_LIST_PAYLOAD = `json_build_object(
      'id', v.id,
      'ref', v.vendor_ref,
      'name', p.name,
      'party_kind', p.kind,
      'city', p.city,
      'state', p.state,
      'recorded_category', ${VENDOR_CATEGORY_KEY},
      'recorded_category_label', vc.label,
      'recorded_stage', v.stage,
      'recorded_stage_label', vs.label,
      'recorded_disposition', v.disposition,
      'recorded_disposition_label', vd.label,
      'recorded_disposition_workable', vd.workable,
      'relationship_level', v.relationship_level,
      'relationship_level_label', vrl.label,
      'referral_active', v.referral_active,
      'is_target', v.is_target,
      'out_of_market', v.out_of_market,
      'last_touch', v.last_touch,
      'relationship', vr.payload,
      'vertical', nullif(array_to_string(v.verticals, ', '), ''),
      'deal_type', vc.label,
      'last_deal_at', vr.last_deal_at,
      'territory', v.territory,
      'owner_label', v.owner_label,
      'owned_by_viewer', (v.owner_id is not null and v.owner_id = ${VIEWER_OWNER}),
      'updated_at', v.updated_at
    )`;

const VENDOR_RECORD_PAYLOAD = `json_build_object(
      'id', v.id,
      'ref', v.vendor_ref,
      'name', p.name,
      'party_kind', p.kind,
      'city', p.city,
      'state', p.state,
      'county', p.county,
      'title', p.title,
      'phone', p.phone,
      'cell', p.cell,
      'email', p.email,
      'contact_state', p.contact_state,
      'contact_state_reason', p.contact_state_reason,
      'contact_state_until', p.contact_state_until,
      'contact_state_cadence', p.contact_state_cadence,
      'recorded_category', ${VENDOR_CATEGORY_KEY},
      'recorded_category_label', vc.label,
      'recorded_stage', v.stage,
      'recorded_stage_label', vs.label,
      'recorded_disposition', v.disposition,
      'recorded_disposition_label', vd.label,
      'recorded_disposition_workable', vd.workable,
      'relationship_level', v.relationship_level,
      'relationship_level_label', vrl.label,
      'relationship_level_note', vrl.note,
      'verticals', v.verticals,
      'territory', v.territory,
      'offers', v.offers,
      'seeking', v.seeking,
      'rivalry_group', v.rivalry_group,
      'originated', v.originated,
      'referral_active', v.referral_active,
      'is_target', v.is_target,
      'out_of_market', v.out_of_market,
      'last_touch', v.last_touch,
      'relationship', vr.payload,
      'loan_programs', v.loan_programs,
      'intro_notes', v.intro_notes,
      'links_label', v.links_label,
      'owner_label', v.owner_label,
      'owned_by_viewer', (v.owner_id is not null and v.owner_id = ${VIEWER_OWNER}),
      'record_version', v.version,
      'created_at', v.created_at,
      'updated_at', v.updated_at
    )`;

function vendorPredicates(query) {
  const where = [...VENDOR_LIVE];
  const values = [];
  if (query.scope === "mine") where.push(`v.owner_id = ${VIEWER_OWNER}`);
  if (query.owner && query.owner !== "all") where.push(`v.owner_id = (select a.id from public.actor a where a.slug = ${bind(values, query.owner)}::text)`);
  if (query.territory) where.push(`v.territory = ${bind(values, query.territory)}::text`);
  if (query.q) {
    const term = bind(values, likeTerm(query.q));
    where.push(`(p.name ilike ${term} escape '\\' or v.vendor_ref ilike ${term} escape '\\' or p.ref ilike ${term} escape '\\')`);
  }
  if (query.category) where.push(`${VENDOR_CATEGORY_KEY} = ${bind(values, query.category)}::text`);
  if (query.stage) where.push(`v.stage = ${bind(values, query.stage)}::text`);
  if (query.disposition) where.push(`v.disposition = ${bind(values, query.disposition)}::text`);
  return { where, values };
}

// ------------------------------------------------------------------ facets
//
// Filter options are the recorded lookup rows themselves — never a distinct()
// over the business tables, and never a hand-written list in the browser. A
// status that does not exist in the lookup cannot be offered as a filter.

const CLIENT_FACETS = `select
    coalesce((select json_agg(json_build_object('slug', slug, 'label', label, 'is_active_pipeline', is_active_pipeline) order by sort, slug) from public.client_status), '[]'::json) as statuses,
    coalesce((select json_agg(json_build_object('slug', slug, 'label', label) order by label, slug) from public.client_type), '[]'::json) as types`;

const VENDOR_FACETS = `select
    coalesce((select json_agg(json_build_object('slug', slug, 'label', label) order by sort, slug) from public.vendor_category), '[]'::json) as categories,
    coalesce((select json_agg(json_build_object('slug', slug, 'label', label) order by sort, slug) from public.vendor_stage), '[]'::json) as stages,
    coalesce((select json_agg(json_build_object('slug', slug, 'label', label, 'workable', workable) order by sort, slug) from public.vendor_disposition), '[]'::json) as dispositions,
    coalesce((select json_agg(json_build_object('slug', t.territory, 'label', t.territory) order by t.territory) from (select distinct v.territory from public.vendor v join public.party p on p.id=v.party_id where v.merged_into is null and p.merged_into is null and p.deleted_at is null and nullif(v.territory,'') is not null) t), '[]'::json) as territories`;

// -------------------------------------------------------------- envelopes

// The one status-provenance sentence the surface shows. Plain words, and it
// still says exactly what these fields are NOT: they are entries on a record,
// never proof that an agreement was countersigned or that anyone took the work.
const RECORDED_FIELD_NOTE = {
  clients: "Status and ETL are what someone entered on this record. They are shown as entered, and they are not proof that an agreement was signed or that anyone was put on the work.",
  vendors: "Stage, disposition and relationship level are what someone entered on this record. They are shown as entered, and they are not proof of a commitment either way.",
};

function sourceEnvelope({ dataset, observedAt, validUntil, correlationId }) {
  return {
    source: dataset === "clients" ? "client" : "vendor",
    source_ref: dataset === "clients"
      ? "client+party+client_status+client_type"
      : "vendor+party+vendor_category+vendor_stage+vendor_disposition",
    observed_at: observedAt,
    valid_until: validUntil,
    freshness: "fresh",
    correlation_id: correlationId,
    safe_explanation: "Fresh because this is a no-store request-time canonical database read of live business records; valid for 60 seconds.",
  };
}

function assertAudience(actor, tenant) {
  const boundTenant = organizationTenantForActor(actor);
  if (tenant !== boundTenant || tenant !== "carr-internal") throw businessError("TENANT_SCOPE_REFUSED");
  if (!actor?.slug || !VALID_ACTORS.has(actor.slug)) throw businessError("AUTHORIZATION_REFUSED");
}

function integer(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : null;
}

function rowsOf(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string") return null;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * A recorded code with no lookup label is a real, reachable partial state: the
 * value is shown as recorded, and the reader is told the label could not be
 * resolved instead of being handed a blank that reads as "none".
 */
export function partialSignal(dataset, rows) {
  const pairs = dataset === "clients"
    ? [["recorded_status", "recorded_status_label"], ["recorded_client_type", "recorded_client_type_label"]]
    // relationship_level is a coded field like the rest: an unmatched level is
    // a bare number, and it belongs in the same signal.
    : [["recorded_category", "recorded_category_label"], ["recorded_stage", "recorded_stage_label"],
      ["recorded_disposition", "recorded_disposition_label"], ["relationship_level", "relationship_level_label"]];
  const fields = new Set();
  // COUNTED PER RECORD, NOT PER CELL. One record carrying two unnamed codes is
  // one record; counting cells and saying "records" is the kind of small lie a
  // truthful-count surface cannot afford.
  let count = 0;
  for (const row of rows) {
    let affected = false;
    for (const [value, label] of pairs) {
      const recorded = row?.[value];
      const resolved = row?.[label];
      if (recorded !== null && recorded !== undefined && (resolved === null || resolved === undefined)) {
        fields.add(value);
        affected = true;
      }
    }
    if (affected) count += 1;
  }
  if (count === 0) return null;
  return {
    kind: "unlabelled_recorded_codes",
    count,
    fields: [...fields].sort(),
    note: "Some entries use a code that is not in the current list of options. The stored value is shown exactly as it is, and has not been renamed.",
  };
}

function pageWindow(total, page) {
  const pageCount = total === 0 ? 1 : Math.ceil(total / PAGE_SIZE);
  return { page, page_size: PAGE_SIZE, page_count: pageCount, out_of_range: page > pageCount };
}

/** The list read. One statement for rows+total, one for the recorded filter options. */
export async function readBusinessList({ client, actor, tenant = organizationTenantForActor(actor), query, correlationId, now = () => new Date() }) {
  if (!correlationId || typeof correlationId !== "string") throw businessError("INTERNAL_ERROR");
  assertAudience(actor, tenant);
  const dataset = query?.dataset;
  if (!DATASETS.includes(dataset)) throw businessError("QUERY_INVALID", { parameter: "dataset" });
  if (!SCOPES.includes(query.scope) || !SORTS.includes(query.sort) || !Number.isInteger(query.page) || query.page < 1) {
    throw businessError("QUERY_INVALID", { parameter: "query" });
  }
  const observedAtDate = now();
  const observedAt = observedAtDate.toISOString();
  const validUntil = new Date(observedAtDate.valueOf() + FRESHNESS_WINDOW_MS).toISOString();
  const clients = dataset === "clients";
  const { where, values } = clients ? clientPredicates(query) : vendorPredicates(query);
  const idColumn = clients ? "c.id" : "v.id";
  const updatedColumn = clients ? "c.updated_at" : "v.updated_at";
  const statement = pageStatement({
    columns: `${idColumn} as id, p.name as name, ${updatedColumn} as updated_at,
      ${clients ? "c.vertical" : "nullif(array_to_string(v.verticals, ', '), '')"} as vertical,
      ${clients ? "c.deal_type_label" : "vc.label"} as deal_type,
      ${clients ? "(select max(d.closed_on) from public.deal d where d.client_id=c.id)" : "vr.last_deal_at"} as last_deal_at,
      ${clients ? "null::text" : "v.territory"} as territory, ${clients ? CLIENT_LIST_PAYLOAD : VENDOR_LIST_PAYLOAD} as row_payload`,
    from: clients ? CLIENT_FROM : VENDOR_FROM,
    where,
    values: [actor.slug, ...values],
    sort: query.sort,
    page: query.page,
  });

  let pageResult;
  let facetResult;
  try {
    [pageResult, facetResult] = await Promise.all([
      client.query(statement.text, statement.values),
      client.query(clients ? CLIENT_FACETS : VENDOR_FACETS, []),
    ]);
  } catch (error) {
    throw classifyReadError(error);
  }

  const head = pageResult?.rows?.[0];
  const total = integer(head?.total_count);
  const rows = rowsOf(head?.rows);
  if (total === null || rows === null) throw businessError("FRESHNESS_UNKNOWN");
  // "My work" is only meaningful if the viewer's own owner UUID exists. A
  // missing actor row would silently render an empty personal list, which is
  // indistinguishable from "you own nothing" and is therefore refused.
  if (query.scope === "mine" && head?.viewer_owner_resolved !== true) throw businessError("VIEWER_OWNER_UNKNOWN");
  // The page can never be wider than the page size, and rows that exist can
  // never reach past the total they were counted with. An EMPTY page past the
  // end is exempt on purpose: that is the honest answer to a page number the
  // filtered set no longer reaches, and the total beside it is still true.
  if (rows.length > PAGE_SIZE) throw businessError("FRESHNESS_UNKNOWN");
  if (rows.length > 0 && (query.page - 1) * PAGE_SIZE + rows.length > total) throw businessError("FRESHNESS_UNKNOWN");

  const facets = facetResult?.rows?.[0] || {};
  return {
    viewer: actor.slug,
    dataset,
    query: { ...query },
    total,
    ...pageWindow(total, query.page),
    rows: rows.map(row => directoryProjection(row, query.dataset, query.contract, observedAt)),
    facets: clients
      ? { statuses: rowsOf(facets.statuses) || [], types: rowsOf(facets.types) || [] }
      : { categories: rowsOf(facets.categories) || [], stages: rowsOf(facets.stages) || [], dispositions: rowsOf(facets.dispositions) || [], ...(query.contract ? {territories: rowsOf(facets.territories) || []} : {}) },
    partial: partialSignal(dataset, rows),
    // Stated once, in the read model that produces the fields, so no rendering
    // layer has to remember it.
    recorded_field_note: RECORDED_FIELD_NOTE[dataset],
    source: sourceEnvelope({ dataset, observedAt, validUntil, correlationId }),
  };
}

// Expanded directory fields are opt-in. Existing v1 consumers keep their
// exact payload shape while DoctorCRE pins the expanded contract revision.
function directoryProjection(row, dataset, contract, observedAt) {
  const result = {...row};
  if (contract === 'vendor-directory.v1') {
    if (dataset === 'vendors') result.relationship = enrichRelationship(row.relationship, observedAt);
    return result;
  }
  for (const key of ['relationship','loan_programs','last_deal_at','deal_type',...(dataset==='vendors'?['vertical','territory']:[])]) delete result[key];
  // Territory already belonged to the legacy vendor detail.
  if (dataset==='vendors' && Object.hasOwn(row,'record_version')) result.territory=row.territory;
  return result;
}

/** The single-record read, from the SAME audience predicate as the list. */
export async function readBusinessRecord({ client, actor, tenant = organizationTenantForActor(actor), dataset, id, contract = null, correlationId, now = () => new Date() }) {
  if (!correlationId || typeof correlationId !== "string") throw businessError("INTERNAL_ERROR");
  assertAudience(actor, tenant);
  if (!DATASETS.includes(dataset)) throw businessError("QUERY_INVALID", { parameter: "dataset" });
  if (typeof id !== "string" || !UUID.test(id)) throw businessError("QUERY_INVALID", { parameter: "id" });
  const observedAtDate = now();
  const observedAt = observedAtDate.toISOString();
  const validUntil = new Date(observedAtDate.valueOf() + FRESHNESS_WINDOW_MS).toISOString();
  const clients = dataset === "clients";
  const where = [...(clients ? CLIENT_LIVE : VENDOR_LIVE), `${clients ? "c.id" : "v.id"} = $2::uuid`];
  const statement = recordStatement({
    columns: `${clients ? CLIENT_RECORD_PAYLOAD : VENDOR_RECORD_PAYLOAD} as row_payload`,
    from: clients ? CLIENT_FROM : VENDOR_FROM,
    where,
    values: [actor.slug, id],
  });

  let result;
  try {
    result = await client.query(statement.text, statement.values);
  } catch (error) {
    throw classifyReadError(error);
  }
  const head = result?.rows?.[0];
  const raw = head?.record ?? null;
  const record = typeof raw === "string" ? JSON.parse(raw) : raw;
  // Merged and deleted records are excluded by the predicate above, so a
  // tombstone lands here as an ordinary not-found rather than as a live row.
  if (!record || typeof record !== "object") throw businessError("RECORD_NOT_FOUND");
  return {
    viewer: actor.slug,
    dataset,
    record: directoryProjection(record, dataset, contract, observedAt),
    partial: partialSignal(dataset, [record]),
    // Named explicitly so the panel never implies it is showing a whole
    // relationship. Assignments, negotiations, correspondence and documents are
    // other slices' read models and are not part of this one.
    not_in_this_read: ["assignments", "negotiations", "deals", "email", "documents", "history"],
    recorded_field_note: RECORDED_FIELD_NOTE[dataset],
    source: sourceEnvelope({ dataset, observedAt, validUntil, correlationId }),
  };
}

/**
 * The production adapter. Kept beside the read model so the browser route in
 * dealroom-web.js needs no database knowledge of its own, and so a test can
 * replace the whole reader with an injected function.
 */
export async function readRelationshipNetwork({client,actor,tenant=organizationTenantForActor(actor),params,now=()=>new Date()}) {
  assertAudience(actor,tenant);
  if (params.get('contract') !== 'relationship-network.v1' || params.getAll('contract').length !== 1 || [...params.keys()].some(key => key !== 'contract')) throw businessError('QUERY_INVALID');
  try { const result = await client.query(networkStatement); return projectNetwork(result.rows[0]?.snapshot,now().toISOString()); }
  catch(error) { throw classifyReadError(error); }
}

export function createWorkspaceBusinessReader() {
  return async (env, actor, request, correlationId) => {
    const sql = neon(env.DATABASE_URL_READER);
    const client = { query: async (text, params = []) => ({ rows: await sql.query(text, params) }) };
    const url = new URL(request.url);
    if (url.pathname === '/api/v1/business/relationships') {
      return readRelationshipNetwork({client,actor,params:url.searchParams});
    }
    const route = parseBusinessApiPath(url.pathname);
    if (!route) throw businessError("RECORD_NOT_FOUND");
    const resolved = correlationId || env.CORRELATION_ID;
    if (route.dataset === "leases") {
      requireExactKeys(url.searchParams, [], actor.slug);
      return readLeaseRadar({ client, actor, correlationId: resolved });
    }
    if (route.id) {
      const {contract} = parseBusinessRecordQuery(url.searchParams, actor.slug);
      return readBusinessRecord({ client, actor, dataset: route.dataset, id: route.id, contract, correlationId: resolved });
    }
    const query = parseBusinessQuery(route.dataset, url.searchParams, actor.slug);
    return readBusinessList({ client, actor, query, correlationId: resolved });
  };
}


// The lease projection shares the existing authenticated business HTTP door.
// Dates are recorded facts, never synthesized from term_months or market data.
export const LEASE_RADAR_SQL = `select starts_on::text, ends_on::text, leases
  from public.v_client_lease_radar`;

export async function readLeaseRadar({ client, actor, tenant = organizationTenantForActor(actor), correlationId, now = () => new Date() }) {
  assertAudience(actor, tenant);
  if (!correlationId) throw businessError("INTERNAL_ERROR");
  let head;
  try { head = (await client.query(LEASE_RADAR_SQL, [])).rows?.[0]; }
  catch (error) { throw classifyReadError(error); }
  const leases = rowsOf(head?.leases);
  if (!leases || !/^\d{4}-\d{2}-\d{2}$/.test(head?.starts_on) || !/^\d{4}-\d{2}-\d{2}$/.test(head?.ends_on)) throw businessError("FRESHNESS_UNKNOWN");
  return { schema_version: "lease-radar.v1", actor: actor.slug, observed_at: now().toISOString(),
    window: { starts_on: head.starts_on, ends_on: head.ends_on }, leases };
}
