// Tour map route-version state (V0 slice 3, DoctorCRE v5 / WR-000012).
//
// ONE canonical route version feeds the marker, the list row, the property card,
// the story section and the offline itinerary, so they cannot disagree about
// order. Pure and import-free on purpose: the tours app vendors this file into a
// browser bundle and the tests run it unchanged in Node.
//
// Doctrine (workspace/contracts/market-map-route-planning.v1.json, 1.2.0):
//   * property identity is separate from mutable route_sequence / route_label;
//   * Search and Tour modes switch through typed events, never a remount;
//   * only a human-approved entrance, driveway or parking-access position gets a
//     native navigation link; centroids, geocoder candidates and unreviewed pins
//     are visibly downgraded and their link is withheld;
//   * no navigation link without an approved promotion receipt bound to this exact
//     Tour, projection and route version, carrying the full passed evidence set.
//     The receipt is READ elsewhere (a retrieval adapter is still owed, see
//     V5_J301_PROMOTION_RECEIPT_RETRIEVAL_SEAM, and it must also supply tour_id,
//     which the stored receipt payload does not carry today). This module only
//     checks the receipt object it is handed and never issues or authenticates one;
//   * an ordered list stays usable with no tiles.
//
// Nothing here calls a provider, a verb or the network.

export const MAP_MODES = Object.freeze(["search", "tour"]);
export const MAP_EVENT_TYPES = Object.freeze([
  "feature_click", "bounds_change", "draw_result", "filter_state", "slider_state",
  "selected_record", "route_stop_change", "mode_change",
]);
export const NAV_PLATFORMS = Object.freeze(["apple_maps", "google_maps"]);
export const NAV_TRAVEL_MODES = Object.freeze(["driving", "walking"]);
const NAVIGABLE_ROLES = Object.freeze(["entrance", "driveway", "parking_access"]);
const NAVIGABLE_PRECISION = Object.freeze(["entrance", "surveyed"]);
const RETURN_TTL_MS = 12 * 60 * 60 * 1000;
const MAX_MINUTES = 1440;
// The same eleven checks record-tour-map-promotion-receipt requires for an approval.
const REQUIRED_CHECKS = Object.freeze([
  "canonical_address_and_coordinate_review",
  "claims_and_layers_have_source_as_of_rights_and_review_state",
  "deterministic_rebuild_from_canonical_record",
  "exact_native_navigation_handoff",
  "locked_appointments_dwell_and_buffers_preserved",
  "map_list_route_offline_order_parity",
  "no_unresolved_route_critical_unknown_or_conflict",
  "optional_context_layers_progressively_disclosed",
  "ordered_offline_itinerary_verified",
  "phone_and_ipad_interaction_test",
  "provider_terms_attribution_expiry_and_cost_gate_passed",
]);
const EVIDENCE_FIELDS = Object.freeze(["mobile_test_evidence", "native_navigation_test_evidence", "offline_test_evidence"]);

export class RouteStateError extends Error {
  constructor(code, message, detail) {
    super(`${code}: ${message}`);
    this.name = "RouteStateError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}
const fail = (code, message, detail) => { throw new RouteStateError(code, message, detail); };

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
const isText = value => typeof value === "string" && value.trim().length > 0;

function text(value, path) {
  if (!isText(value)) fail("invalid_shape", `${path} must be a non-empty string`);
  return value;
}

function finite(value, min, max, path) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    fail("invalid_coordinate", `${path} must be a number between ${min} and ${max}`);
  }
  return value;
}

function minutes(value, path) {
  if (value === undefined || value === null) return null; // a deliberate unknown
  if (!Number.isInteger(value) || value < 0 || value > MAX_MINUTES) {
    fail("invalid_duration", `${path} must be a whole number of minutes from 0 to ${MAX_MINUTES}, or null when unknown`);
  }
  return value;
}

function timestamp(value, path) {
  if (!isText(value) || !/^\d{4}-\d{2}-\d{2}T/.test(value) || !Number.isFinite(Date.parse(value))) {
    fail("invalid_appointment", `${path} must be an ISO timestamp`);
  }
  return value;
}

function clone(value, path) {
  try { return structuredClone(value); } catch { return fail("invalid_shape", `${path} must be plain data`); }
}

