import assert from "node:assert/strict";
import test from "node:test";
import { projectPropertyEvidence, readPropertyEvidence } from "../src/tour-property-evidence.js";

const propertyId = "10000000-0000-4000-8000-000000000001";
const at = "2026-09-29T12:00:00.000Z";
const source = { locator: "https://example.invalid/county-record", evidence_class: "direct_source", retrieved_at: "2026-09-01T00:00:00Z" };
const row = (field, value, asOf, extras = {}) => ({ field, value, as_of: asOf, effective_from: asOf, effective_to: null,
  review_state: "reviewed", geometry_precision: "unknown", source_crs: null, geometry_method: null,
  source, ...extras });

test("property evidence keeps historical county changes and never turns context into a legal determination", () => {
  const rows = [
    row("county", "Escambia", "2025-01-01T00:00:00Z", { geometry_precision: "jurisdiction_reference", source_crs: "EPSG:4326" }),
    row("county", "Santa Rosa", "2026-08-01T00:00:00Z", { geometry_precision: "jurisdiction_reference", source_crs: "EPSG:4326" }),
    row("parcel", "17-3S-29-0000-001", "2026-01-01T00:00:00Z", { geometry_precision: "parcel_reference", geometry_method: "authoritative_reference" }),
  ];
  const old = projectPropertyEvidence(propertyId, "2025-06-01T00:00:00Z", rows);
  const current = projectPropertyEvidence(propertyId, at, rows);
  assert.equal(old.facts.county.value, "Escambia");
  assert.equal(current.facts.county.value, "Santa Rosa");
  assert.equal(current.facts.county.source.locator, source.locator);
  assert.equal(current.facts.county.source_crs, "EPSG:4326");
  assert.equal(current.facts.county.determination_status, "context_only");
  assert.equal(current.facts.parcel.as_of, "2026-01-01T00:00:00Z");
  assert.equal(current.facts.municipality.status, "unavailable");
  assert.equal(current.facts.special_authority.status, "unavailable");
});

test("newer conflict hides a prior reviewed county and exposes both sourced candidates", () => {
  const rows = [
    row("county", "Escambia", "2026-01-01T00:00:00Z"),
    row("county", "Bay", "2026-08-01T00:00:00Z", { review_state: "conflicted" }),
    row("county", "Walton", "2026-08-01T00:00:00Z", { review_state: "conflicted", source: { ...source, locator: "https://example.invalid/other-record" } }),
  ];
  const result = projectPropertyEvidence(propertyId, at, rows);
  assert.equal(result.facts.county.status, "conflicted");
  assert.equal(result.facts.county.value, null);
  assert.deepEqual(result.facts.county.conflicts.map(item => item.value), ["Bay", "Walton"]);
  assert.equal(result.facts.county.conflicts[1].source.locator, "https://example.invalid/other-record");
});

test("five-county boundary holds unknown for an out-of-area source and preserves a county-edge conflict", () => {
  const outside = projectPropertyEvidence(propertyId, at, [
    row("county", "Baldwin", "2026-09-01T00:00:00Z", { source_crs: "EPSG:4326" }),
  ]);
  assert.equal(outside.facts.county.status, "unknown");
  const edge = projectPropertyEvidence(propertyId, at, [
    row("county", "Bay", "2026-09-01T00:00:00Z", { source_crs: "EPSG:4326" }),
    row("county", "Walton", "2026-09-01T00:00:00Z", { source_crs: "EPSG:4326" }),
  ]);
  assert.equal(edge.facts.county.status, "conflicted");
  assert.deepEqual(edge.facts.county.conflicts.map(item => item.value), ["Bay", "Walton"]);
  assert.equal(edge.facts.county.determination_status, "context_only");
});

test("read query calls the tenant-bound, time-bound evidence function and returns unknown for missing evidence", async () => {
  const calls = [];
  const client = { async query(sql, params) { calls.push({ sql, params }); return { rows: [{ data: [] }] }; } };
  const result = await readPropertyEvidence(client, "carr-internal", propertyId, at);
  assert.deepEqual(calls[0].params, ["carr-internal", propertyId, at]);
  assert.match(calls[0].sql, /ops\.read_tour_property_evidence/);
  assert.equal(result.facts.county.status, "unknown");
  assert.equal(result.facts.parcel.status, "unknown");
  assert.equal(result.facts.site_address.status, "unknown");
});
