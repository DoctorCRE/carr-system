import assert from "node:assert/strict";
import test from "node:test";
import { execFile } from "node:child_process";
import { mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import {
  MAP_MODES, MAP_EVENT_TYPES, classifyPin, buildRouteVersionState, projectRoute,
  checkParity, reduceMapEvent, applyRouteVersion, cameraPlan, buildNativeNavLink,
  buildReturnState, resolveReturn, renderOrderedListHtml, renderRouteEndpointsHtml,
} from "../src/tour-map-route-state.js";

// Synthetic fixtures only: no client names, no real addresses.
const USER = "user-synthetic";
const NOW = "2026-09-30T12:00:00Z";
const approvedPin = (lat, lng, role = "entrance") => ({
  latitude: lat, longitude: lng, coordinate_role: role, precision_class: "entrance",
  review_state: "reviewed", human_approved: true,
});
const centroidPin = (lat, lng) => ({
  latitude: lat, longitude: lng, coordinate_role: "parcel_centroid", precision_class: "parcel",
  review_state: "reviewed", human_approved: true,
});
const geocoderPin = (lat, lng) => ({
  latitude: lat, longitude: lng, coordinate_role: "geocoder_candidate", precision_class: "approximate",
  review_state: "unreviewed", human_approved: false,
});

function route(overrides = {}) {
  return {
    tour_id: "tour-synthetic-1",
    projection_id: "proj-synthetic-1",
    route_version: 3,
    route_version_id: `route-version-${overrides.route_version ?? 3}`,
    canonical_dataset_version: "dataset-1", component_registry_version: "components-1",
    stops: [
      { route_stop_id: "rs-a", property_id: "prop-a", route_sequence: 1, route_label: "A", locked_state: "locked",
        dwell_minutes: 30, buffer_minutes: 10, title: "Synthetic Clinic One", address_line: "100 Example Way",
        position: approvedPin(30.6001, -88.0001) },
      { route_stop_id: "rs-b", property_id: "prop-b", route_sequence: 2, route_label: "B", locked_state: "flexible",
        dwell_minutes: 20, buffer_minutes: 10, title: "Synthetic Clinic Two", address_line: "200 Example Way",
        position: centroidPin(30.6102, -88.0102) },
      { route_stop_id: "rs-c", property_id: "prop-c", route_sequence: 3, route_label: "C", locked_state: "flexible",
        dwell_minutes: 20, buffer_minutes: 10, title: "Synthetic Clinic Three", address_line: "300 Example Way",
        position: approvedPin(30.6203, -88.0203, "parking_access") },
    ],
    ...overrides,
  };
}
const CHECKS = [
  "canonical_address_and_coordinate_review", "claims_and_layers_have_source_as_of_rights_and_review_state",
  "deterministic_rebuild_from_canonical_record", "exact_native_navigation_handoff",
  "locked_appointments_dwell_and_buffers_preserved", "map_list_route_offline_order_parity",
  "no_unresolved_route_critical_unknown_or_conflict", "optional_context_layers_progressively_disclosed",
  "ordered_offline_itinerary_verified", "phone_and_ipad_interaction_test",
  "provider_terms_attribution_expiry_and_cost_gate_passed",
];
const receipt = (overrides = {}) => ({
  decision: "approved", promotion_receipt_id: "rcpt-1", tour_id: "tour-synthetic-1",
  projection_id: "proj-synthetic-1", route_version: 3,
  route_version_id: `route-version-${overrides.route_version ?? 3}`,
  canonical_dataset_version: "dataset-1", component_registry_version: "components-1",
  provider_rights_receipt_ids: ["prr-1"],
  required_checks: Object.fromEntries(CHECKS.map(key => [key, true])),
  mobile_test_evidence: { status: "passed" }, native_navigation_test_evidence: { status: "passed" },
  offline_test_evidence: { status: "passed" },
  ...overrides,
});
const nav = (state, stopId, overrides = {}) => buildNativeNavLink(state, {
  route_stop_id: stopId, platform: "apple_maps", travel_mode: "driving",
  promotion_receipt: receipt(), now: NOW, user_ref: USER, ...overrides,
});
const tourState = (overrides = {}, options = {}) => buildRouteVersionState(route(overrides), { mode: "tour", ...options });
const renumber = (stops, suffix = "") => stops.map((stop, index) => ({
  ...stop, route_stop_id: `${stop.route_stop_id}${suffix}`, route_sequence: index + 1, route_label: "ABCD"[index],
}));

test("registers exactly the two modes and the typed events plus mode_change", () => {
  assert.deepEqual([...MAP_MODES], ["search", "tour"]);
  assert.deepEqual([...MAP_EVENT_TYPES].sort(), [
    "bounds_change", "draw_result", "feature_click", "filter_state", "mode_change",
    "route_stop_change", "selected_record", "slider_state",
  ]);
});

test("only entrance, driveway or parking access reviewed by a human is navigable", () => {
  assert.equal(classifyPin(approvedPin(1, 2)).navigable, true);
  assert.equal(classifyPin(approvedPin(1, 2, "driveway")).navigable, true);
  assert.equal(classifyPin(approvedPin(1, 2, "parking_access")).navigable, true);
  for (const pin of [centroidPin(1, 2), geocoderPin(1, 2)]) {
    const result = classifyPin(pin);
    assert.equal(result.navigable, false);
    assert.equal(result.display, "downgraded");
    assert.ok(result.reason.length > 0);
  }
  assert.equal(classifyPin({ ...approvedPin(1, 2), human_approved: false }).navigable, false);
  assert.equal(classifyPin({ ...approvedPin(1, 2), review_state: "unreviewed" }).navigable, false);
  assert.equal(classifyPin({ ...approvedPin(1, 2), review_state: "rejected" }).navigable, false);
  assert.equal(classifyPin({ ...approvedPin(1, 2), precision_class: "approximate" }).navigable, false);
  const missing = classifyPin(null);
  assert.equal(missing.navigable, false);
  assert.equal(missing.display, "unknown");
});

test("marker, list, card, story section and offline order all derive from one route version", () => {
  const projection = projectRoute(tourState({}, { selected_property_id: "prop-b" }));
  const ids = ["rs-a", "rs-b", "rs-c"];
  assert.deepEqual(projection.markers.map(item => item.route_stop_id), ids);
  assert.deepEqual(projection.list.map(item => item.route_stop_id), ids);
  assert.deepEqual(projection.story_sections.map(item => item.route_stop_id), ids);
  assert.deepEqual(projection.offline_itinerary.map(item => item.route_stop_id), ids);
  assert.deepEqual(projection.markers.map(item => item.label), ["A", "B", "C"]);
  assert.equal(projection.route_version, 3);
  assert.equal(projection.projection_id, "proj-synthetic-1");
  assert.equal(projection.card.property_id, "prop-b");
  assert.equal(projection.card.route_stop_id, "rs-b");
  assert.equal(checkParity(projection).ok, true);
});

test("markers carry a text label and shape so meaning never rests on colour", () => {
  const projection = projectRoute(tourState());
  for (const marker of projection.markers) {
    assert.ok(marker.accessible_name.includes(marker.label));
    assert.ok(["circle", "diamond"].includes(marker.shape));
  }
  assert.equal(projection.markers[0].shape, "circle");
  assert.equal(projection.markers[1].shape, "diamond");
});

test("centroid and unverified pins are visibly downgraded and withhold native navigation", () => {
  const projection = projectRoute(tourState(), { promotion_receipt: receipt(), user_ref: USER });
  const [a, b, c] = projection.markers;
  assert.equal(a.display, "verified");
  assert.equal(b.display, "downgraded");
  assert.match(b.precision_label, /approximate|centroid/i);
  assert.equal(c.display, "verified");
  const navigation = Object.fromEntries(projection.list.map(item => [item.route_stop_id, item.native_navigation]));
  assert.equal(navigation["rs-a"].available, true);
  assert.equal(navigation["rs-b"].available, false);
  assert.equal(navigation["rs-b"].reason_code, "pin_not_entrance_approved");
  assert.match(navigation["rs-b"].reason, /entrance|driveway|parking/i);
  assert.equal(projection.offline_itinerary[1].coordinate_card, null);
  assert.ok(projection.offline_itinerary[1].address_line);
  assert.deepEqual(projection.exclusions.map(item => item.route_stop_id), ["rs-b"]);
});

test("the projection is deep-frozen so a surface cannot mutate parity", () => {
  const projection = projectRoute(tourState());
  assert.throws(() => { "use strict"; projection.markers.push({}); });
  assert.throws(() => { "use strict"; projection.markers[0].label = "Z"; });
});

// ---- Finding 5: parity checker ------------------------------------------------

test("parity check names the surface that disagrees, for each independently corrupted surface", () => {
  const good = projectRoute(tourState({}, { selected_property_id: "prop-a" }));
  assert.equal(checkParity(good).ok, true);
  const clone = () => structuredClone(good);
  const corruptions = {
    list_order: p => { p.list = [p.list[1], p.list[0], p.list[2]]; },
    list_property: p => { p.list[0].property_id = "prop-x"; },
    list_label: p => { p.list[1].label = "Z"; },
    story_property: p => { p.story_sections[2].label = "Z"; },
    offline_sequence: p => { p.offline_itinerary[0].route_sequence = 9; },
    marker_label: p => { p.markers[0].label = "Z"; },
    card_stop: p => { p.card.route_stop_id = "rs-b"; p.card.label = "B"; },
    card_property: p => { p.card.property_id = "prop-b"; },
    card_title: p => { p.card.title = "Somewhere Else"; },
    card_navigation: p => { p.card.native_navigation = { available: true, reason_code: null, reason: null }; },
    missing_card: p => { p.card = null; },
    selected_flag: p => { p.markers[1].selected = true; },
    current_flag: p => { p.list[2].current = true; },
  };
  for (const [name, corrupt] of Object.entries(corruptions)) {
    const broken = clone();
    corrupt(broken);
    const result = checkParity(broken);
    assert.equal(result.ok, false, name);
    assert.ok(result.divergences.length > 0, name);
  }
  assert.ok(checkParity((() => { const p = clone(); p.list = [p.list[1], p.list[0], p.list[2]]; return p; })())
    .divergences.some(item => item.surface === "list"));
});

test("parity check does not collide distinct id and label tuples", () => {
  const good = structuredClone(projectRoute(tourState()));
  const broken = structuredClone(good);
  broken.markers[0].route_stop_id = "a:b"; broken.markers[0].label = "c";
  for (const surface of ["list", "story_sections", "offline_itinerary"]) {
    broken[surface][0].route_stop_id = "a"; broken[surface][0].label = "b:c";
  }
  assert.equal(checkParity(broken).ok, false);
});

test("duplicate sequences or identities are refused", () => {
  const dupSeq = route();
  dupSeq.stops[1].route_sequence = 1;
  assert.throws(() => buildRouteVersionState(dupSeq), /route_sequence/);
  const dupProp = route();
  dupProp.stops[1].property_id = "prop-a";
  assert.throws(() => buildRouteVersionState(dupProp), /property_id/);
  assert.throws(() => buildRouteVersionState(route({ route_version: 0 })), /route_version/);
  assert.throws(() => buildRouteVersionState(route({ projection_id: "" })), /projection_id/);
  assert.throws(() => buildRouteVersionState(route({ projection_id: undefined })), /projection_id/);
});

// ---- Finding 7: malformed constraints -----------------------------------------

test("malformed stop constraints are refused, not turned into unknown or flexible", () => {
  const bad = {
    negative_dwell: { dwell_minutes: -10 },
    string_buffer: { buffer_minutes: "10" },
    fractional_dwell: { dwell_minutes: 10.5 },
    over_a_day: { dwell_minutes: 1441 },
    unknown_lock: { locked_state: "sometimes" },
    missing_lock: { locked_state: undefined },
    nan_buffer: { buffer_minutes: Number.NaN },
  };
  for (const [name, patch] of Object.entries(bad)) {
    const data = route();
    Object.assign(data.stops[0], patch);
    assert.throws(() => buildRouteVersionState(data), /invalid_(duration|locked_state)/, name);
  }
});

test("a deliberate unknown duration is kept as null and boundary values are accepted", () => {
  const data = route();
  data.stops[0].dwell_minutes = null;
  delete data.stops[0].buffer_minutes;
  data.stops[1].dwell_minutes = 0;
  data.stops[1].buffer_minutes = 1440;
  const state = buildRouteVersionState(data);
  assert.equal(state.route.stops[0].dwell_minutes, null);
  assert.equal(state.route.stops[0].buffer_minutes, null);
  assert.equal(state.route.stops[1].dwell_minutes, 0);
  assert.equal(state.route.stops[1].buffer_minutes, 1440);
});

// ---- Finding 8: appointment windows and endpoints -----------------------------

test("locked appointment windows and route endpoints survive state, revisions, projections and lists", () => {
  const data = route({
    start_point: { latitude: 30.59, longitude: -88.0, label: "Office" },
    end_point: { latitude: 30.64, longitude: -88.05, label: "Airport" },
  });
  data.stops[0].appointment_start = "2026-10-05T14:00:00Z";
  data.stops[0].appointment_end = "2026-10-05T14:45:00Z";
  const state = buildRouteVersionState(data, { mode: "tour", selected_property_id: "prop-a" });
  const projection = projectRoute(state);
  const window = { start: "2026-10-05T14:00:00Z", end: "2026-10-05T14:45:00Z" };
  assert.deepEqual(projection.list[0].appointment, window);
  assert.deepEqual(projection.card.appointment, window);
  assert.deepEqual(projection.offline_itinerary[0].appointment, window);
  assert.equal(projection.list[1].appointment, null);
  assert.deepEqual(projection.route_endpoints.start_point, { latitude: 30.59, longitude: -88.0, label: "Office" });
  assert.deepEqual(projection.route_endpoints.end_point, { latitude: 30.64, longitude: -88.05, label: "Airport" });
  assert.match(renderOrderedListHtml(projection), /2026-10-05T14:00:00Z/);
  assert.match(renderRouteEndpointsHtml(projection), /Office/);
  assert.match(renderRouteEndpointsHtml(projection), /Airport/);
  // A revision that carries the same facts keeps them.
  const next = structuredClone(data);
  next.route_version = 4;
  const { state: after } = applyRouteVersion(state, next);
  const again = projectRoute(after);
  assert.deepEqual(again.list[0].appointment, window);
  assert.deepEqual(again.route_endpoints, projection.route_endpoints);
  // A state with no endpoints says so rather than inventing some.
  assert.deepEqual(projectRoute(tourState()).route_endpoints, { start_point: null, end_point: null });
});

test("malformed appointment windows and endpoints are refused", () => {
  const half = route(); half.stops[0].appointment_start = "2026-10-05T14:00:00Z";
  assert.throws(() => buildRouteVersionState(half), /appointment/);
  const reversed = route();
  reversed.stops[0].appointment_start = "2026-10-05T15:00:00Z";
  reversed.stops[0].appointment_end = "2026-10-05T14:00:00Z";
  assert.throws(() => buildRouteVersionState(reversed), /appointment/);
  const junk = route(); junk.stops[0].appointment_start = "soon"; junk.stops[0].appointment_end = "later";
  assert.throws(() => buildRouteVersionState(junk), /appointment/);
  assert.throws(() => buildRouteVersionState(route({ start_point: { latitude: 99, longitude: 0 } })), /invalid_coordinate/);
  assert.throws(() => buildRouteVersionState(route({ end_point: "airport" })), /end_point/);
});

// ---- identity and reorder ---------------------------------------------------------

test("route_sequence and label are presentation only: reorder keeps property identity", () => {
  const state = tourState({}, { selected_property_id: "prop-c" });
  const next = route({ route_version: 4 });
  next.stops = renumber([next.stops[2], next.stops[0], next.stops[1]]);
  const { state: reordered, mapping } = applyRouteVersion(state, next);
  assert.equal(reordered.selected_property_id, "prop-c");
  assert.deepEqual(projectRoute(reordered).markers.map(m => m.property_id), ["prop-c", "prop-a", "prop-b"]);
  assert.equal(mapping.length, 3);
  const c = mapping.find(item => item.property_id === "prop-c");
  assert.equal(c.old_route_sequence, 3);
  assert.equal(c.new_route_sequence, 1);
  assert.equal(c.old_route_label, "C");
  assert.equal(c.new_route_label, "A");
  assert.equal(c.disposition, "resequenced");
  assert.equal(c.old_route_version, 3);
  assert.equal(c.new_route_version, 4);
});

test("version mapping reports removed, added and unchanged stops explicitly", () => {
  const state = tourState();
  const next = route({ route_version: 4 });
  next.stops = [next.stops[0], { ...next.stops[2], route_sequence: 2, route_label: "B" }];
  next.stops.push({
    route_stop_id: "rs-d", property_id: "prop-d", route_sequence: 3, route_label: "C", locked_state: "flexible",
    dwell_minutes: 15, buffer_minutes: 5, title: "Synthetic Clinic Four", address_line: "400 Example Way",
    position: approvedPin(30.63, -88.03),
  });
  const { mapping } = applyRouteVersion(state, next);
  const byProperty = Object.fromEntries(mapping.map(item => [item.property_id, item.disposition]));
  assert.deepEqual(byProperty, { "prop-a": "unchanged", "prop-b": "removed", "prop-c": "resequenced", "prop-d": "added" });
});

test("a removed selection clears selection instead of pointing at a stale stop", () => {
  const state = tourState({}, { selected_property_id: "prop-b" });
  const next = route({ route_version: 4 });
  next.stops = renumber([next.stops[0], next.stops[2]]);
  const { state: after } = applyRouteVersion(state, next);
  assert.equal(after.selected_property_id, null);
});

// ---- events (findings 6 and 9) ----------------------------------------------------

const square = { type: "Polygon", coordinates: [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]] };