/** Decide how a pin may be shown and whether it may drive native navigation. */
export function classifyPin(position) {
  if (!position || typeof position !== "object") {
    return { navigable: false, display: "unknown", precision_label: "Location unknown",
      reason: "No coordinate on record. Add or approve an entrance, driveway or parking access point." };
  }
  const roleOk = NAVIGABLE_ROLES.includes(position.coordinate_role);
  const precisionOk = NAVIGABLE_PRECISION.includes(position.precision_class);
  const reviewed = position.review_state === "reviewed" && position.human_approved === true;
  if (roleOk && precisionOk && reviewed) {
    return { navigable: true, display: "verified", precision_label: "Approved access point", reason: null };
  }
  const role = String(position.coordinate_role || "unknown").replace(/_/g, " ");
  const why = !roleOk
    ? `The pin is a ${role}, not an entrance, driveway or parking access point.`
    : !reviewed ? "The access point has not been approved by a human reviewer."
      : "The pin precision is below entrance level.";
  return { navigable: false, display: "downgraded", precision_label: "Approximate location", reason: why };
}

function normalizePoint(point, path) {
  if (point === undefined || point === null) return null;
  if (!isObject(point)) fail("invalid_shape", `${path} must be an object`);
  return {
    latitude: finite(point.latitude, -90, 90, `${path}.latitude`),
    longitude: finite(point.longitude, -180, 180, `${path}.longitude`),
    label: isText(point.label) ? point.label : null,
    ...Object.fromEntries(["position_role", "precision_class", "source_ref"]
      .filter(key => Object.hasOwn(point, key)).map(key => [key, clone(point[key], `${path}.${key}`)])),
  };
}

function normalizeStop(stop, index) {
  const path = `stops[${index}]`;
  if (!isObject(stop)) fail("invalid_shape", `${path} must be an object`);
  if (!Number.isSafeInteger(stop.route_sequence) || stop.route_sequence < 1) {
    fail("invalid_route_sequence", `${path}.route_sequence must be a positive integer`);
  }
  if (stop.locked_state !== "locked" && stop.locked_state !== "flexible") {
    fail("invalid_locked_state", `${path}.locked_state must be "locked" or "flexible"`);
  }
  const hasStart = stop.appointment_start !== undefined && stop.appointment_start !== null;
  const hasEnd = stop.appointment_end !== undefined && stop.appointment_end !== null;
  let appointment = null;
  if (hasStart !== hasEnd) fail("invalid_appointment", `${path} needs both appointment_start and appointment_end`);
  if (hasStart) {
    const start = timestamp(stop.appointment_start, `${path}.appointment_start`);
    const end = timestamp(stop.appointment_end, `${path}.appointment_end`);
    if (Date.parse(end) <= Date.parse(start)) fail("invalid_appointment", `${path} appointment must end after it starts`);
    appointment = { start, end };
  }
  const position = stop.position ? {
    latitude: finite(stop.position.latitude, -90, 90, `${path}.position.latitude`),
    longitude: finite(stop.position.longitude, -180, 180, `${path}.position.longitude`),
    coordinate_role: stop.position.coordinate_role ?? null,
    precision_class: stop.position.precision_class ?? null,
    review_state: stop.position.review_state ?? null,
    human_approved: stop.position.human_approved === true,
  } : null;
  return {
    route_stop_id: text(stop.route_stop_id, `${path}.route_stop_id`),
    property_id: text(stop.property_id, `${path}.property_id`),
    route_sequence: stop.route_sequence,
    route_label: text(stop.route_label, `${path}.route_label`),
    locked_state: stop.locked_state,
    dwell_minutes: minutes(stop.dwell_minutes, `${path}.dwell_minutes`),
    buffer_minutes: minutes(stop.buffer_minutes, `${path}.buffer_minutes`),
    appointment,
    title: text(stop.title, `${path}.title`),
    address_line: typeof stop.address_line === "string" ? stop.address_line : "",
    position,
  };
}

