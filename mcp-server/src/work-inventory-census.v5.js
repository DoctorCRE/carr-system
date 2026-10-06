// V5-UX-C10 — Complete Work Inventory source census.
//
// A pure, read-only federation over the SIX canonical work stores that already
// exist. It creates no second work store: every leg below reads the same table
// the owning verb reads (ops.work_request via current-work-requests /
// work-request-card, ops.portfolio_node via read-portfolio, public.loop_item
// via loop-headers / read-loop, ops.work_shape_revision via read-work-shape,
// ops.engineering_slice_plan via register-engineering-slice-plan, and
// ops.rule_admission via governance-queue).
//
// WHY THIS EXISTS, in one line: today's windows onto open work are CAPPED
// ACTIVE QUEUES, so a queued, dormant, superseded, declined or unlinked row is
// invisible rather than merely quiet. This census enumerates EVERY status by
// default and, when a source is down or cannot be enumerated in full, says so
// in `coverage` — an incomplete census is never published as an empty one.
//
// This module is deliberately NOT a script: it has no shebang, reads no CLI
// arguments and carries no main-module guard, so the sealed script-entrypoint
// frontier does not move. The predicate in ops/scac-mutation-inventory.mjs
// matches on PLAIN TEXT, so even naming those constructs in a comment here
// would turn this library into an ingress. That cost one verdict to learn.

import { readSystemWorkCensus } from "./system-work-census.v5.js";
import { organizationTenantForActor } from "./identity.js";

export const WORK_INVENTORY_PATH = "/api/v1/work-inventory";

export const WORK_INVENTORY_KINDS = [
  "work_request", "portfolio_node", "loop", "work_shape", "slice_plan", "governance_item",
];

export const WORK_INVENTORY_LIMIT_DEFAULT = 100;
export const WORK_INVENTORY_LIMIT_MAX = 500;

const VALID_ACTORS = new Set(["joe", "dell"]);
const TENANT = "carr-internal";
const DEPENDENCY_CODES = new Set([
  "DEPENDENCY_UNAVAILABLE", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "08000", "57P01",
  // 42501 insufficient_privilege: a missing grant is a DEPENDENCY the reader
  // role does not have, not a bug in this module. Classing it INTERNAL_ERROR
  // would hide a grant gap behind the code reserved for our own defects.
  "42501",
]);

// The only deep link admitted here. /system-work.html is an EXISTING surface
// (see NEEDS_JOE_DESTINATION in workspace-command-center.js); no other kind has
// a route in this codebase today, so every other kind reports a null `open`
// rather than inventing one.
const SYSTEM_WORK_DESTINATION = "/system-work.html";