test("typed events never remount the map and keep camera and selection", () => {
  let state = buildRouteVersionState(route(), { mode: "search" });
  const steps = [
    { type: "bounds_change", route_version: 3, bounds: [-88.1, 30.5, -87.9, 30.7] },
    { type: "feature_click", route_version: 3, property_id: "prop-a" },
    { type: "filter_state", route_version: 3, filters: { locked_state: "locked" } },
    { type: "slider_state", route_version: 3, name: "horizon_days", value: 7 },
    { type: "draw_result", route_version: 3, geometry: square },
  ];
  for (const event of steps) {
    const result = reduceMapEvent(state, event);
    assert.equal(result.effects.remount, false, event.type);
    assert.equal(result.effects.map_instance, "keep", event.type);
    state = result.state;
  }
  assert.deepEqual(state.camera.bounds, [-88.1, 30.5, -87.9, 30.7]);
  assert.equal(state.selected_property_id, "prop-a");
  assert.equal(state.filters.locked_state, "locked");
  assert.equal(state.sliders.horizon_days, 7);
  assert.deepEqual(state.drawn_geometry, square);
});

test("accepted payloads are snapshots: mutating the event afterwards changes nothing", () => {
  const state = buildRouteVersionState(route(), { mode: "search" });
  const filters = { owner: { names: ["a", "b"] }, tags: ["x"] };
  const geometry = structuredClone(square);
  const bounds = [-88.1, 30.5, -87.9, 30.7];
  let next = reduceMapEvent(state, { type: "filter_state", route_version: 3, filters }).state;
  next = reduceMapEvent(next, { type: "draw_result", route_version: 3, geometry }).state;
  next = reduceMapEvent(next, { type: "bounds_change", route_version: 3, bounds }).state;
  const snapshot = structuredClone(next);
  filters.owner.names.push("c"); filters.tags.length = 0; filters.owner = null;
  geometry.coordinates[0][0][0] = 50; bounds[0] = -1;
  assert.deepEqual(next, snapshot);
  // The prior state is also untouched by the reduction.
  assert.deepEqual(state.filters, {});
  assert.equal(state.drawn_geometry, null);
});