/** Build the one canonical state. Everything else is derived from `state.route`. */
export function buildRouteVersionState(route, options = {}) {
  if (!isObject(route)) fail("invalid_shape", "route must be an object");
  if (!Number.isSafeInteger(route.route_version) || route.route_version < 1) {
    fail("invalid_route_version", "route_version must be a positive integer");
  }
  if (!isText(route.projection_id)) fail("invalid_projection_id", "projection_id is required to bind a promotion receipt");
  if (!Array.isArray(route.stops)) fail("invalid_shape", "route.stops must be an array");
  const stops = route.stops.map(normalizeStop).sort((a, b) => a.route_sequence - b.route_sequence);
  for (const field of ["route_sequence", "property_id", "route_stop_id"]) {
    if (new Set(stops.map(stop => stop[field])).size !== stops.length) {
      fail(`duplicate_${field}`, `${field} must be unique within one route version`);
    }
  }
  const mode = options.mode ?? "search";
  if (!MAP_MODES.includes(mode)) fail("unknown_mode", `"${mode}" is not a map mode`);
  const known = new Set(stops.map(stop => stop.property_id));
  const selected = options.selected_property_id ?? null;
  return {
    tour_id: text(route.tour_id, "tour_id"),
    projection_id: route.projection_id,
    route_version: route.route_version,
    route_version_id: route.route_version_id ?? null,
    canonical_dataset_version: route.canonical_dataset_version ?? null,
    component_registry_version: route.component_registry_version ?? null,
    route: {
      stops,
      start_point: normalizePoint(route.start_point, "start_point"),
      end_point: normalizePoint(route.end_point, "end_point"),
    },
    mode,
    selected_property_id: selected !== null && known.has(selected) ? selected : null,
    current_route_stop_id: null,
    camera: { bounds: null },
    filters: {},
    sliders: {},
    drawn_geometry: null,
    lineage: [],
  };
}

function stopById(state, id) {
  const stop = state.route.stops.find(item => item.route_stop_id === id);
  if (!stop) fail("unknown_route_stop", `no stop "${id}" in route version ${state.route_version}`);
  return stop;
}

/** Reduced-motion aware camera plan. No essential explanation is animation-only. */
export function cameraPlan({ prefersReducedMotion = false } = {}) {
  return prefersReducedMotion
    ? { animate: false, duration_ms: 0, method: "jumpTo" }
    : { animate: true, duration_ms: 600, method: "flyTo" };
}

function receiptProblem(state, receipt) {
  if (!isObject(receipt)) {
    return ["promotion_receipt_missing", "No approved map promotion receipt was supplied."];
  }
  if (receipt.decision !== "approved") {
    return ["promotion_receipt_not_approved", "The map promotion receipt is not an approval."];
  }
  if (receipt.tour_id !== state.tour_id || receipt.projection_id !== state.projection_id
    || receipt.route_version !== state.route_version
    || ["route_version_id", "canonical_dataset_version", "component_registry_version"]
      .some(key => !isText(state[key]) || receipt[key] !== state[key])) {
    return ["promotion_receipt_unbound", "The promotion receipt does not cover this Tour, projection and route version."];
  }
  const checks = receipt.required_checks;
  const complete = isText(receipt.promotion_receipt_id)
    && Array.isArray(receipt.provider_rights_receipt_ids) && receipt.provider_rights_receipt_ids.length > 0
    && receipt.provider_rights_receipt_ids.every(isText)
    && isObject(checks) && REQUIRED_CHECKS.every(key => checks[key] === true)
    && EVIDENCE_FIELDS.every(key => isObject(receipt[key]) && receipt[key].status === "passed");
  if (!complete) {
    return ["promotion_receipt_incomplete", "The promotion receipt lacks the verified evidence a navigation handoff needs."];
  }
  return null;
}

/**
 * The ONE predicate for "may this stop hand off to native navigation". The list
 * row, the card, the HTML and buildNativeNavLink all read it, so they cannot
 * disagree about whether Navigate is enabled or why it is not.
 */
export function handoffEligibility(state, stop, { promotion_receipt = null, user_ref = null } = {}) {
  const pin = classifyPin(stop.position);
  if (!pin.navigable) return { available: false, reason_code: "pin_not_entrance_approved", reason: pin.reason };
  const problem = receiptProblem(state, promotion_receipt);
  if (problem) return { available: false, reason_code: problem[0], reason: problem[1] };
  if (!isText(user_ref)) {
    return { available: false, reason_code: "user_binding_missing", reason: "A signed-in user is required to hand off navigation." };
  }
  return { available: true, reason_code: null, reason: null };
}

const windowOf = stop => (stop.appointment ? { ...stop.appointment } : null);