function typedError(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function classifyReadError(error) {
  if (DEPENDENCY_CODES.has(error?.code)) return "DEPENDENCY_UNAVAILABLE";
  return "INTERNAL_ERROR";
}

/**
 * The six legs. Each carries its OWN select and its OWN count select against
 * the same canonical relation the owning verb reads. Read-only by construction:
 * assertReadOnly() below refuses anything but a select, and the tests assert it.
 */
export const WORK_INVENTORY_LEGS = [
  {
    kind: "work_request",
    sourceRef: "ops.work_request",
    // Parameter order per leg: only the parameters that leg's SQL actually binds.
    params: ({ tenant, statuses, cursorAt, fetch }) => [tenant, statuses, cursorAt, fetch],
    countParams: ({ tenant, statuses }) => [tenant, statuses],
    open: () => SYSTEM_WORK_DESTINATION,
    rowsSql: `select 'work_request' as kind, ref as id, version::text as version, title,
                     state as status, updated_at, organization_tenant_id,
                     superseded_by::text as related_work_request, origin_ref as related_origin
                from ops.work_request
               where ($1::text is null or organization_tenant_id = $1::text)
                 and ($2::text[] is null or state = any($2::text[]))
                 and ($3::timestamptz is null or updated_at <= $3::timestamptz)
               order by updated_at desc, ref asc
               limit $4::int`,
    countSql: `select count(*) as count from ops.work_request
                where ($1::text is null or organization_tenant_id = $1::text)
                  and ($2::text[] is null or state = any($2::text[]))`,
    related: (row) => [
      ...(row.related_work_request ? [{ kind: "work_request", id: row.related_work_request }] : []),
      ...(row.related_origin ? [{ kind: "doctrine_section", id: row.related_origin }] : []),
    ],
  },
  {
    kind: "portfolio_node",
    sourceRef: "ops.portfolio_node",
    params: ({ statuses, cursorAt, fetch }) => [statuses, cursorAt, fetch],
    countParams: ({ statuses }) => [statuses],
    open: () => null,
    // portfolio_node has no status column of its own; node_kind IS the source's
    // own classification string and is passed through unmapped. created_at is
    // the row's only time, so it doubles as updated_at (the table is append-only).
    rowsSql: `select 'portfolio_node' as kind, n.node_ref as id, r.revision_version::text as version,
                     n.node_ref as title, n.node_kind as status, n.created_at as updated_at,
                     null::text as organization_tenant_id, n.parent_ref as related_parent,
                     n.child_ref as related_child
                from ops.portfolio_node n
                join ops.portfolio_revision r on r.id = n.portfolio_revision_id
               where ($1::text[] is null or n.node_kind = any($1::text[]))
                 and ($2::timestamptz is null or n.created_at <= $2::timestamptz)
               order by n.created_at desc, n.node_ref asc
               limit $3::int`,
    countSql: `select count(*) as count from ops.portfolio_node n
                where ($1::text[] is null or n.node_kind = any($1::text[]))`,
    related: (row) => [
      ...(row.related_parent ? [{ kind: "portfolio_node", id: row.related_parent }] : []),
      ...(row.related_child ? [{ kind: "portfolio_node", id: row.related_child }] : []),
    ],
  },
  {
    kind: "loop",
    sourceRef: "public.loop_item",
    params: ({ statuses, cursorAt, fetch }) => [statuses, cursorAt, fetch],
    countParams: ({ statuses }) => [statuses],
    open: () => null,
    rowsSql: `select 'loop' as kind, number as id, version::text as version, title,
                     status, updated_at, null::text as organization_tenant_id,
                     domain as related_domain
                from loop_item
               where ($1::text[] is null or status = any($1::text[]))
                 and ($2::timestamptz is null or updated_at <= $2::timestamptz)
                 and tier = 'shared'
               order by updated_at desc, number asc
               limit $3::int`,
    countSql: `select count(*) as count from loop_item
                where ($1::text[] is null or status = any($1::text[])) and tier = 'shared'`,
    related: (row) => (row.related_domain ? [{ kind: "loop_domain", id: row.related_domain }] : []),
  },
  {
    kind: "work_shape",
    sourceRef: "ops.work_shape_revision",
    params: ({ tenant, statuses, cursorAt, fetch }) => [tenant, statuses, cursorAt, fetch],
    countParams: ({ tenant, statuses }) => [tenant, statuses],
    open: () => SYSTEM_WORK_DESTINATION,
    // A shape revision's own status is the work request's shape_disposition —
    // the column set-work-shape-disposition writes. 'unset' is the source's
    // null, named rather than dropped, so the row stays enumerable.
    rowsSql: `select 'work_shape' as kind, s.id::text as id, s.version::text as version,
                     w.title, coalesce(w.shape_disposition, 'unset') as status,
                     s.created_at as updated_at, w.organization_tenant_id,
                     w.ref as related_work_request
                from ops.work_shape_revision s
                join ops.work_request w on w.id = s.work_request_id
               where ($1::text is null or w.organization_tenant_id = $1::text)
                 and ($2::text[] is null or coalesce(w.shape_disposition, 'unset') = any($2::text[]))
                 and ($3::timestamptz is null or s.created_at <= $3::timestamptz)
               order by s.created_at desc, s.id asc
               limit $4::int`,
    countSql: `select count(*) as count from ops.work_shape_revision s
                join ops.work_request w on w.id = s.work_request_id
                where ($1::text is null or w.organization_tenant_id = $1::text)
                  and ($2::text[] is null or coalesce(w.shape_disposition, 'unset') = any($2::text[]))`,
    related: (row) => (row.related_work_request
      ? [{ kind: "work_request", id: row.related_work_request }] : []),
  },
  {
    kind: "slice_plan",
    sourceRef: "ops.engineering_slice_plan",
    params: ({ tenant, statuses, cursorAt, fetch }) => [tenant, statuses, cursorAt, fetch],
    countParams: ({ tenant, statuses }) => [tenant, statuses],
    open: () => SYSTEM_WORK_DESTINATION,
    rowsSql: `select 'slice_plan' as kind, p.id::text as id, p.work_request_version::text as version,
                     coalesce(p.plan->>'title', w.title) as title,
                     coalesce(p.plan->>'status', 'registered') as status,
                     p.created_at as updated_at, w.organization_tenant_id,
                     w.ref as related_work_request
                from ops.engineering_slice_plan p
                join ops.work_request w on w.id = p.work_request_id
               where ($1::text is null or w.organization_tenant_id = $1::text)
                 and ($2::text[] is null or coalesce(p.plan->>'status', 'registered') = any($2::text[]))
                 and ($3::timestamptz is null or p.created_at <= $3::timestamptz)
               order by p.created_at desc, p.id asc
               limit $4::int`,
    countSql: `select count(*) as count from ops.engineering_slice_plan p
                join ops.work_request w on w.id = p.work_request_id
                where ($1::text is null or w.organization_tenant_id = $1::text)
                  and ($2::text[] is null or coalesce(p.plan->>'status', 'registered') = any($2::text[]))`,
    related: (row) => (row.related_work_request
      ? [{ kind: "work_request", id: row.related_work_request }] : []),
  },
  {
    kind: "governance_item",
    sourceRef: "ops.rule_admission",
    params: ({ statuses, cursorAt, fetch }) => [statuses, cursorAt, fetch],
    countParams: ({ statuses }) => [statuses],
    open: () => null,
    // DELIBERATELY NO JOIN TO public.rule. db/schema.sql grants SELECT on
    // public.rule to carr_writer and carr_authority only — never carr_reader,
    // the role behind DATABASE_URL_READER. Joining it would make this leg fail
    // 42501 on EVERY production request while every fake-client test stayed
    // green, so the title and the link come from ops.rule_admission's own
    // columns. The allowlist test derives the readable relations from the
    // schema's grants so a future leg cannot reintroduce this.
    rowsSql: `select 'governance_item' as kind, a.rule_id::text as id, a.version::text as version,
                     left(coalesce(a.reason, a.enforcement_class || ' · ' || a.binding_moment), 160) as title,
                     a.state as status, a.updated_at,
                     null::text as organization_tenant_id,
                     a.guidance_intake_id::text as related_intake
                from ops.rule_admission a
               where ($1::text[] is null or a.state = any($1::text[]))
                 and ($2::timestamptz is null or a.updated_at <= $2::timestamptz)
               order by a.updated_at desc, a.rule_id asc
               limit $3::int`,
    countSql: `select count(*) as count from ops.rule_admission a
                where ($1::text[] is null or a.state = any($1::text[]))`,
    related: (row) => (row.related_intake
      ? [{ kind: "guidance_intake", id: row.related_intake }] : []),
  },
];

// Word-bounded on purpose: a substring test would flag `updated_at` and
// `created_at`, which is exactly the kind of check that gets weakened to pass.
export const WRITE_KEYWORDS = [
  "insert", "update", "delete", "merge", "truncate", "drop", "alter", "create",
  "grant", "revoke", "copy", "call", "lock", "nextval", "setval",
];

/** A leg that is not a bare select, or that carries any write keyword, is a defect, not a query. */
export function assertReadOnly(sql) {
  const text = String(sql).toLowerCase();
  if (!text.trimStart().startsWith("select")) return false;
  if (/\bfor\s+(update|share|no\s+key\s+update)\b/.test(text)) return false;
  return !WRITE_KEYWORDS.some((keyword) => new RegExp(`\\b${keyword}\\b`).test(text));
}

export function encodeCursor(item) {
  const payload = JSON.stringify({ updated_at: item.updated_at, kind: item.kind, id: item.id });
  return Buffer.from(payload, "utf8").toString("base64");
}

export function decodeCursor(cursor) {
  if (cursor === null || cursor === undefined || cursor === "") return null;
  let parsed;
  try {
    parsed = JSON.parse(Buffer.from(String(cursor), "base64").toString("utf8"));
  } catch {
    throw typedError("AUTHORIZATION_REFUSED");
  }
  if (!parsed || typeof parsed.updated_at !== "string" || typeof parsed.kind !== "string" ||
      typeof parsed.id !== "string" || !WORK_INVENTORY_KINDS.includes(parsed.kind)) {
    throw typedError("AUTHORIZATION_REFUSED");
  }
  return parsed;
}

function isoOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date.toISOString();
}