test("slider values must be scalars; objects and missing values are refused", () => {
  const state = buildRouteVersionState(route(), { mode: "search" });
  const before = structuredClone(state);
  for (const value of [undefined, null, { a: 1 }, [1], () => 1, Number.NaN, Infinity]) {
    assert.throws(() => reduceMapEvent(state, { type: "slider_state", route_version: 3, name: "x", value }), /slider|invalid/);
  }
  assert.throws(() => reduceMapEvent(state, { type: "slider_state", route_version: 3, value: 1 }), /name/);
  assert.deepEqual(state, before);
  for (const value of [0, -2.5, "30d", true]) {
    assert.equal(reduceMapEvent(state, { type: "slider_state", route_version: 3, name: "x", value }).state.sliders.x, value);
  }
});

test("draw_result accepts Polygon and MultiPolygon only, with valid closed rings and coordinates", () => {
  const state = buildRouteVersionState(route(), { mode: "search" });
  const before = structuredClone(state);
  const multi = { type: "MultiPolygon", coordinates: [square.coordinates, square.coordinates] };
  assert.deepEqual(reduceMapEvent(state, { type: "draw_result", route_version: 3, geometry: multi }).state.drawn_geometry, multi);
  const bad = {
    array: [],
    empty_object: {},
    point: { type: "Point", coordinates: [0, 0] },
    no_coordinates: { type: "Polygon" },
    empty_polygon: { type: "Polygon", coordinates: [] },
    short_ring: { type: "Polygon", coordinates: [[[0, 0], [1, 1], [0, 0]]] },
    open_ring: { type: "Polygon", coordinates: [[[0, 0], [0, 1], [1, 1], [1, 0]]] },
    out_of_range: { type: "Polygon", coordinates: [[[0, 0], [0, 95], [1, 1], [1, 0], [0, 0]]] },
    not_numbers: { type: "Polygon", coordinates: [[["a", 0], [0, 1], [1, 1], [1, 0], ["a", 0]]] },
    empty_multi: { type: "MultiPolygon", coordinates: [] },
  };
  for (const [name, geometry] of Object.entries(bad)) {
    assert.throws(() => reduceMapEvent(state, { type: "draw_result", route_version: 3, geometry }), /geometry|invalid/, name);
  }
  assert.deepEqual(state, before);
});