/** Derive every surface from the one route version. */
export function projectRoute(state, { prefersReducedMotion = false, promotion_receipt = null, user_ref = null } = {}) {
  const selectedStop = state.route.stops.find(stop => stop.property_id === state.selected_property_id) ?? null;
  const rows = state.route.stops.map(stop => ({
    stop,
    pin: classifyPin(stop.position),
    current: stop.route_stop_id === state.current_route_stop_id,
    navigation: handoffEligibility(state, stop, { promotion_receipt, user_ref }),
  }));
  const markers = rows.map(({ stop, pin, current }) => ({
    route_stop_id: stop.route_stop_id,
    property_id: stop.property_id,
    label: stop.route_label,
    route_sequence: stop.route_sequence,
    shape: stop.locked_state === "locked" ? "circle" : "diamond",
    display: pin.display,
    precision_label: pin.precision_label,
    position: stop.position && pin.display !== "unknown"
      ? { latitude: stop.position.latitude, longitude: stop.position.longitude } : null,
    selected: stop.property_id === state.selected_property_id,
    current,
    accessible_name: `Stop ${stop.route_label}, ${stop.title}, ${stop.locked_state} stop, ${pin.precision_label}`,
  }));
  const list = rows.map(({ stop, pin, current, navigation }) => ({
    route_stop_id: stop.route_stop_id,
    property_id: stop.property_id,
    label: stop.route_label,
    route_sequence: stop.route_sequence,
    title: stop.title,
    locked_state: stop.locked_state,
    dwell_minutes: stop.dwell_minutes,
    buffer_minutes: stop.buffer_minutes,
    appointment: windowOf(stop),
    display: pin.display,
    precision_label: pin.precision_label,
    selected: stop.property_id === state.selected_property_id,
    current,
    pin_eligible: pin.navigable,
    native_navigation: { ...navigation },
  }));
  const storySections = rows.map(({ stop, current }) => ({
    route_stop_id: stop.route_stop_id,
    property_id: stop.property_id,
    label: stop.route_label,
    route_sequence: stop.route_sequence,
    title: stop.title,
    active: current,
  }));
  const offline = rows.map(({ stop, pin }) => ({
    route_stop_id: stop.route_stop_id,
    property_id: stop.property_id,
    label: stop.route_label,
    route_sequence: stop.route_sequence,
    title: stop.title,
    address_line: stop.address_line,
    locked_state: stop.locked_state,
    appointment: windowOf(stop),
    dwell_minutes: stop.dwell_minutes,
    buffer_minutes: stop.buffer_minutes,
    display: pin.display,
    coordinate_card: pin.navigable
      ? { latitude: stop.position.latitude, longitude: stop.position.longitude, basis: "approved_access_point" }
      : null,
  }));
  const selectedRow = rows.find(({ stop }) => stop === selectedStop);
  const card = selectedRow ? {
    route_stop_id: selectedStop.route_stop_id,
    property_id: selectedStop.property_id,
    label: selectedStop.route_label,
    route_sequence: selectedStop.route_sequence,
    title: selectedStop.title,
    address_line: selectedStop.address_line,
    locked_state: selectedStop.locked_state,
    appointment: windowOf(selectedStop),
    dwell_minutes: selectedStop.dwell_minutes,
    buffer_minutes: selectedStop.buffer_minutes,
    display: selectedRow.pin.display,
    precision_label: selectedRow.pin.precision_label,
    native_navigation: { ...selectedRow.navigation },
  } : null;
  const currentStop = state.current_route_stop_id === null ? null : stopById(state, state.current_route_stop_id);
  const next = currentStop ? rows.find(({ stop }) => stop.route_sequence > currentStop.route_sequence) : null;
  return deepFreeze({
    tour_id: state.tour_id,
    projection_id: state.projection_id,
    route_version: state.route_version,
    mode: state.mode,
    markers, list, card,
    story_sections: storySections,
    offline_itinerary: offline,
    route_endpoints: { start_point: state.route.start_point, end_point: state.route.end_point },
    next_stop_id: next ? next.stop.route_stop_id : null,
    exclusions: rows.filter(({ pin }) => !pin.navigable).map(({ stop, pin }) => ({
      route_stop_id: stop.route_stop_id, display: pin.display, reason: pin.reason,
    })),
    camera_plan: cameraPlan({ prefersReducedMotion }),
  });
}

