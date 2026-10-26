// Versioned, internal-only property evidence read. No spatial overlay is a
// legal determination; absent authoritative coverage stays visible as unknown.

import type { ClientBase } from "@neondatabase/serverless";

/** One reviewed evidence row as ops.read_tour_property_evidence returns it. */
type EvidenceRow = {
  field: string; value: unknown; review_state: string; source?: unknown;
  as_of: unknown; effective_from: unknown; effective_to?: unknown;
  geometry_precision?: unknown; geometry_method?: unknown; source_crs?: unknown;
};

const FIELDS = ["county", "municipality", "special_authority", "parcel", "site_address", "building"];
const FIVE_COUNTIES = new Set<string | null>(["Escambia", "Santa Rosa", "Okaloosa", "Walton", "Bay"]);
const UNAVAILABLE = new Set(["municipality", "special_authority"]);

function instant(value: unknown): number {
  // Date.parse coerces its argument to a string, so a non-string reads as NaN.
  const time = value instanceof Date ? value.getTime() : Date.parse(value as string);
  return Number.isFinite(time) ? time : -Infinity;
}
function iso(value: unknown): string | null {
  return value instanceof Date ? value.toISOString() : typeof value === "string" ? value : null;
}
function publicSource(value: unknown) {
  const source: Record<string, unknown> = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return {
    locator: typeof source.locator === "string" ? source.locator : null,
    evidence_class: typeof source.evidence_class === "string" ? source.evidence_class : null,
    retrieved_at: iso(source.retrieved_at),
  };
}
function projectedValue(row: EvidenceRow): string | null {
  if (typeof row.value === "string") return row.value;
  if (row.field === "site_address" && row.value && typeof row.value === "object") {
    const address = row.value as Record<string, unknown>;
    // The predicate admits only non-blank strings, so a hit is a string.
    return [address.formatted, address.address, address.street_address, address.street]
      .find(value => typeof value === "string" && value.trim()) as string | undefined || null;
  }
  return null;
}
function fact(row: EvidenceRow) {
  return {
    status: "reviewed", value: projectedValue(row), as_of: iso(row.as_of),
    effective_from: iso(row.effective_from), effective_to: iso(row.effective_to),
    geometry_precision: typeof row.geometry_precision === "string" ? row.geometry_precision : "unknown",
    geometry_method: typeof row.geometry_method === "string" ? row.geometry_method : null,
    source_crs: typeof row.source_crs === "string" ? row.source_crs : null,
    review_state: row.review_state, source: publicSource(row.source),
    determination_status: "context_only", conflicts: [],
  };
}
function missing(field: string) {
  return { status: UNAVAILABLE.has(field) ? "unavailable" : "unknown", value: null,
    reason: UNAVAILABLE.has(field) ? "authoritative_coverage_missing" : "no_authorized_evidence",
    as_of: null, effective_from: null, effective_to: null,
    geometry_precision: "unknown", geometry_method: null, source_crs: null,
    review_state: "unknown", source: null, determination_status: "context_only", conflicts: [] };
}

export function projectPropertyEvidence(propertyId: string, asOf: unknown, rows: unknown) {
  const selectedAt = instant(asOf);
  const facts: Record<string, unknown> = {};
  for (const field of FIELDS) {
    if (UNAVAILABLE.has(field)) { facts[field] = missing(field); continue; }
    const candidates: EvidenceRow[] = (Array.isArray(rows) ? rows : [])
      .filter(row => row?.field === field && ["reviewed", "conflicted"].includes(row.review_state)
        && instant(row.as_of) <= selectedAt && instant(row.effective_from) <= selectedAt
        && (row.effective_to == null || instant(row.effective_to) > selectedAt)
        && (field !== "county" || FIVE_COUNTIES.has(projectedValue(row))))
      .sort((a, b) => instant(b.as_of) - instant(a.as_of) || instant(b.effective_from) - instant(a.effective_from));
    if (!candidates.length) { facts[field] = missing(field); continue; }
    const latest = candidates[0];
    const peers = candidates.filter(row => instant(row.as_of) === instant(latest.as_of));
    const values = new Set(peers.map(projectedValue));
    if (latest.review_state === "conflicted" || peers.some(row => row.review_state === "conflicted") || values.size > 1) {
      facts[field] = { ...missing(field), status: "conflicted", reason: "evidence_conflict",
        as_of: iso(latest.as_of), review_state: "conflicted",
        conflicts: peers.map(row => ({ ...fact(row), status: "conflicted", conflicts: [] })) };
    } else {
      facts[field] = fact(latest);
    }
  }
  return { schema: "tour-property-evidence.v1", property_id: propertyId, as_of: iso(asOf), facts };
}

export async function readPropertyEvidence(client: Pick<ClientBase, "query">, tenant: string, propertyId: string, asOf: unknown) {
  const result = await client.query("select ops.read_tour_property_evidence($1::text,$2::uuid,$3::timestamptz) as data", [tenant, propertyId, asOf]);
  return projectPropertyEvidence(propertyId, asOf, result.rows[0]?.data);
}