test("bounds must be ordered in latitude and finite; antimeridian longitudes are allowed", () => {
  const state = buildRouteVersionState(route(), { mode: "search" });
  const before = structuredClone(state);
  for (const bounds of [[200, 0, 0, 0], [-88, 40, -87, 30], [-88, 30, -87, 30], [-88, 30, -87, Number.NaN], [-88, 30, -87], "x"]) {
    assert.throws(() => reduceMapEvent(state, { type: "bounds_change", route_version: 3, bounds }), /bounds/);
  }
  assert.deepEqual(state, before);
  const across = reduceMapEvent(state, { type: "bounds_change", route_version: 3, bounds: [170, -10, -170, 10] });
  assert.deepEqual(across.state.camera.bounds, [170, -10, -170, 10]);
});

test("switching Search to Tour and back keeps map instance, camera and selection", () => {
  let state = buildRouteVersionState(route(), { mode: "search", selected_property_id: "prop-c" });
  state = reduceMapEvent(state, { type: "bounds_change", route_version: 3, bounds: [-88.1, 30.5, -87.9, 30.7] }).state;
  const toTour = reduceMapEvent(state, { type: "mode_change", route_version: 3, mode: "tour" });
  assert.equal(toTour.state.mode, "tour");
  assert.equal(toTour.effects.remount, false);
  assert.equal(toTour.state.selected_property_id, "prop-c");
  assert.deepEqual(toTour.state.camera.bounds, [-88.1, 30.5, -87.9, 30.7]);
  const back = reduceMapEvent(toTour.state, { type: "mode_change", route_version: 3, mode: "search" });
  assert.equal(back.state.mode, "search");
  assert.equal(back.effects.remount, false);
});