const tuple = item => JSON.stringify([item.route_sequence, item.route_stop_id, item.property_id, item.label]);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** Prove every surface agrees on order, identity, selection and navigation; name any that does not. */
export function checkParity(projection) {
  const divergences = [];
  const flag = (surface, expected, actual) => divergences.push({ surface, expected, actual });
  const reference = projection.markers.map(tuple);
  for (const surface of ["list", "story_sections", "offline_itinerary"]) {
    const seen = projection[surface].map(tuple);
    if (!same(seen, reference)) flag(surface, reference, seen);
  }
  const sequences = projection.markers.map(item => item.route_sequence);
  if (sequences.some((value, index) => index > 0 && value <= sequences[index - 1])) {
    flag("markers", "strictly ascending route_sequence", sequences);
  }
  // Fields the list repeats from the markers.
  for (const field of ["selected", "current", "display"]) {
    const a = projection.markers.map(item => item[field]);
    const b = projection.list.map(item => item[field]);
    if (!same(a, b)) flag(`list.${field}`, a, b);
  }
  const active = projection.story_sections.map(item => item.active);
  const current = projection.markers.map(item => item.current);
  if (!same(active, current)) flag("story_sections.active", current, active);
  for (const [field, fromList] of [["title", projection.list], ["title", projection.story_sections]]) {
    const a = projection.offline_itinerary.map(item => item[field]);
    const b = fromList.map(item => item[field]);
    if (!same(a, b)) flag(`${field} across surfaces`, a, b);
  }
  if (!same(projection.offline_itinerary.map(item => item.appointment), projection.list.map(item => item.appointment))) {
    flag("appointment", projection.list.map(item => item.appointment), projection.offline_itinerary.map(item => item.appointment));
  }
  // The card must be the selected stop, and only the selected stop.
  const selected = projection.markers.filter(item => item.selected);
  if (selected.length > 1) flag("markers.selected", "at most one", selected.length);
  if (selected.length === 1 && !projection.card) flag("card", "a card for the selected stop", null);
  if (selected.length === 0 && projection.card) flag("card", "no card without a selected stop", projection.card.route_stop_id);
  if (projection.card && selected.length === 1) {
    const marker = selected[0];
    const row = projection.list.find(item => item.route_stop_id === projection.card.route_stop_id);
    const wanted = [marker.route_sequence, marker.route_stop_id, marker.property_id, marker.label];
    const got = [projection.card.route_sequence, projection.card.route_stop_id, projection.card.property_id, projection.card.label];
    if (!same(wanted, got)) flag("card", wanted, got);
    if (!row) {
      flag("card", "a matching list row", null);
    } else {
      if (projection.card.title !== row.title) flag("card.title", row.title, projection.card.title);
      if (!same(projection.card.native_navigation, row.native_navigation)) {
        flag("card.native_navigation", row.native_navigation, projection.card.native_navigation);
      }
      if (!same(projection.card.appointment, row.appointment)) flag("card.appointment", row.appointment, projection.card.appointment);
    }
  }
  return { ok: divergences.length === 0, divergences };
}

function assertBounds(bounds) {
  if (!Array.isArray(bounds) || bounds.length !== 4) fail("invalid_bounds", "bounds must be [west,south,east,north]");
  const [west, south, east, north] = bounds;
  finite(west, -180, 180, "bounds.west"); finite(south, -90, 90, "bounds.south");
  finite(east, -180, 180, "bounds.east"); finite(north, -90, 90, "bounds.north");
  // Longitude may wrap across the antimeridian (west > east); latitude may not be reversed.
  if (south >= north) fail("invalid_bounds", "bounds.south must be below bounds.north");
}

function assertPosition(position, path) {
  if (!Array.isArray(position) || position.length < 2 || position.length > 3) {
    fail("invalid_geometry", `${path} must be a [longitude, latitude] position`);
  }
  try {
    finite(position[0], -180, 180, `${path}[0]`); finite(position[1], -90, 90, `${path}[1]`);
    if (position.length === 3 && (typeof position[2] !== "number" || !Number.isFinite(position[2]))) {
      fail("invalid_geometry", `${path}[2] must be a finite altitude`);
    }
  } catch { fail("invalid_geometry", `${path} holds an unusable coordinate`); }
}

function assertPolygon(rings, path) {
  if (!Array.isArray(rings) || rings.length < 1 || Object.keys(rings).length !== rings.length) fail("invalid_geometry", `${path} needs complete rings`);
  rings.forEach((ring, index) => {
    if (!Array.isArray(ring) || ring.length < 4 || Object.keys(ring).length !== ring.length) fail("invalid_geometry", `${path}[${index}] needs at least four complete positions`);
    ring.forEach((position, at) => assertPosition(position, `${path}[${index}][${at}]`));
    const first = ring[0]; const last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) fail("invalid_geometry", `${path}[${index}] must be closed`);
  });
}

function assertGeometry(geometry) {
  if (!isObject(geometry)) fail("invalid_geometry", "geometry must be a GeoJSON Polygon or MultiPolygon");
  if (geometry.type === "Polygon") return assertPolygon(geometry.coordinates, "geometry.coordinates");
  if (geometry.type === "MultiPolygon") {
    if (!Array.isArray(geometry.coordinates) || geometry.coordinates.length < 1 || Object.keys(geometry.coordinates).length !== geometry.coordinates.length) {
      fail("invalid_geometry", "a MultiPolygon needs at least one polygon");
    }
    return geometry.coordinates.forEach((rings, index) => assertPolygon(rings, `geometry.coordinates[${index}]`));
  }
  return fail("invalid_geometry", "geometry must be a GeoJSON Polygon or MultiPolygon");
}

