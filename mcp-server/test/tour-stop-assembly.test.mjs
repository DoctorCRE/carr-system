import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createTourInternalWebHandler, isTourInternalRequest } from "../src/tour-internal-web.js";
import { tourDomainTools } from "../src/tour-domain.js";
import { ToolError } from "../src/tool-error.js";

// Slice 2 (create a Tour and assemble its stops). Synthetic identifiers only.
const ORIGIN = "https://app.doctorcre.com";
const ENV = { APP_HOST: "app.doctorcre.com" };
const ACTOR = { id: "partner-actor" };
const SESSION = { key: "opaque-server-session", csrfToken: "csrf-value" };
const id = n => `${String(n).repeat(8)}-${String(n).repeat(4)}-4${String(n).repeat(3)}-8${String(n).repeat(3)}-${String(n).repeat(12)}`;
const [tourId, baseRoute, draftRoute, propA, propB, propC, stopA, stopB, key] = [1, 2, 3, 4, 5, 6, 7, 8, 9].map(id);
const digest = `sha256:${"a".repeat(64)}`;
const start = { latitude: 30.5, longitude: -87.2, position_role: "start", precision_class: "approximate", source_ref: "fixture:start" };
const end = { ...start, position_role: "end", source_ref: "fixture:end" };
const headers = { origin: ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json", "x-carr-csrf": SESSION.csrfToken };

const createBody = { idempotency_key: key, tour_name: "Synthetic Tour", subject_type: "client", subject_id: "client:fixture-1", canonical_dataset_version: "fixture-v1", start_point: start, end_point: end };
const draftBody = { idempotency_key: key, tour_id: tourId, route_version: 2, base_route_version_id: baseRoute, expected_route_version: 1, start_point: start, end_point: end };
const stopBody = { idempotency_key: key, route_version_id: draftRoute, property_id: propA, route_sequence: 1, route_label: "Stop 1", stop_state: "active",
  appointment_start: "2026-10-05T14:00:00.000Z", appointment_end: "2026-10-05T14:45:00.000Z", locked_appointment: true, dwell_minutes: 45, buffer_minutes: 15,
  access_coordinate_status: "approved", assertion_set_digest: digest };
const transitionBody = { idempotency_key: key, old_route_version_id: baseRoute, new_route_version_id: draftRoute, old_route_stop_id: stopA, new_route_stop_id: stopB, disposition: "reordered" };

const ROUTES = [
  ["/api/tours/create", "createTourFn", createBody],
  ["/api/tours/route-draft", "openRouteDraftFn", draftBody],
  ["/api/tours/route-stop", "appendRouteStopFn", stopBody],
  ["/api/tours/route-stop-transition", "appendRouteStopTransitionFn", transitionBody],
];
function post(path, body) { return new Request(`${ORIGIN}${path}`, { method: "POST", headers, body: JSON.stringify(body) }); }
function surface(overrides = {}) {
  const ok = async () => ({ ok: true, data: { saved: true } });
  return createTourInternalWebHandler({ ...Object.fromEntries(ROUTES.map(([, seam]) => [seam, ok])), ...overrides });
}
const fetchWith = (s, req, actor = ACTOR, session = SESSION) => s.fetch(req, ENV, {}, actor, session);

for (const [path, seam, body] of ROUTES) {
  test(`${path}: authenticated, CSRF-bound, exact-shape, and passes the actor untouched`, async () => {
    const seen = [];
    const s = surface({ [seam]: async context => { seen.push(context); return { ok: true, data: { saved: true } }; } });
    assert.equal(isTourInternalRequest(post(path, body)), true);
    assert.equal((await s.fetch(post(path, body), ENV, {}, undefined, undefined)).status, 401);
    const noCsrf = new Request(`${ORIGIN}${path}`, { method: "POST", headers: { ...headers, "x-carr-csrf": "wrong" }, body: JSON.stringify(body) });
    assert.equal((await fetchWith(s, noCsrf)).status, 403);
    assert.equal((await fetchWith(s, new Request(`${ORIGIN}${path}`, { method: "GET" }))).status, 405);
    assert.equal(seen.length, 0, "refused requests never reach the seam");
    assert.equal((await fetchWith(s, post(path, body))).status, 200);
    assert.deepEqual(seen[0].input, body);
    assert.deepEqual(seen[0].actor, ACTOR);
    for (const bad of [{ ...body, tenant_id: "other" }, { ...body, actor: "x" }, { ...body, extra: 1 }, { ...body, idempotency_key: "nope" }]) {
      assert.equal((await fetchWith(s, post(path, bad))).status, 400, JSON.stringify(Object.keys(bad)));
    }
    assert.equal(seen.length, 1);
  });
}

test("create refuses a bad subject type, a missing point, a wrong point role and a phase or deal field", async () => {
  const s = surface();
  for (const bad of [
    { ...createBody, subject_type: "deal" }, { ...createBody, subject_id: "has space" }, { ...createBody, tour_name: "  " },
    { ...createBody, start_point: null }, { ...createBody, start_point: end }, { ...createBody, end_point: { ...end, latitude: 91 } },
    { ...createBody, deal_phase: "touring" }, { ...createBody, assignment_id: id(5) },
  ]) assert.equal((await fetchWith(s, post("/api/tours/create", bad))).status, 400, JSON.stringify(bad).slice(0, 80));
});

test("stop edit keeps property identity apart from the route label and enforces state rules", async () => {
  const s = surface();
  const held = { ...stopBody, stop_state: "held", route_sequence: null, route_label: null, locked_appointment: false, appointment_start: null, appointment_end: null };
  const excluded = { ...held, stop_state: "excluded" };
  for (const okBody of [stopBody, held, excluded]) assert.equal((await fetchWith(s, post("/api/tours/route-stop", okBody))).status, 200);
  for (const bad of [
    { ...stopBody, route_label: null }, { ...stopBody, route_sequence: null }, { ...stopBody, route_label: propA + "!" },
    { ...held, route_sequence: 2 }, { ...held, route_label: "Stop 2" },
    { ...stopBody, property_id: "Stop 1" }, { ...stopBody, dwell_minutes: -1 }, { ...stopBody, buffer_minutes: 1441 },
    { ...stopBody, appointment_end: null }, { ...stopBody, appointment_end: "2026-10-05T13:00:00.000Z" },
    { ...stopBody, appointment_start: null, appointment_end: null, locked_appointment: true },
    { ...stopBody, locked_appointment: "yes" }, { ...stopBody, stop_state: "hidden" }, { ...stopBody, access_coordinate_status: "guess" },
    { ...stopBody, assertion_set_digest: "sha256:short" },
  ]) assert.equal((await fetchWith(s, post("/api/tours/route-stop", bad))).status, 400, JSON.stringify(bad).slice(0, 120));
});

test("stop transitions must name every changed order or exclusion with a legal disposition shape", async () => {
  const s = surface();
  const good = [
    transitionBody,
    { ...transitionBody, disposition: "added", old_route_version_id: null, old_route_stop_id: null },
    { ...transitionBody, disposition: "removed", new_route_stop_id: null },
    { ...transitionBody, disposition: "excluded", new_route_stop_id: stopB },
    { ...transitionBody, disposition: "held", new_route_stop_id: null },
  ];
  for (const b of good) assert.equal((await fetchWith(s, post("/api/tours/route-stop-transition", b))).status, 200, b.disposition);
  for (const bad of [
    { ...transitionBody, disposition: "added" }, { ...transitionBody, disposition: "removed" }, { ...transitionBody, disposition: "unchanged", new_route_stop_id: null },
    { ...transitionBody, disposition: "teleported" }, { ...transitionBody, disposition: "reordered", old_route_stop_id: null },
  ]) assert.equal((await fetchWith(s, post("/api/tours/route-stop-transition", bad))).status, 400, bad.disposition);
});


const refusal = async (path, seam, body, message) => {
  const s = createTourInternalWebHandler({ [seam]: async () => { throw new Error(message); } });
  return fetchWith(s, post(path, body));
};
test("stale versions, accepted-version edits and acceptance preconditions refuse as 409 using the real SQL messages", async () => {
  const cases = [
    ["/api/tours/route-draft", "openRouteDraftFn", draftBody, "route version refuses concurrent or stale route state"],
    ["/api/tours/route-draft", "openRouteDraftFn", draftBody, "route version base is invalid"],
    ["/api/tours/route-stop", "appendRouteStopFn", stopBody, "route stop cannot alter an accepted route version"],
    ["/api/tours/route-stop-transition", "appendRouteStopTransitionFn", transitionBody, "route transition cannot alter an accepted route version"],
  ];
  for (const [path, seam, body, message] of cases) {
    const response = await refusal(path, seam, body, message);
    assert.equal(response.status, 409, message);
    assert.deepEqual(await response.json(), { error: "conflict" });
  }
});

test("unknown failures stay a coarse 503 with no detail leaked or reported", async () => {
  const reports = [];
  const s = createTourInternalWebHandler({ appendRouteStopFn: async () => { throw new Error("connect ECONNRESET db.internal.example:5432"); }, reportFailureFn: r => reports.push(r) });
  const response = await fetchWith(s, post("/api/tours/route-stop", stopBody));
  assert.equal(response.status, 503);
  assert.doesNotMatch(await response.text(), /ECONNRESET|db\.internal/);
  assert.ok(reports.every(r => !JSON.stringify(r).includes("db.internal")));
});

test("typed domain validation refusals are 400, never a retryable outage", async () => {
  for (const code of ["tour_point_invalid", "tour_input_invalid", "tour_stop_state_invalid", "tour_appointment_invalid", "tour_transition_invalid"]) {
    const s = createTourInternalWebHandler({ createTourFn: async () => { throw new ToolError({ error: code }); } });
    const response = await fetchWith(s, post("/api/tours/create", createBody));
    assert.equal(response.status, 400, code);
    assert.deepEqual(await response.json(), { error: "invalid_request" });
  }
});

test("the door agrees with the real domain point policy, so a forbidden reference is refused as 400 before any write", async () => {
  const tools = tourDomainTools({ withEnvelope: async (_c, _a, _v, _x, fn) => fn(), writeEvent: async () => {}, ToolError });
  const domain = async body => {
    try { await tools["create-tour-domain"].handler({ query: async () => ({ rows: [{ tour_id: tourId }] }) }, { id: "partner-actor" }, body); return "accepted"; }
    catch (error) { return error?.payload?.error === "tour_point_invalid" ? "refused" : `other:${error?.payload?.error || error?.message}`; }
  };
  const s = surface();
  for (const ref of ["fixture:start", "parcel:123", "a@b.test", "Contact sheet", "PHONE-1", "internal-note", "client-file", "x".repeat(501), "  "]) {
    const body = { ...createBody, start_point: { ...start, source_ref: ref } };
    const door = (await fetchWith(s, post("/api/tours/create", body))).status === 200 ? "accepted" : "refused";
    const verdict = await domain(body);
    assert.equal(door, verdict === "accepted" ? "accepted" : "refused", `door vs domain for ${JSON.stringify(ref.slice(0, 20))}`);
    if (ref.includes("@")) assert.equal((await fetchWith(s, post("/api/tours/route-draft", { ...draftBody, end_point: { ...end, source_ref: ref } }))).status, 400);
  }
});

// An in-memory stand-in shaped like the SQL in migration 0429: create also writes route 1; a
// draft must follow an ACCEPTED base at the tour's current version; accepting needs an active
// stop, an "added" (or other) transition for every new stop and a disposition for every prior
// stop; accepted versions take no stop or transition writes; a key replayed with a different
// payload is refused. It exists so the browser-facing sequence can run offline; the verbs'
// own SQL tests remain the proof of database behavior.
function fakeDomain() {
  const db = { tour: null, routes: [], stops: new Map(), transitions: [], replays: new Map(), phase: "none", seq: 0 };
  const uid = () => { db.seq += 1; return `${String(db.seq).padStart(8, "0")}-0000-4000-8000-000000000000`; };
  const refuse = message => { throw new Error(message); };
  const once = (input, run) => {
    const hash = JSON.stringify(input);
    const prior = db.replays.get(input.idempotency_key);
    if (prior) { if (prior.hash !== hash) throw new ToolError({ error: "key_reuse" }); return prior.result; }
    const result = { ok: true, data: run() }; db.replays.set(input.idempotency_key, { hash, result }); return result;
  };
  const route = rid => db.routes.find(r => r.id === rid) || refuse("route stop route version is unavailable");
  const seams = {
    createTourFn: async ({ input }) => once(input, () => {
      db.tour = { id: tourId, route_version: 1, subject_type: input.subject_type, subject_id: input.subject_id, status: "draft" };
      db.routes.push({ id: uid(), route_version: 1, base: null, accepted: false });
      return { tour_id: tourId };
    }),
    openRouteDraftFn: async ({ input }) => once(input, () => {
      if (input.expected_route_version !== db.tour.route_version || input.route_version !== db.routes.length + 1) refuse("route version refuses concurrent or stale route state");
      const base = db.routes.find(r => r.id === input.base_route_version_id);
      if (!base || !base.accepted || base.route_version !== db.tour.route_version) refuse("route version base is invalid");
      const created = { id: uid(), route_version: input.route_version, base: base.id, accepted: false }; db.routes.push(created);
      return { route_version_id: created.id };
    }),
    appendRouteStopFn: async ({ input }) => once(input, () => {
      if (route(input.route_version_id).accepted) refuse("route stop cannot alter an accepted route version");
      const s = { ...input, id: uid() }; db.stops.set(s.id, s); return { route_stop_id: s.id };
    }),
    appendRouteStopTransitionFn: async ({ input }) => once(input, () => {
      if (route(input.new_route_version_id).accepted) refuse("route transition cannot alter an accepted route version");
      // Same guards as append_tour_route_stop_transition (0429:155-157).
      const [o, n] = [db.stops.get(input.old_route_stop_id), db.stops.get(input.new_route_stop_id)];
      const both = ["unchanged", "reordered"].includes(input.disposition) || (["held", "excluded"].includes(input.disposition) && n);
      if (both && o.property_id !== n.property_id) refuse("route transition property identity mismatch");
      if (input.disposition === "unchanged" && o.route_sequence !== n.route_sequence) refuse("unchanged route transition requires the same sequence");
      if (input.disposition === "reordered" && o.route_sequence === n.route_sequence) refuse("reordered route transition requires a sequence change");
      db.transitions.push(input); return { route_stop_transition_id: uid() };
    }),
    acceptRouteVersionFn: async ({ input }) => once(input, () => {
      const r = route(input.route_version_id);
      const prior = db.routes.filter(x => x.accepted).at(-1);
      if (input.expected_prior_route_version !== (prior?.route_version ?? 0) || (prior ? r.base !== prior.id : r.base !== null)) refuse("route acceptance refuses concurrent or stale route state");
      const mine = [...db.stops.values()].filter(s => s.route_version_id === r.id);
      if (!mine.some(s => s.stop_state === "active")) refuse("route acceptance requires at least one active stop");
      if (mine.some(s => !db.transitions.some(t => t.new_route_version_id === r.id && t.new_route_stop_id === s.id))) refuse("route acceptance requires an explicit transition for every new route stop");
      if (prior && [...db.stops.values()].filter(s => s.route_version_id === prior.id).some(o => !db.transitions.some(t => t.new_route_version_id === r.id && t.old_route_stop_id === o.id))) refuse("route acceptance requires an explicit disposition for every prior route stop");
      r.accepted = true; db.tour.route_version = r.route_version; return { accepted: true };
    }),
    readTourFn: async () => ({ ok: true, data: { deal_phase: db.phase, route_version: db.tour?.route_version, routes: [...db.routes].reverse().map(r => ({ id: r.id, route_version: r.route_version, accepted: r.accepted,
      stops: [...db.stops.values()].filter(s => s.route_version_id === r.id).map(({ id: stopId, property_id, route_label, stop_state, route_sequence, dwell_minutes, buffer_minutes, locked_appointment }) => ({ id: stopId, property_id, route_label, stop_state, route_sequence, dwell_minutes, buffer_minutes, locked_appointment })) })) } }),
  };
  return { db, seams };
}
const data = async response => (await response.json()).data;
const k = n => `${String(n).padStart(8, "0")}-1111-4111-8111-111111111111`;
const held = (extra = {}) => ({ route_sequence: null, route_label: null, locked_appointment: false, appointment_start: null, appointment_end: null, ...extra });
async function send(s, path, body, expected = 200) { const r = await fetchWith(s, post(path, body)); assert.equal(r.status, expected, `${path} -> ${r.status}`); return r; }
const detail = async s => data(await fetchWith(s, new Request(`${ORIGIN}/api/tours/detail?tour_id=${tourId}`)));

test("lifecycle: create (route 1 exists) -> stops -> transitions -> accept -> revised draft, with held and excluded stops and no phase change", async () => {
  const { db, seams } = fakeDomain();
  const s = createTourInternalWebHandler(seams);
  await send(s, "/api/tours/create", { ...createBody, idempotency_key: k(1) });
  const first = (await detail(s)).routes[0];
  assert.equal(first.route_version, 1, "creation already wrote the initial route");
  // The initial route is not a base yet, and route 1 cannot be opened again.
  await send(s, "/api/tours/route-draft", { ...draftBody, tour_id: tourId, route_version: 1, base_route_version_id: first.id, expected_route_version: 0, idempotency_key: k(2) }, 409);
  await send(s, "/api/tours/route-draft", { ...draftBody, route_version: 2, base_route_version_id: first.id, expected_route_version: 1, idempotency_key: k(3) }, 409);
  // Accepting an empty route is refused; the editor is told to fix its state.
  await send(s, "/api/tours/route-accept", { route_version_id: first.id, expected_prior_route_version: 0, acceptance_digest: digest, idempotency_key: k(4) }, 409);
  const rows = [
    { ...stopBody, idempotency_key: k(5), route_version_id: first.id, property_id: propA, route_sequence: 1, route_label: "First", dwell_minutes: 30 },
    { ...stopBody, ...held(), idempotency_key: k(6), route_version_id: first.id, property_id: propB, stop_state: "held" },
    { ...stopBody, ...held(), idempotency_key: k(7), route_version_id: first.id, property_id: propC, stop_state: "excluded" },
  ];
  const stopIds = [];
  for (const r of rows) stopIds.push((await data(await send(s, "/api/tours/route-stop", r))).route_stop_id);
  // A stop without its transition still blocks acceptance.
  await send(s, "/api/tours/route-accept", { route_version_id: first.id, expected_prior_route_version: 0, acceptance_digest: digest, idempotency_key: k(8) }, 409);
  for (const [n, sid] of stopIds.entries()) await send(s, "/api/tours/route-stop-transition", { idempotency_key: k(10 + n), old_route_version_id: null, new_route_version_id: first.id, old_route_stop_id: null, new_route_stop_id: sid, disposition: "added" });
  await send(s, "/api/tours/route-accept", { route_version_id: first.id, expected_prior_route_version: 0, acceptance_digest: digest, idempotency_key: k(20) });
  // Accepted versions take no further stop or transition writes.
  await send(s, "/api/tours/route-stop", { ...rows[0], idempotency_key: k(21) }, 409);
  await send(s, "/api/tours/route-stop-transition", { idempotency_key: k(22), old_route_version_id: null, new_route_version_id: first.id, old_route_stop_id: null, new_route_stop_id: stopIds[0], disposition: "added" }, 409);
  // Revised draft: every prior stop needs an explicit disposition before acceptance.
  const draft = (await data(await send(s, "/api/tours/route-draft", { ...draftBody, route_version: 2, base_route_version_id: first.id, expected_route_version: 1, idempotency_key: k(23) }))).route_version_id;
  const moved = (await data(await send(s, "/api/tours/route-stop", { ...rows[0], idempotency_key: k(24), route_version_id: draft, route_sequence: 2, route_label: "Moved" }))).route_stop_id;
  await send(s, "/api/tours/route-stop-transition", { idempotency_key: k(25), old_route_version_id: first.id, new_route_version_id: draft, old_route_stop_id: stopIds[0], new_route_stop_id: moved, disposition: "reordered" });
  await send(s, "/api/tours/route-accept", { route_version_id: draft, expected_prior_route_version: 1, acceptance_digest: digest, idempotency_key: k(26) }, 409);
  await send(s, "/api/tours/route-stop-transition", { idempotency_key: k(27), old_route_version_id: first.id, new_route_version_id: draft, old_route_stop_id: stopIds[1], new_route_stop_id: null, disposition: "removed" });
  await send(s, "/api/tours/route-stop-transition", { idempotency_key: k(28), old_route_version_id: first.id, new_route_version_id: draft, old_route_stop_id: stopIds[2], new_route_stop_id: null, disposition: "removed" });
  await send(s, "/api/tours/route-accept", { route_version_id: draft, expected_prior_route_version: 1, acceptance_digest: digest, idempotency_key: k(29) });
  const reloaded = await detail(s);
  const v1 = reloaded.routes.find(r => r.route_version === 1);
  assert.deepEqual(v1.stops.map(x => [x.property_id, x.stop_state, x.route_label]), [[propA, "active", "First"], [propB, "held", null], [propC, "excluded", null]]);
  assert.ok(v1.stops.every(x => x.property_id !== x.route_label), "property ID is never the route label");
  assert.equal(v1.stops[0].dwell_minutes, 30);
  assert.equal(reloaded.route_version, 2);
  assert.equal(reloaded.deal_phase, "none", "assembly never touches an Assignment or Deal phase");
  assert.equal(db.transitions.length, 6, "every order change, hold, exclusion and removal has its own transition");
});

test("a reorder that keeps the sequence, or an unchanged that moves it, is refused as 400 and never recorded", async () => {
  const { db, seams } = fakeDomain();
  const s = createTourInternalWebHandler(seams);
  await send(s, "/api/tours/create", { ...createBody, idempotency_key: k(1) });
  const first = (await detail(s)).routes[0];
  const a = (await data(await send(s, "/api/tours/route-stop", { ...stopBody, idempotency_key: k(2), route_version_id: first.id, route_sequence: 1 }))).route_stop_id;
  await send(s, "/api/tours/route-stop-transition", { idempotency_key: k(3), old_route_version_id: null, new_route_version_id: first.id, old_route_stop_id: null, new_route_stop_id: a, disposition: "added" });
  await send(s, "/api/tours/route-accept", { route_version_id: first.id, expected_prior_route_version: 0, acceptance_digest: digest, idempotency_key: k(4) });
  const draft = (await data(await send(s, "/api/tours/route-draft", { ...draftBody, route_version: 2, base_route_version_id: first.id, expected_route_version: 1, idempotency_key: k(5) }))).route_version_id;
  const same = (await data(await send(s, "/api/tours/route-stop", { ...stopBody, idempotency_key: k(6), route_version_id: draft, route_sequence: 1 }))).route_stop_id;
  const moved = (await data(await send(s, "/api/tours/route-stop", { ...stopBody, idempotency_key: k(7), route_version_id: draft, route_sequence: 2 }))).route_stop_id;
  const link = (n, disposition, to) => ({ idempotency_key: k(n), old_route_version_id: first.id, new_route_version_id: draft, old_route_stop_id: a, new_route_stop_id: to, disposition });
  await send(s, "/api/tours/route-stop-transition", link(8, "reordered", same), 400);
  await send(s, "/api/tours/route-stop-transition", link(9, "unchanged", moved), 400);
  assert.equal(db.transitions.filter(t => t.new_route_version_id === draft).length, 0);
  await send(s, "/api/tours/route-stop-transition", link(10, "unchanged", same));
  await send(s, "/api/tours/route-stop-transition", link(11, "reordered", moved));
});

test("a key replayed with a changed payload is refused, and an identical replay returns the original result", async () => {
  const { db, seams } = fakeDomain();
  const s = createTourInternalWebHandler(seams);
  await send(s, "/api/tours/create", { ...createBody, idempotency_key: k(1) });
  const first = (await detail(s)).routes[0];
  const body = { ...stopBody, idempotency_key: k(2), route_version_id: first.id };
  const a = await data(await send(s, "/api/tours/route-stop", body));
  const b = await data(await send(s, "/api/tours/route-stop", body));
  assert.equal(a.route_stop_id, b.route_stop_id);
  await send(s, "/api/tours/route-stop", { ...body, dwell_minutes: 99 }, 409);
  assert.equal(db.stops.size, 1);
});

test("unknown outcome reconciles by replaying the same idempotency key without a duplicate stop", async () => {
  const { db, seams } = fakeDomain();
  await seams.createTourFn({ input: { ...createBody, idempotency_key: k(1) } });
  const first = db.routes[0];
  // The write commits but the response is lost; the browser cannot tell.
  let dropped = false;
  const s = createTourInternalWebHandler({ ...seams, appendRouteStopFn: async context => { const r = await seams.appendRouteStopFn(context); if (!dropped) { dropped = true; throw new Error("socket hang up"); } return r; } });
  const body = { ...stopBody, idempotency_key: k(2), route_version_id: first.id };
  await send(s, "/api/tours/route-stop", body, 503);
  assert.equal((await detail(s)).routes[0].stops.length, 1, "reload shows the write did commit");
  await send(s, "/api/tours/route-stop", body);
  assert.equal(db.stops.size, 1, "same key never appends a second stop");
});

test("runtime maps each route to the existing verb and adds no Assignment, Deal or route-store write", async () => {
  const runtime = await readFile(new URL("../src/tour-runtime.js", import.meta.url), "utf8");
  assert.match(runtime, /createTourFn: context => invoke\(context, "create-tour-domain"/);
  assert.match(runtime, /openRouteDraftFn: context => invoke\(context, "append-tour-route-version"/);
  assert.match(runtime, /appendRouteStopFn: context => invoke\(context, "append-tour-route-stop"/);
  assert.match(runtime, /appendRouteStopTransitionFn: context => invoke\(context, "append-tour-route-stop-transition"/);
  assert.match(runtime, /routing_source: "manual"/);
  assert.doesNotMatch(runtime, /assignment|deal_phase|advance-deal/i);
});