test("route_stop_change moves progress and selection together in Tour mode", () => {
  const state = tourState();
  const { state: moved } = reduceMapEvent(state, { type: "route_stop_change", route_version: 3, route_stop_id: "rs-b" });
  assert.equal(moved.current_route_stop_id, "rs-b");
  assert.equal(moved.selected_property_id, "prop-b");
  const projection = projectRoute(moved);
  assert.equal(projection.card.route_stop_id, "rs-b");
  assert.equal(projection.story_sections.find(item => item.active).route_stop_id, "rs-b");
  assert.equal(projection.list.find(item => item.current).route_stop_id, "rs-b");
  assert.equal(projection.markers.find(item => item.current).route_stop_id, "rs-b");
  assert.equal(projection.next_stop_id, "rs-c");
  assert.equal(checkParity(projection).ok, true);
});

test("events for an old route version or unknown targets are rejected without changing state", () => {
  const state = tourState();
  const before = structuredClone(state);
  assert.throws(() => reduceMapEvent(state, { type: "feature_click", route_version: 2, property_id: "prop-a" }), /stale_route_version/);
  assert.throws(() => reduceMapEvent(state, { type: "route_stop_change", route_version: 3, route_stop_id: "rs-zzz" }), /unknown_route_stop/);
  assert.throws(() => reduceMapEvent(state, { type: "teleport", route_version: 3 }), /unknown_event/);
  assert.throws(() => reduceMapEvent(state, { type: "mode_change", route_version: 3, mode: "satellite" }), /unknown_mode/);
  assert.deepEqual(state, before);
});

test("route_stop_change is refused in Search mode", () => {
  const state = buildRouteVersionState(route(), { mode: "search" });
  assert.throws(() => reduceMapEvent(state, { type: "route_stop_change", route_version: 3, route_stop_id: "rs-a" }), /tour_mode_required/);
});

test("reduced motion removes camera animation", () => {
  assert.deepEqual(cameraPlan({ prefersReducedMotion: true }), { animate: false, duration_ms: 0, method: "jumpTo" });
  const normal = cameraPlan({ prefersReducedMotion: false });
  assert.equal(normal.animate, true);
  assert.equal(normal.method, "flyTo");
  assert.ok(normal.duration_ms > 0);
  assert.equal(projectRoute(tourState(), { prefersReducedMotion: true }).camera_plan.animate, false);
});

// ---- Finding 1: receipt binding ----------------------------------------------------

test("native link is withheld for a downgraded stop and carries the reason and a fallback", () => {
  const result = nav(tourState(), "rs-b");
  assert.equal(result.available, false);
  assert.equal(result.reason_code, "pin_not_entrance_approved");
  assert.equal(result.link, undefined);
  assert.ok(result.fallback.address_line);
  assert.equal(result.fallback.coordinate_card, null);
});