function assertSliderValue(value) {
  const ok = (typeof value === "number" && Number.isFinite(value))
    || typeof value === "boolean" || (typeof value === "string" && value.length > 0 && value.length <= 200);
  if (!ok) fail("invalid_slider_value", "slider value must be a finite number, boolean or short string");
  return value;
}

/**
 * Typed events in, next state out. The effects record says the map instance is
 * kept: nothing here asks a caller to remount, rebuild or discard camera state.
 * Accepted payloads are copied, so later mutation of an event cannot change
 * state. A refused event throws before any state is returned.
 */
export function reduceMapEvent(state, event) {
  if (!isObject(event) || !MAP_EVENT_TYPES.includes(event.type)) {
    fail("unknown_event", `"${event && event.type}" is not a registered map event`);
  }
  if (event.route_version !== state.route_version) {
    fail("stale_route_version", `event is for route version ${event.route_version}, state is ${state.route_version}`,
      { event_route_version: event.route_version, state_route_version: state.route_version });
  }
  const next = structuredClone(state);
  switch (event.type) {
    case "mode_change":
      if (!MAP_MODES.includes(event.mode)) fail("unknown_mode", `"${event.mode}" is not a map mode`);
      next.mode = event.mode;
      break;
    case "bounds_change":
      assertBounds(event.bounds);
      next.camera = { ...next.camera, bounds: [...event.bounds] };
      break;
    case "feature_click":
    case "selected_record": {
      const property = text(event.property_id, "property_id");
      if (!next.route.stops.some(stop => stop.property_id === property)) {
        fail("unknown_property", `property "${property}" is not on route version ${state.route_version}`);
      }
      next.selected_property_id = property;
      break;
    }
    case "filter_state":
      if (!isObject(event.filters)) fail("invalid_shape", "filters must be an object");
      next.filters = clone(event.filters, "filters");
      break;
    case "slider_state":
      next.sliders = { ...next.sliders, [text(event.name, "name")]: assertSliderValue(event.value) };
      break;
    case "draw_result":
      assertGeometry(event.geometry);
      next.drawn_geometry = clone(event.geometry, "geometry");
      break;
    case "route_stop_change": {
      if (state.mode !== "tour") fail("tour_mode_required", "route progress only moves in Tour mode");
      const stop = stopById(state, event.route_stop_id);
      next.current_route_stop_id = stop.route_stop_id;
      next.selected_property_id = stop.property_id;
      break;
    }
    default:
      break;
  }
  return { state: next, effects: { remount: false, map_instance: "keep" } };
}

/** Move to a new route version, keeping identity-keyed state and recording the mapping. */
export function applyRouteVersion(state, route) {
  const next = buildRouteVersionState(route, { mode: state.mode });
  if (next.tour_id !== state.tour_id) fail("tour_mismatch", "a route version cannot change tour");
  if (next.route_version <= state.route_version) {
    fail("stale_route_version", "the new route version must be greater than the current one");
  }
  const oldByProperty = new Map(state.route.stops.map(stop => [stop.property_id, stop]));
  const newByProperty = new Map(next.route.stops.map(stop => [stop.property_id, stop]));
  const mapping = [];
  for (const old of state.route.stops) {
    const now = newByProperty.get(old.property_id);
    mapping.push({
      old_route_version: state.route_version, new_route_version: next.route_version, property_id: old.property_id,
      old_route_stop_id: old.route_stop_id, new_route_stop_id: now ? now.route_stop_id : null,
      old_route_sequence: old.route_sequence, new_route_sequence: now ? now.route_sequence : null,
      old_route_label: old.route_label, new_route_label: now ? now.route_label : null,
      disposition: !now ? "removed"
        : now.route_sequence === old.route_sequence && now.route_label === old.route_label ? "unchanged" : "resequenced",
    });
  }
  for (const now of next.route.stops) {
    if (!oldByProperty.has(now.property_id)) {
      mapping.push({
        old_route_version: state.route_version, new_route_version: next.route_version, property_id: now.property_id,
        old_route_stop_id: null, new_route_stop_id: now.route_stop_id,
        old_route_sequence: null, new_route_sequence: now.route_sequence,
        old_route_label: null, new_route_label: now.route_label, disposition: "added",
      });
    }
  }
  const carried = {
    ...next,
    selected_property_id: newByProperty.has(state.selected_property_id) ? state.selected_property_id : null,
    current_route_stop_id: null,
    camera: structuredClone(state.camera),
    filters: structuredClone(state.filters),
    sliders: structuredClone(state.sliders),
    drawn_geometry: structuredClone(state.drawn_geometry),
    version_mapping: mapping,
    lineage: [...structuredClone(state.lineage ?? []), {
      tour_id: state.tour_id,
      from_route_version: state.route_version, to_route_version: next.route_version,
      from_projection_id: state.projection_id, to_projection_id: next.projection_id,
      from_route_version_id: state.route_version_id, to_route_version_id: next.route_version_id,
      mapping: structuredClone(mapping),
    }],
  };
  const previousCurrent = state.current_route_stop_id
    ? state.route.stops.find(stop => stop.route_stop_id === state.current_route_stop_id) : null;
  if (previousCurrent && newByProperty.has(previousCurrent.property_id)) {
    carried.current_route_stop_id = newByProperty.get(previousCurrent.property_id).route_stop_id;
  }
  return { state: carried, mapping };
}

