import assert from "node:assert/strict";
import test from "node:test";
import { projectTourPropertySearch, TOUR_SEARCH_COUNTIES, tourPropertySearchTools } from "../src/tour-property-search.js";

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const actor = { id: "actor-00000000-0000-4000-8000-000000000001", slug: "codex" };
const ids = {
  tour: "10000000-0000-4000-8000-000000000001",
  propertyA: "20000000-0000-4000-8000-000000000001",
  propertyB: "30000000-0000-4000-8000-000000000001",
  selection: "40000000-0000-4000-8000-000000000001",
  idempotency: "50000000-0000-4000-8000-000000000001",
};
const selectionDigest = `sha256:${"a".repeat(64)}`;
const propertyRef = "property:public:abcdefghijklmnop";

function harness() {
  const calls = [], events = [], envelopes = [];
  const client = { async query(sql, params) {
    calls.push({ sql, params });
    if (sql.includes("search_tour_properties")) return { rows: [{ search: { count: 1, has_more: true, cursor: "25", provider: "private", items: [{ property_id: ids.propertyA, property_ref: propertyRef, name: "Medical Plaza", address: "100 Clinic Way", county: "Escambia", state: "FL", property_type: "medical_office", availability: "available", size: { value: 4200, unit: "SF", verifier: "private" }, asking_economics: { value: 24, currency: "USD", period: "NNN" }, entrance_verified: true, public_projection_ready: false, photos_available: true, photo_count: 2, rights_receipt_id: ids.propertyB }] } }] };
    if (sql.includes("append_tour_selection_cart_version")) return { rows: [{ selection_version_id: ids.selection }] };
    if (sql.includes("read_tour_selection_cart")) return { rows: [{ cart: { tour_id: ids.tour, selection_version_id: ids.selection, selection_version: 2, property_ids: [ids.propertyA], updated_at: "2026-08-27T12:00:00Z", token_digest: selectionDigest } }] };
    throw new Error(sql);
  } };
  const withEnvelope = async (c, a, verb, args, fn) => { assert.equal(c, client); assert.equal(a, actor); envelopes.push({ verb, args }); return fn(); };
  return { client, calls, events, envelopes, tools: tourPropertySearchTools({ withEnvelope, writeEvent: async (...args) => events.push(args), ToolError }) };
}

const searchArgs = {
  query: "medical office", counties: ["Escambia", "Santa Rosa"], property_types: ["medical_office"],
  min_square_feet: 2000, max_square_feet: 8000, availability: ["available"],
  entrance_verified: null, public_projection_ready: null, photos_available: true,
  sort: "updated_desc", cursor: null, limit: 25,
};

test("specialist search is five-county, facts-only, deterministic, and sanitized", async () => {
  assert.deepEqual(TOUR_SEARCH_COUNTIES, ["Escambia", "Santa Rosa", "Okaloosa", "Walton", "Bay"]);
  const h = harness();
  assert.deepEqual(Object.keys(h.tools).sort(), ["append-tour-selection-cart-version", "read-tour-selection-cart", "search-tour-properties"]);
  assert.equal(h.tools["search-tour-properties"].writerConnection, true);
  assert.equal(h.tools["read-tour-selection-cart"].writerConnection, true);
  const result = await h.tools["search-tour-properties"].handler(h.client, actor, searchArgs);
  assert.equal(result.search.items[0].property_id, ids.propertyA);
  assert.equal(result.search.items[0].property_ref, propertyRef);
  assert.deepEqual(result.search.items[0].size, { value: 4200, unit: "SF" });
  assert.equal(result.search.items[0].source_label, "CARR reviewed property register");
  assert.equal(result.search.items[0].rights_status, "unknown");
  assert.equal(result.search.items[0].coordinate_precision, "unknown");
  assert.equal(result.search.cursor, "25");
  assert.equal(result.search.has_more, true);
  assert.doesNotMatch(JSON.stringify(result), /provider|rights_receipt|evidence|verifier|contact|token_digest/);
  assert.deepEqual(h.calls[0].params.slice(0, 2), ["carr-internal", actor.id]);
  assert.deepEqual(JSON.parse(h.calls[0].params[2]), searchArgs);
});

test("search refuses out-of-scope counties, advice-like sorts, invalid ranges, and authority selectors", async () => {
  for (const input of [
    { ...searchArgs, counties: ["Leon"] },
    { ...searchArgs, sort: "best_for_client" },
    { ...searchArgs, min_square_feet: 9000, max_square_feet: 8000 },
    { ...searchArgs, organization_tenant_id: "other" },
    { ...searchArgs, cursor: "next_1" },
  ]) {
    const h = harness();
    await assert.rejects(h.tools["search-tour-properties"].handler(h.client, actor, input), error => error instanceof ToolError);
    assert.equal(h.calls.length, 0);
  }
});

test("selection cart appends immutable versions and emits no property list in its event", async () => {
  const h = harness();
  const args = { idempotency_key: ids.idempotency, tour_id: ids.tour, base_selection_version_id: null, property_ids: [ids.propertyA, ids.propertyB], expected_selection_version: 0, selection_digest: selectionDigest };
  assert.deepEqual(await h.tools["append-tour-selection-cart-version"].handler(h.client, actor, args), { ok: true, selection_version_id: ids.selection, selected_count: 2 });
  assert.equal(h.envelopes.length, 1); assert.equal(h.events.length, 1);
  assert.doesNotMatch(JSON.stringify(h.events), new RegExp(ids.propertyA));
  assert.deepEqual(h.calls[0].params, ["carr-internal", ids.tour, null, JSON.stringify([ids.propertyA, ids.propertyB]), 0, selectionDigest]);
  const duplicate = { ...args, property_ids: [ids.propertyA, ids.propertyA] };
  await assert.rejects(h.tools["append-tour-selection-cart-version"].handler(h.client, actor, duplicate), error => error instanceof ToolError && error.payload.error === "tour_selection_invalid");
});

test("selection cart read is tenant-bound and strips digests", async () => {
  const h = harness();
  const result = await h.tools["read-tour-selection-cart"].handler(h.client, actor, { tour_id: ids.tour });
  assert.deepEqual(result.cart, { tour_id: ids.tour, selection_version_id: ids.selection, selection_version: 2, property_ids: [ids.propertyA], updated_at: "2026-08-27T12:00:00Z" });
  assert.deepEqual(h.calls[0].params, ["carr-internal", ids.tour, actor.id]);
  assert.doesNotMatch(JSON.stringify(result), /digest|provider|rights|evidence/);
});

test("public search projector drops malformed property identities and unknown fields", () => {
  const projected = projectTourPropertySearch({ items: [{ property_id: "bad", name: "drop" }, { property_id: ids.propertyB, county: "Leon", state: "FL", name: "Out of scope" }, { property_id: ids.propertyA, property_ref: `property:public:${"x".repeat(129)}`, name: "Keep facts", county: "Bay", state: "FL", internal_notes: "never" }] });
  assert.equal(projected.items.length, 1);
  assert.equal(projected.items[0].property_ref, undefined);
  assert.equal(projected.items[0].internal_notes, undefined);
});