test("native link needs a receipt bound to this exact tour, projection and route version", () => {
  const state = tourState();
  const cases = {
    promotion_receipt_missing: [undefined, null, "approved"],
    promotion_receipt_not_approved: [receipt({ decision: "rejected" }), receipt({ decision: undefined })],
    promotion_receipt_unbound: [
      receipt({ route_version: 2 }), receipt({ route_version: 4 }),
      receipt({ tour_id: "tour-other" }), receipt({ projection_id: "projection-other" }),
      receipt({ tour_id: undefined }), receipt({ projection_id: undefined }),
      { decision: "approved", route_version: 3 },
      { decision: "approved", route_version: 3, tour_id: "tour-other", projection_id: "projection-other" },
    ],
    promotion_receipt_incomplete: [
      receipt({ promotion_receipt_id: "" }), receipt({ provider_rights_receipt_ids: [] }),
      receipt({ required_checks: { ...receipt().required_checks, exact_native_navigation_handoff: false } }),
      receipt({ required_checks: {} }), receipt({ required_checks: undefined }),
      receipt({ native_navigation_test_evidence: { status: "failed" } }),
      receipt({ mobile_test_evidence: undefined }), receipt({ offline_test_evidence: { status: "pending" } }),
    ],
  };
  for (const [code, receipts] of Object.entries(cases)) {
    for (const candidate of receipts) {
      const result = nav(state, "rs-a", { promotion_receipt: candidate });
      assert.equal(result.available, false, `${code}: ${JSON.stringify(candidate)}`);
      assert.equal(result.reason_code, code, JSON.stringify(candidate));
      assert.equal(result.link, undefined);
    }
  }
});

test("a changed projection on a later route version invalidates the old receipt", () => {
  const state = tourState();
  const next = route({ route_version: 4, projection_id: "proj-synthetic-2" });
  const { state: after } = applyRouteVersion(state, next);
  assert.equal(nav(after, "rs-a", { promotion_receipt: receipt({ route_version: 4 }) }).reason_code, "promotion_receipt_unbound");
  assert.equal(nav(after, "rs-a", { promotion_receipt: receipt({ route_version: 4, projection_id: "proj-synthetic-2" }) }).available, true);
});

test("an approved entrance with a matching bound receipt yields a minimal-data link for each platform", () => {
  const state = tourState();
  const apple = nav(state, "rs-a");
  assert.equal(apple.available, true);
  assert.equal(apple.link, "https://maps.apple.com/?daddr=30.6001,-88.0001&dirflg=d");
  const google = nav(state, "rs-c", { platform: "google_maps", travel_mode: "walking" });
  assert.equal(google.link, "https://www.google.com/maps/dir/?api=1&destination=30.6203,-88.0203&travelmode=walking");
  for (const result of [apple, google]) {
    assert.doesNotMatch(result.link, /Synthetic|prop-|rs-|tour-synthetic|proj-|Example/);
    assert.equal(result.return_state.route_stop_id, result.route_stop_id);
    assert.equal(result.return_state.route_version, 3);
    assert.equal(result.return_state.user_ref, USER);
    assert.ok(Date.parse(result.return_state.expires_at) > Date.parse(NOW));
  }
});

test("bad platform, travel mode or stop is refused", () => {
  const state = tourState();
  assert.throws(() => nav(state, "rs-a", { platform: "waze" }), /platform/);
  assert.throws(() => nav(state, "rs-a", { travel_mode: "flying" }), /travel_mode/);
  assert.throws(() => nav(state, "rs-nope"), /unknown_route_stop/);
});

// ---- Finding 2: projection availability matches the handoff ------------------------

test("projection, card and HTML report the same navigation availability as the handoff", () => {
  const state = tourState({}, { selected_property_id: "prop-a" });
  const scenarios = {
    absent: undefined,
    rejected: receipt({ decision: "rejected" }),
    stale: receipt({ route_version: 2 }),
    other_tour: receipt({ tour_id: "tour-other" }),
    incomplete: receipt({ provider_rights_receipt_ids: [] }),
  };
  for (const [name, candidate] of Object.entries(scenarios)) {
    const projection = projectRoute(state, { promotion_receipt: candidate, user_ref: USER });
    const handoff = nav(state, "rs-a", { promotion_receipt: candidate });
    const row = projection.list.find(item => item.route_stop_id === "rs-a");
    assert.equal(row.native_navigation.available, false, name);
    assert.equal(projection.card.native_navigation.available, false, name);
    assert.equal(row.native_navigation.reason_code, handoff.reason_code, name);
    assert.equal(projection.card.native_navigation.reason_code, handoff.reason_code, name);
    assert.equal(row.pin_eligible, true, name);
    assert.match(renderOrderedListHtml(projection), /Navigation not available/, name);
    assert.equal(checkParity(projection).ok, true, name);
  }
  const ok = projectRoute(state, { promotion_receipt: receipt(), user_ref: USER });
  assert.equal(ok.list[0].native_navigation.available, true);
  assert.equal(ok.card.native_navigation.available, true);
  assert.equal(nav(state, "rs-a").available, true);
  // Without a user binding neither the projection nor the handoff offers navigation.
  assert.equal(projectRoute(state, { promotion_receipt: receipt() }).list[0].native_navigation.available, false);
  assert.equal(nav(state, "rs-a", { user_ref: undefined }).available, false);
  assert.equal(nav(state, "rs-a", { user_ref: "" }).reason_code, "user_binding_missing");
});

// ---- Findings 3 and 4: return -------------------------------------------------------

const back = (state, marker, options = {}) => resolveReturn(state, marker, { now: "2026-09-30T12:20:00Z", user_ref: USER, ...options });

test("return after native navigation lands on the exact stop that was handed off", () => {
  const state = tourState();
  const result = back(state, buildReturnState(nav(state, "rs-c")));
  assert.equal(result.ok, true);
  assert.equal(result.route_stop_id, "rs-c");
  assert.equal(result.state.current_route_stop_id, "rs-c");
  assert.equal(result.state.selected_property_id, "prop-c");
  assert.equal(result.state.mode, "tour");
});