/** Build a native navigation link, or withhold it with a reason and an offline fallback. */
export function buildNativeNavLink(state, request) {
  const platform = request.platform;
  if (!NAV_PLATFORMS.includes(platform)) fail("unknown_platform", `"${platform}" is not a navigation platform`);
  const travelMode = request.travel_mode;
  if (!NAV_TRAVEL_MODES.includes(travelMode)) fail("unknown_travel_mode", `"${travelMode}" is not a travel mode`);
  const stop = stopById(state, request.route_stop_id);
  const pin = classifyPin(stop.position);
  const fallback = {
    route_stop_id: stop.route_stop_id, label: stop.route_label, address_line: stop.address_line,
    coordinate_card: pin.navigable
      ? { latitude: stop.position.latitude, longitude: stop.position.longitude, basis: "approved_access_point" } : null,
  };
  const base = { route_stop_id: stop.route_stop_id, route_version: state.route_version, fallback };
  const eligibility = handoffEligibility(state, stop, {
    promotion_receipt: request.promotion_receipt, user_ref: request.user_ref,
  });
  if (!eligibility.available) return { ...base, available: false, reason_code: eligibility.reason_code, reason: eligibility.reason };
  const { latitude, longitude } = stop.position;
  const link = platform === "apple_maps"
    ? `https://maps.apple.com/?daddr=${latitude},${longitude}&dirflg=${travelMode === "walking" ? "w" : "d"}`
    : `https://www.google.com/maps/dir/?api=1&destination=${latitude},${longitude}&travelmode=${travelMode}`;
  const generated = Date.parse(request.now);
  if (!Number.isFinite(generated)) fail("invalid_time", "now must be an ISO timestamp");
  return {
    ...base, available: true, platform, travel_mode: travelMode, link,
    return_state: {
      tour_id: state.tour_id, projection_id: state.projection_id, route_version_id: state.route_version_id,
      route_stop_id: stop.route_stop_id, property_id: stop.property_id,
      route_version: state.route_version, user_ref: request.user_ref,
      generated_at: new Date(generated).toISOString(), expires_at: new Date(generated + RETURN_TTL_MS).toISOString(),
    },
  };
}

/** The marker the client persists before leaving for the native app. */
export function buildReturnState(handoff) {
  if (!handoff || handoff.available !== true || !handoff.return_state) {
    fail("no_handoff", "a return marker exists only for an available handoff");
  }
  return structuredClone(handoff.return_state);
}

function validMarker(marker) {
  return isObject(marker)
    && ["tour_id", "projection_id", "route_version_id", "route_stop_id", "property_id", "user_ref"].every(key => isText(marker[key]))
    && Number.isSafeInteger(marker.route_version) && marker.route_version >= 1
    && Number.isFinite(Date.parse(marker.generated_at)) && Number.isFinite(Date.parse(marker.expires_at));
}

/**
 * On return, restore the exact stop, or say why not and fall back to the ordered
 * list. A marker from an earlier route version is followed only through the
 * recorded stop transitions in `state.lineage`; a stop that was removed is never
 * guessed back from its property identity. The marker itself is not signed here:
 * server-side signing of the return token is still owed.
 */
