// Test workspace fixture coverage and structure.
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

const fixtureDirectory = new URL("../fixtures/", import.meta.url);
const expectedStates = ["conflict", "empty", "loading", "normal", "offline", "partial", "refusal", "retry", "stale", "unauthorized"];
const expectedSurfaces = ["call-review", "command-center", "deal-room", "doc-request", "lead-board", "market-map", "marketing", "more", "notifications", "tour"];

async function fixtures() {
  const names = (await readdir(fixtureDirectory)).filter(name => name.endsWith(".v1.json")).sort();
  return Promise.all(names.map(async name => ({ name, value: JSON.parse(await readFile(join(fixtureDirectory.pathname, name), "utf8")) })));
}

test("canonical JSON twins cover every assigned surface", async () => {
  const all = await fixtures();
  assert.deepEqual(all.map(({ value }) => value.surface).sort(), expectedSurfaces);
  assert.equal(new Set(all.map(({ value }) => value.surface)).size, all.length);
});

test("every fixture declares all ten authored states", async () => {
  for (const { name, value } of await fixtures()) {
    assert.equal(value.schema_version, "workspace-fixture/v1", name);
    assert.equal(value.synthetic, true, name);
    assert.ok(Array.isArray(value.requirement_ids) && value.requirement_ids.length > 0, name);
    assert.deepEqual(Object.keys(value.states).sort(), expectedStates, name);
    for (const state of expectedStates) {
      assert.ok(value.states[state].freshness, `${name}:${state} freshness`);
      assert.equal(typeof value.states[state].freshness.status, "string", `${name}:${state} status`);
    }
  }
});

test("fixtures contain no client, credential, or transcript payload shapes", async () => {
  for (const { name, value } of await fixtures()) {
    const text = JSON.stringify(value);
    assert.doesNotMatch(text, /@[a-z0-9.-]+\.[a-z]{2,}/i, `${name}: email-shaped value`);
    assert.doesNotMatch(text, /\b(?:sk|pk)_(?:live|test)_[a-z0-9]+\b/i, `${name}: credential-shaped value`);
    assert.doesNotMatch(text, /"(?:password|access_token|refresh_token|client_secret|transcript|audio_blob)"\s*:/i, `${name}: restricted key`);
    assert.doesNotMatch(text, /\b\d{3}[-.) ]\d{3}[- ]\d{4}\b/, `${name}: phone-shaped value`);
  }
});

test("critical synthetic twins retain human gates and safe boundaries", async () => {
  const bySurface = new Map((await fixtures()).map(({ value }) => [value.surface, value]));
  const deal = JSON.stringify(bySurface.get("deal-room").states.normal);
  const call = JSON.stringify(bySurface.get("call-review").states.normal);
  const doc = JSON.stringify(bySurface.get("doc-request").states.normal);
  const market = JSON.stringify(bySurface.get("market-map").states.normal);
  const tour = JSON.stringify(bySurface.get("tour").states.normal);
  assert.match(deal, /parking_reasons/);
  assert.match(deal, /history/);
  assert.match(call, /consent_confirmed/);
  assert.match(call, /purge_eligible/);
  assert.match(doc, /external_effect[^]*false/);
  assert.match(market, /target_market_or_search_area/);
  assert.match(market, /practice_or_current_site/);
  assert.match(market, /candidate_property/);
  assert.match(market, /confirmed_project_site/);
  assert.match(tour, /locked[^]*true/);
  assert.match(tour, /offline_pack/);
});

test("market map keeps record type separate from sourced location role and makes gaps visible", async () => {
  const market = (await fixtures()).find(({ value }) => value.surface === "market-map").value.states.normal.market;
  const locations = market.records.flatMap(record => record.locations.map(location => ({ record, location })));
  assert.ok(market.records.some(record => record.locations.length > 1), "one business record can have multiple markers");
  const exactLocationKeys = ["map_item_id", "organization_tenant_id", "business_workspace_id", "record_type", "record_id", "record_version", "location_role", "location_source", "location_projection_version", "display_label", "location_precision", "position", "observed_at", "freshness", "redaction_profile", "deep_link", "trip_eligible", "trip_eligibility_reason"].sort();
  for (const { location } of locations) {
    assert.deepEqual(Object.keys(location).sort(), exactLocationKeys, location.map_item_id);
    assert.deepEqual(Object.keys(location.location_source).sort(), ["id", "type", "version"]);
  }
  assert.ok(locations.some(({ location }) => location.position === null), "unmapped locations remain explicit");
  assert.ok(locations.some(({ location }) => location.location_role === "appointment_or_visit_stop"), "visits are a first-class location layer");
  assert.ok(locations.some(({ location }) => location.trip_eligible));
  assert.ok(locations.some(({ location }) => !location.trip_eligible));
  assert.ok(locations.filter(({ location }) => location.position).every(({ location }) => Number.isFinite(location.position.x) && Number.isFinite(location.position.y)));
  assert.ok(market.records.every(record => ["prospect", "active_client", "project_in_process", "active_project"].includes(record.record_type)));
  assert.ok(market.records.some(record => record.record_type === "active_project"));
  assert.ok(market.records.every(record => !Object.hasOwn(record, "appointment")), "appointment authority is not record-level");
  assert.ok(market.visit_appointments.every(item => locations.some(({ location }) => location.map_item_id === item.map_item_id)));
  assert.ok(market.travel_opportunities.every(item => item.score === null && item.source_rows.length >= 4));
  assert.deepEqual(market.aggregate_counts, {
    business_records: 6,
    current_records: 4,
    stale_records: 1,
    unknown_records: 1,
    locations: 11,
    mapped_locations: 9,
    unlocated_locations: 2,
    trip_eligible_locations: 4,
    trip_ineligible_locations: 7
  });
});