test("return follows the recorded version mapping across several revisions", () => {
  const v3 = tourState();
  const marker = buildReturnState(nav(v3, "rs-c"));
  const r4 = route({ route_version: 4 });
  r4.stops = renumber([r4.stops[2], r4.stops[0], r4.stops[1]], "-v4");
  const { state: v4 } = applyRouteVersion(v3, r4);
  const r5 = route({ route_version: 5 });
  const [sa, sb, sc] = r5.stops;
  const r5stops = [
    { ...sc, route_sequence: 1, route_label: "A", route_stop_id: "rs-c-v5" },
    { ...sa, route_sequence: 2, route_label: "B", route_stop_id: "rs-a-v5" },
    { ...sb, route_sequence: 3, route_label: "C", route_stop_id: "rs-b-v5" },
  ];
  const { state: v5 } = applyRouteVersion(v4, { ...r5, stops: r5stops });
  const onV4 = back(v4, marker);
  assert.equal(onV4.ok, true);
  assert.equal(onV4.route_stop_id, "rs-c-v4");
  assert.equal(onV4.note, "route_version_changed");
  const onV5 = back(v5, marker);
  assert.equal(onV5.ok, true);
  assert.equal(onV5.route_stop_id, "rs-c-v5");
  assert.equal(onV5.state.selected_property_id, "prop-c");
});

test("a stop removed and later re-added is not restored by property identity", () => {
  const v3 = tourState();
  const marker = buildReturnState(nav(v3, "rs-c"));
  const r4 = route({ route_version: 4 });
  r4.stops = renumber([r4.stops[0], r4.stops[1]]);
  const { state: v4 } = applyRouteVersion(v3, r4);
  const r5 = route({ route_version: 5 });
  r5.stops = renumber([r5.stops[0], r5.stops[1], { ...r5.stops[2], route_stop_id: "rs-c-new" }]);
  const { state: v5 } = applyRouteVersion(v4, r5);
  assert.equal(v5.version_mapping.find(item => item.property_id === "prop-c").disposition, "added");
  for (const state of [v4, v5]) {
    const result = back(state, marker);
    assert.equal(result.ok, false);
    assert.equal(result.reason_code, "return_stop_removed");
    assert.equal(result.fallback, "ordered_list");
  }
});

test("return refuses future versions, unbound lineage and tampered markers", () => {
  const state = tourState();
  const marker = buildReturnState(nav(state, "rs-a"));
  assert.equal(back(state, { ...marker, route_version: 999 }).reason_code, "return_version_future");
  const fresh = buildRouteVersionState(route({ route_version: 7 }), { mode: "tour" });
  assert.equal(back(fresh, marker).reason_code, "return_version_unbound");
  assert.equal(back(state, { ...marker, property_id: "prop-b" }).reason_code, "return_marker_mismatch");
  assert.equal(back(state, { ...marker, route_stop_id: "rs-zzz" }).reason_code, "return_marker_mismatch");
  for (const bad of [null, undefined, "marker", 42, [], {}, { ...marker, route_version: "3" }, { ...marker, expires_at: "never" },
    { ...marker, tour_id: undefined }, { ...marker, route_stop_id: "" }]) {
    const result = back(state, bad);
    assert.equal(result.ok, false, JSON.stringify(bad));
    assert.equal(result.reason_code, "return_marker_invalid", JSON.stringify(bad));
    assert.equal(result.fallback, "ordered_list");
  }
});

test("return fails closed unless the marker is bound to the same user", () => {
  const state = tourState();
  const marker = buildReturnState(nav(state, "rs-a"));
  assert.equal(back(state, marker, { user_ref: "someone-else" }).reason_code, "return_user_mismatch");
  assert.equal(back(state, marker, { user_ref: undefined }).reason_code, "return_user_mismatch");
  assert.equal(back(state, marker, { user_ref: "" }).reason_code, "return_user_mismatch");
  // A handoff built without a user binding never produces an actionable marker.
  const unbound = nav(state, "rs-a", { user_ref: undefined });
  assert.equal(unbound.available, false);
  assert.throws(() => buildReturnState(unbound), /no_handoff/);
  // A hand-built marker with a null user is not accepted for anyone.
  const nulled = { ...marker, user_ref: null };
  assert.equal(back(state, nulled, { user_ref: "someone-else" }).reason_code, "return_marker_invalid");
  assert.equal(back(state, nulled, { user_ref: null }).reason_code, "return_marker_invalid");
});

test("return degrades to the ordered list when expired or for another tour", () => {
  const state = tourState();
  const marker = buildReturnState(nav(state, "rs-a", { platform: "google_maps" }));
  const expired = back(state, marker, { now: "2026-10-02T12:00:00Z" });
  assert.equal(expired.ok, false);
  assert.equal(expired.reason_code, "return_expired");
  assert.equal(expired.fallback, "ordered_list");
  assert.equal(back({ ...state, tour_id: "tour-other" }, marker).reason_code, "return_tour_mismatch");
  assert.equal(back(state, marker, { now: undefined }).reason_code, "return_expired");
});

// ---- ordered list ---------------------------------------------------------------------