export function resolveReturn(state, marker, { now, user_ref = null } = {}) {
  const refuse = code => ({ ok: false, reason_code: code, fallback: "ordered_list" });
  if (!validMarker(marker)) return refuse("return_marker_invalid");
  if (marker.tour_id !== state.tour_id) return refuse("return_tour_mismatch");
  if (!isText(user_ref) || marker.user_ref !== user_ref) return refuse("return_user_mismatch");
  if (!(Date.parse(now) < Date.parse(marker.expires_at))) return refuse("return_expired");
  if (marker.route_version > state.route_version) return refuse("return_version_future");
  let stopId = marker.route_stop_id;
  let projectionId = marker.projection_id;
  let routeVersionId = marker.route_version_id;
  let note = null;
  if (marker.route_version < state.route_version) {
    let version = marker.route_version;
    while (version < state.route_version) {
      const steps = (state.lineage ?? []).filter(item => item.tour_id === state.tour_id
        && item.from_route_version === version && item.from_projection_id === projectionId
        && item.from_route_version_id === routeVersionId);
      if (steps.length !== 1) return refuse("return_version_unbound");
      const step = steps[0];
      if (!Number.isSafeInteger(step.to_route_version) || step.to_route_version <= version
        || step.to_route_version > state.route_version || !isText(step.to_projection_id)
        || !isText(step.to_route_version_id) || !Array.isArray(step.mapping)) return refuse("return_version_unbound");
      const entries = step.mapping.filter(item => item.old_route_stop_id === stopId && item.property_id === marker.property_id
        && item.old_route_version === version && item.new_route_version === step.to_route_version);
      if (entries.length !== 1) return refuse("return_version_unbound");
      const entry = entries[0];
      if (entry.disposition === "removed") return refuse("return_stop_removed");
      if (!["unchanged", "resequenced"].includes(entry.disposition) || !isText(entry.new_route_stop_id)) return refuse("return_version_unbound");
      stopId = entry.new_route_stop_id;
      projectionId = step.to_projection_id;
      routeVersionId = step.to_route_version_id;
      version = step.to_route_version;
    }
    if (version !== state.route_version) return refuse("return_version_unbound");
    note = "route_version_changed";
  }
  if (projectionId !== state.projection_id || routeVersionId !== state.route_version_id) return refuse("return_version_unbound");
  const stop = state.route.stops.find(item => item.route_stop_id === stopId && item.property_id === marker.property_id);
  if (!stop) return refuse(note ? "return_stop_removed" : "return_marker_mismatch");
  return {
    ok: true, route_stop_id: stop.route_stop_id, note,
    state: { ...structuredClone(state), mode: "tour", current_route_stop_id: stop.route_stop_id, selected_property_id: stop.property_id },
  };
}

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ESCAPES[char]);

/** Tile-free ordered list. Usable when the map fails to load or tiles are offline. */
export function renderOrderedListHtml(projection) {
  const items = projection.list.map(item => {
    const offline = projection.offline_itinerary.find(entry => entry.route_stop_id === item.route_stop_id);
    const notice = item.display === "verified" ? "" : ` <span class="tour-pin-note">${esc(item.precision_label)}</span>`;
    const when = item.appointment
      ? ` <span class="tour-appointment">Appointment ${esc(item.appointment.start)} to ${esc(item.appointment.end)}</span>` : "";
    const nav = item.native_navigation.available ? ""
      : ` <span class="tour-nav-withheld">Navigation not available: ${esc(item.native_navigation.reason)}</span>`;
    return `<li data-route-stop-id="${esc(item.route_stop_id)}"${item.current ? ' aria-current="step"' : ""}>`
      + `<strong>${esc(item.label)}</strong> ${esc(item.title)}`
      + (offline && offline.address_line ? `, ${esc(offline.address_line)}` : "")
      + ` <span class="tour-durations">${esc(item.dwell_minutes ?? "unknown")} min dwell; ${esc(item.buffer_minutes ?? "unknown")} min buffer</span>`
      + notice + when + nav + "</li>";
  });
  return `<ol class="tour-stop-list" aria-label="Tour stops in visit order">${items.join("")}</ol>`;
}

/** Start and end assumptions for the same ordered list, kept out of the stop count. */
export function renderRouteEndpointsHtml(projection) {
  const { start_point: start, end_point: end } = projection.route_endpoints;
  const part = (name, point) => (point
    ? `<dt>${name}</dt><dd>${esc(point.label ?? point.source_ref ?? "Unnamed point")} (${esc(point.latitude)}, ${esc(point.longitude)})</dd>` : "");
  return `<dl class="tour-route-endpoints">${part("Start", start)}${part("End", end)}</dl>`;
}