/** Deterministic total order: updated_at desc, then kind asc, then id asc. */
function compareItems(a, b) {
  if (a.updated_at !== b.updated_at) return a.updated_at < b.updated_at ? 1 : -1;
  if (a.kind !== b.kind) return a.kind < b.kind ? -1 : 1;
  if (a.id === b.id) return 0;
  return a.id < b.id ? -1 : 1;
}

function afterCursor(item, cursor) {
  return cursor ? compareItems(cursor, item) < 0 : true;
}

function count(row) {
  const raw = row?.count;
  if (raw === null || raw === undefined || raw === "") return null;
  const value = Number(raw);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function sourceBlock({ sourceRef, observedAt, validUntil, correlationId, freshness, explanation }) {
  return {
    source: "work_inventory_census", source_ref: sourceRef,
    observed_at: observedAt, valid_until: validUntil, freshness,
    correlation_id: correlationId, safe_explanation: explanation,
  };
}

function normalizeList(value, allowed, fallback) {
  if (value === null || value === undefined) return fallback;
  const list = (Array.isArray(value) ? value : String(value).split(","))
    .map((entry) => String(entry).trim()).filter((entry) => entry !== "");
  if (list.length === 0) return fallback;
  if (allowed && list.some((entry) => !allowed.includes(entry))) throw typedError("AUTHORIZATION_REFUSED");
  return list;
}

function normalizeLimit(value) {
  if (value === null || value === undefined || value === "") return WORK_INVENTORY_LIMIT_DEFAULT;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) throw typedError("AUTHORIZATION_REFUSED");
  return Math.min(parsed, WORK_INVENTORY_LIMIT_MAX);
}