test("ordered list fallback renders every stop in order, escapes text and needs no tiles", () => {
  const data = route();
  data.stops[0].title = "<img src=x onerror=alert(1)> Clinic";
  const projection = projectRoute(buildRouteVersionState(data, { mode: "tour" }));
  const html = renderOrderedListHtml(projection);
  assert.match(html, /^<ol /);
  assert.equal((html.match(/<li /g) || []).length, 3);
  assert.ok(html.indexOf("Synthetic Clinic Two") < html.indexOf("Synthetic Clinic Three"));
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
  assert.match(html, /Approximate location/);
  assert.doesNotMatch(html, /https?:\/\/[^"]*tile/);
  assert.match(html, /aria-label="Tour stops in visit order"/);
});

test("the module has no imports, so the app can vendor it into a browser bundle", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("../src/tour-map-route-state.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /^\s*import\s/m);
  assert.doesNotMatch(source, /require\(|process\.|node:/);
});

// ---- measurement script -------------------------------------------------------------

const script = new URL("../bin/measure-tour-map-route.mjs", import.meta.url).pathname;
const fixture = new URL("./fixtures/tour-map-route-state.synthetic.json", import.meta.url).pathname;
const run = (...args) => promisify(execFile)(process.execPath, [script, ...args]).then(
  ({ stdout }) => ({ code: 0, stdout }), error => ({ code: error.code, stdout: error.stdout, stderr: error.stderr }));

test("the measurement script reports parity and withheld navigation for a route file", async () => {
  const result = await run(fixture);
  assert.equal(result.code, 0);
  const report = JSON.parse(result.stdout);
  assert.equal(report.parity_ok, true);
  assert.equal(report.stop_count, 2);
  assert.equal(report.downgraded_or_unknown, 1);
  assert.equal(report.stops[0].navigation, "promotion_receipt_missing");
  assert.equal(report.stops[1].navigation, "pin_not_entrance_approved");
});

test("the measurement script exits nonzero on malformed constraint data", async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "tour-map-"));
  const data = JSON.parse((await import("node:fs")).readFileSync(fixture, "utf8"));
  data.stops[0].dwell_minutes = -10;
  data.stops[0].buffer_minutes = "10";
  data.stops[0].locked_state = "maybe";
  const file = path.join(dir, "bad.json");
  await writeFile(file, JSON.stringify(data));
  const result = await run(file);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /invalid_(duration|locked_state)/);
});


test("finding 1 supplement: promotion retains exact canonical route dataset and component bindings", () => {
  const state = tourState();
  assert.equal(nav(state, "rs-a").available, true);
  for (const field of ["route_version_id", "canonical_dataset_version", "component_registry_version"]) {
    for (const value of [undefined, "other"]) {
      assert.equal(nav(state, "rs-a", { promotion_receipt: receipt({ [field]: value }) }).available, false, field);
    }
    assert.equal(nav(tourState({ [field]: undefined }), "rs-a").available, false, `missing state ${field}`);
  }
});


test("finding 3 supplement: return lineage binds projections and strictly advancing unique steps", () => {
  const prior = tourState();
  const marker = buildReturnState(nav(prior, "rs-a"));
  assert.equal(back(tourState({ projection_id: "other-projection" }), marker).ok, false);
  const nextRoute = route({ route_version: 4, projection_id: "projection-4" });
  const next = applyRouteVersion(prior, nextRoute).state;
  const latest = applyRouteVersion(next, route({ route_version: 5, projection_id: "projection-5" })).state;
  assert.equal(back(latest, marker).ok, true);
  for (const field of ["from_projection_id", "from_route_version_id", "tour_id"]) {
    const broken = structuredClone(latest); broken.lineage[0][field] = "foreign";
    assert.equal(back(broken, marker).ok, false, field);
  }
  for (const version of [undefined, 3, 999]) {
    const broken = structuredClone(latest); broken.lineage[0].to_route_version = version;
    assert.equal(back(broken, marker).ok, false);
  }
  const duplicate = structuredClone(latest); duplicate.lineage.push(structuredClone(duplicate.lineage[0]));
  assert.equal(back(duplicate, marker).ok, false);
});


test("finding 8 supplement: canonical endpoint provenance and list durations round trip", () => {
  const point = { latitude: 30.5, longitude: -88.1, position_role: "start", precision_class: "surveyed", source_ref: "synthetic-start" };
  const data = route({ start_point: point, end_point: { ...point, position_role: "end", source_ref: "synthetic-end" } });
  const state = buildRouteVersionState(data, { selected_property_id: "prop-a" });
  for (const key of ["position_role", "precision_class", "source_ref"]) assert.equal(state.route.start_point[key], point[key]);
  point.source_ref = "mutated";
  assert.equal(state.route.start_point.source_ref, "synthetic-start");
  const projection = projectRoute(state);
  assert.equal(projection.list[0].dwell_minutes, 30); assert.equal(projection.list[0].buffer_minutes, 10);
  assert.match(renderRouteEndpointsHtml(projection), /synthetic-start/);
  assert.match(renderRouteEndpointsHtml(projection), /synthetic-end/);
  assert.match(renderOrderedListHtml(projection), /30 min dwell/);
  assert.match(renderOrderedListHtml(projection), /10 min buffer/);
});


test("finding 9 supplement: sparse rings and nonnumeric altitude cannot become accepted geometry", () => {
  const state = tourState(); const before = structuredClone(state);
  const ring = [[0, 0], [0, 1], [1, 1], [0, 0]];
  const sparse = new Array(4);
  const cases = [
    { type: "Polygon", coordinates: [sparse] },
    ...[undefined, "high", NaN, Infinity].map(altitude => ({ type: "Polygon", coordinates: [ring.map(p => [...p, altitude])] })),
  ];
  for (const geometry of cases) {
    assert.throws(() => reduceMapEvent(state, { type: "draw_result", route_version: 3, geometry }), /invalid_geometry/);
    assert.deepEqual(state, before);
  }
});