/**
 * The complete work inventory census. Pure: the caller injects `client`
 * ({ query(sql, params) }) and `now`. Server-scoped: the tenant is derived from
 * the actor, never taken from a caller argument, and any row carrying a
 * different tenant is dropped before it can reach a response.
 */
export async function readWorkInventoryCensus({
  client, actor, tenant = organizationTenantForActor(actor), correlationId,
  now = () => new Date(), cursor = null, limit, kinds, statuses, system = false, ...filters
}) {
  if (system === true || system === "true") return readSystemWorkCensus({client,actor,correlationId,now,cursor,limit,kinds,...filters});
  if (!correlationId || typeof correlationId !== "string") throw typedError("INTERNAL_ERROR");
  const boundTenant = organizationTenantForActor(actor);
  if (tenant !== boundTenant || tenant !== TENANT) throw typedError("TENANT_SCOPE_REFUSED");
  if (!actor?.slug || !VALID_ACTORS.has(actor.slug)) throw typedError("AUTHORIZATION_REFUSED");

  const selectedKinds = normalizeList(kinds, WORK_INVENTORY_KINDS, WORK_INVENTORY_KINDS);
  // Statuses are NOT enumerated in code: every status each source defines is in
  // scope by default, which is the whole point of the census. A null filter
  // means "all", so queued/dormant/superseded/declined/closed rows all return.
  const selectedStatuses = normalizeList(statuses, null, null);
  const pageLimit = normalizeLimit(limit);
  const decodedCursor = decodeCursor(cursor);

  const observedAtDate = now();
  const observedAt = observedAtDate.toISOString();
  const validUntil = new Date(observedAtDate.valueOf() + 60_000).toISOString();

  const legs = WORK_INVENTORY_LEGS.filter((leg) => selectedKinds.includes(leg.kind));
  const cursorAt = decodedCursor ? decodedCursor.updated_at : null;
  const binding = { tenant, statuses: selectedStatuses, cursorAt, fetch: pageLimit + 1 };

  const results = await Promise.all(legs.map(async (leg) => {
    if (!assertReadOnly(leg.rowsSql) || !assertReadOnly(leg.countSql)) throw typedError("INTERNAL_ERROR");
    try {
      const [rowsResult, countResult] = await Promise.all([
        client.query(leg.rowsSql, leg.params(binding)),
        client.query(leg.countSql, leg.countParams(binding)),
      ]);
      return { leg, rows: rowsResult?.rows || [], total: count(countResult?.rows?.[0]) };
    } catch (error) {
      return { leg, error: classifyReadError(error) };
    }
  }));

  const candidates = [];
  const coverage = [];
  for (const result of results) {
    const { leg } = result;
    if (result.error) {
      coverage.push({
        kind: leg.kind, source_ref: leg.sourceRef, state: "unavailable",
        count_returned: 0, count_total: null, reason: result.error,
      });
      continue;
    }
    let unorderable = 0;
    let excluded = 0;
    const legItems = [];
    for (const row of result.rows) {
      const updatedAt = isoOrNull(row.updated_at);
      // A row with no order key cannot be paged deterministically. Dropping it
      // silently would be the capped-queue failure again, so it is counted and
      // named in coverage instead.
      if (updatedAt === null) { unorderable += 1; continue; }
      const rowTenant = row.organization_tenant_id ?? null;
      if (rowTenant !== null && rowTenant !== tenant) { excluded += 1; continue; }
      const related = (leg.related(row) || []).filter((link) => link.kind && link.id);
      legItems.push({
        kind: leg.kind,
        id: row.id === null || row.id === undefined ? null : String(row.id),
        version: row.version === null || row.version === undefined ? null : String(row.version),
        title: row.title ?? null,
        status: row.status ?? null,
        source_ref: leg.sourceRef,
        updated_at: updatedAt,
        related,
        unlinked: related.length === 0,
        open: leg.open(row),
      });
    }
    const capped = result.rows.length > pageLimit;
    const reasons = [];
    if (result.total === null) reasons.push("count_unavailable");
    if (unorderable > 0) reasons.push(`rows_missing_order_key:${unorderable}`);
    const state = reasons.length > 0 ? "partial" : "complete";
    candidates.push(...legItems);
    coverage.push({
      kind: leg.kind, source_ref: leg.sourceRef, state,
      count_returned: 0, count_total: result.total,
      reason: reasons.length > 0 ? reasons.join(";") : null,
      excluded_other_tenant: excluded, page_capped: capped,
    });
  }

  const ordered = candidates
    .filter((item) => afterCursor(item, decodedCursor))
    .sort(compareItems);
  const page = ordered.slice(0, pageLimit);
  const nextCursor = ordered.length > pageLimit ? encodeCursor(page[page.length - 1]) : null;
  for (const entry of coverage) {
    entry.count_returned = page.filter((item) => item.kind === entry.kind).length;
  }

  const unavailable = coverage.filter((entry) => entry.state === "unavailable").map((entry) => entry.kind);
  const partial = coverage.filter((entry) => entry.state === "partial").map((entry) => entry.kind);
  const freshness = unavailable.length > 0 || partial.length > 0 ? "unknown" : "fresh";
  const explanation = unavailable.length > 0
    ? `This census is INCOMPLETE, not empty: ${unavailable.join(", ")} could not be read. The remaining sources are current as of this request.`
    : partial.length > 0
      ? `This census returned every row it could enumerate, but ${partial.join(", ")} could not be counted in full, so a total is not claimed.`
      : "Fresh because every source answered a no-store request-time canonical read; valid for 60 seconds.";

  return {
    viewer: actor.slug,
    tenant,
    kinds: selectedKinds,
    statuses: selectedStatuses,
    limit: pageLimit,
    items: page,
    coverage,
    census_complete: unavailable.length === 0 && partial.length === 0,
    next_cursor: nextCursor,
    source: sourceBlock({
      sourceRef: legs.map((leg) => leg.sourceRef).join("+"),
      observedAt, validUntil, correlationId, freshness, explanation,
    }),
  };
}
