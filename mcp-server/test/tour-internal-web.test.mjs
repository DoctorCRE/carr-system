import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createTourInternalWebHandler, isTourInternalRequest, TOUR_INTERNAL_ASSET_DIRECTORY } from "../src/tour-internal-web.js";

const ROOT = fileURLToPath(new URL("../../dealroom", import.meta.url));
const ORIGIN = "https://app.doctorcre.com";
const ACTOR = { id: "partner-actor" };
const SESSION = { key: "opaque-server-session", csrfToken: "csrf-value" };
const tourId = "11111111-1111-4111-8111-111111111111";
const routeId = "22222222-2222-4222-8222-222222222222";
const stopA = "33333333-3333-4333-8333-333333333333";
const stopB = "44444444-4444-4444-8444-444444444444";
const projectionId = "55555555-5555-4555-8555-555555555555";
const grantId = "66666666-6666-4666-8666-666666666666";
const digest = `sha256:${"a".repeat(64)}`;

class Assets {
  constructor() { this.paths = []; }
  async fetch(request) {
    const pathname = new URL(request.url).pathname;
    this.paths.push(pathname);
    try { return new Response(await readFile(`${ROOT}${pathname}`)); }
    catch { return new Response("missing", { status: 404 }); }
  }
}
function request(path, options = {}) { return new Request(`${ORIGIN}${path}`, options); }
function handler(overrides = {}) {
  const success = async () => ({ ok: true, data: { saved: true } });
  return createTourInternalWebHandler({
    listToursFn: async () => ({ ok: true, data: { tours: [] } }), readTourFn: async () => ({ ok: true, data: { id: tourId } }),
    searchTourPropertiesFn: success, readTourSelectionCartFn: success, appendTourSelectionCartVersionFn: success,
    createRouteVersionFn: success, reorderRouteStopsFn: success, acceptRouteVersionFn: success,
    autosaveCheatSheetFn: success, restoreCheatSheetFn: success, createProjectionFn: success,
    readProjectionCandidatesFn: success, sealProjectionFn: success,
    issueShareGrantFn: success, rotateShareGrantFn: success, revokeShareGrantFn: success,
    renderPdfFn: success, readPdfRenderFn: success, reviewPdfFn: success,
    previewPdfFn: async () => ({ ok: true, response: new Response("pdf", { headers: { "content-type": "application/pdf", "content-disposition": "inline" } }) }),
    downloadPdfFn: async () => ({ ok: true, response: new Response("pdf", { headers: { "content-type": "application/pdf" } }) }),
    ...overrides,
  });
}
const postHeaders = { origin: ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json", "x-carr-csrf": SESSION.csrfToken };
const issueBody = { projection_id: projectionId, token_digest: digest, permission_scopes: ["view_packet"], expires_at: "2027-01-02T03:04:05.000Z", receipt_digest: digest, idempotency_key: grantId };
const searchBody = { query: null, counties: ["Escambia"], property_types: [], min_square_feet: null,
  max_square_feet: null, availability: [], entrance_verified: null, public_projection_ready: null,
  photos_available: null, sort: "updated_desc", cursor: null, limit: 25 };
const appointmentBody = (start, end) => ({ idempotency_key: routeId, route_version_id: routeId, property_id: stopA,
  route_sequence: 1, route_label: "A", stop_state: "active", appointment_start: start, appointment_end: end,
  locked_appointment: true, dwell_minutes: 20, buffer_minutes: 5,
  access_coordinate_status: "approved", assertion_set_digest: digest });

test("HTTP rejects reversed microsecond windows before writes and preserves valid windows", async t => {
  for (const [name, start, end, valid] of [
    ["reversed", "2026-10-01T14:00:00.123999Z", "2026-10-01T14:00:00.123001Z", false],
    ["equal across offsets", "2026-10-01T09:00:00.123456-05:00", "2026-10-01T14:00:00.123456Z", true],
    ["increasing", "2026-10-01T14:00:00.123001Z", "2026-10-01T14:00:00.123002Z", true],
    ["reversed across offsets", "2026-10-01T09:00:00.123999-05:00", "2026-10-01T14:00:00.123001Z", false],
    ["reversed before epoch", "1969-12-31T23:59:59.999999Z", "1969-12-31T23:59:59.999998Z", false],
    ["increasing across epoch", "1969-12-31T23:59:59.999999Z", "1970-01-01T00:00:00.000001Z", true],
    ["reversed distant date", "9999-10-01T14:00:00.123999Z", "9999-10-01T14:00:00.123998Z", false],
  ]) await t.test(name, async () => {
    const calls = [], body = appointmentBody(start, end);
    const surface = handler({ appendRouteStopFn: async context => {
      calls.push(context.input); return { ok: true, data: { route_stop_id: stopA } };
    } });
    const response = await surface.fetch(request("/api/tours/route-stop", {
      method: "POST", headers: postHeaders, body: JSON.stringify(body),
    }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION);
    assert.equal(response.status, valid ? 200 : 400, await response.text());
    assert.deepEqual(calls, valid ? [body] : []);
  });
});

test("HTTP appointment timestamps respect PostgreSQL offset and year boundaries", async t => {
  for (const [name, start, end, valid] of [
    ["positive offset boundary", "2026-10-01T14:00:00.123456+15:59", "2026-10-01T14:00:00.123457+15:59", true],
    ["negative offset boundary", "2026-10-01T14:00:00.123456-15:59", "2026-10-01T14:00:00.123457-15:59", true],
    ["first AD year", "0001-10-01T14:00:00Z", "0001-10-01T14:30:00Z", true],
    ["last four-digit year", "9999-10-01T14:00:00Z", "9999-10-01T14:30:00Z", true],
    ["positive offset overflow", "2026-10-01T14:00:00+16:00", "2026-10-01T14:30:00+16:00", false],
    ["negative offset overflow", "2026-10-01T14:00:00-16:00", "2026-10-01T14:30:00-16:00", false],
    ["minute overflow", "2026-10-01T14:00:00+15:60", "2026-10-01T14:30:00+15:60", false],
    ["year zero", "0000-10-01T14:00:00Z", "0000-10-01T14:30:00Z", false],
    ["extended year", "+010000-10-01T14:00:00Z", "+010000-10-01T14:30:00Z", false],
  ]) await t.test(name, async () => {
    const calls = [], body = appointmentBody(start, end);
    const surface = handler({ appendRouteStopFn: async context => {
      calls.push(context.input); return { ok: true, data: { route_stop_id: stopA } };
    } });
    const inputs = valid ? [body] : ["appointment_start", "appointment_end"].map(field =>
      ({ ...appointmentBody("2026-10-01T14:00:00Z", "2026-10-01T14:30:00Z"), [field]: body[field] }));
    for (const input of inputs) {
      const response = await surface.fetch(request("/api/tours/route-stop", {
        method: "POST", headers: postHeaders, body: JSON.stringify(input),
      }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION);
      assert.equal(response.status, valid ? 200 : 400, await response.text());
      assert.deepEqual(calls, valid ? [body] : []);
    }
  });
});

test("revised route save accepts unchanged PostgreSQL appointment timestamps", async () => {
  const calls = [];
  const surface = handler({ appendRouteStopFn: async context => {
    calls.push(context.input); return { ok: true, data: { route_stop_id: stopA } };
  } });
  for (const [start, end] of [
    ["2026-10-01T14:00:00+00:00", "2026-10-01T14:30:00+00:00"],
    ["2026-10-01T09:00:00.123456-05:00", "2026-10-01T09:30:00.123456-05:00"],
    ["2026-10-02T00:00:00+10:00", "2026-10-02T00:30:00+10:00"],
  ]) {
    const body = { idempotency_key: routeId, route_version_id: routeId, property_id: stopA,
      route_sequence: 1, route_label: "A", stop_state: "active", appointment_start: start,
      appointment_end: end, locked_appointment: true, dwell_minutes: 20, buffer_minutes: 5,
      access_coordinate_status: "approved", assertion_set_digest: digest };
    const response = await surface.fetch(request("/api/tours/route-stop", {
      method: "POST", headers: postHeaders, body: JSON.stringify(body),
    }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION);
    assert.equal(response.status, 200, await response.text());
    assert.deepEqual(calls.at(-1), body, "preserve the instant and PostgreSQL microseconds");
  }
});

test("authenticated property search and versioned cart keep exact tenant-safe contracts", async () => {
  const calls = [];
  const surface = handler({
    searchTourPropertiesFn: async context => { calls.push(["search", context]); return { ok: true, data: { search: { items: [] } } }; },
    readTourSelectionCartFn: async context => { calls.push(["read", context]); return { ok: true, data: { cart: { tour_id: tourId, property_ids: [] } } }; },
    appendTourSelectionCartVersionFn: async context => { calls.push(["append", context]); return { ok: true, data: { selection_version_id: routeId } }; },
  });
  const env = { APP_HOST: "app.doctorcre.com" };
  const search = await surface.fetch(request("/api/tours/properties/search", { method: "POST", headers: postHeaders, body: JSON.stringify(searchBody) }), env, {}, ACTOR, SESSION);
  assert.equal(search.status, 200);
  assert.deepEqual((await search.json()).data.search.items, []);
  const read = await surface.fetch(request(`/api/tours/selection-cart?tour_id=${tourId}`), env, {}, ACTOR, SESSION);
  assert.equal(read.status, 200);
  assert.deepEqual((await read.json()).data.cart.property_ids, []);
  const payload = { tour_id: tourId, base_selection_version_id: null, expected_selection_version: 0,
    property_ids: [stopA, stopB], selection_digest: digest, idempotency_key: grantId };
  const append = await surface.fetch(request("/api/tours/selection-cart", { method: "POST", headers: postHeaders, body: JSON.stringify(payload) }), env, {}, ACTOR, SESSION);
  assert.equal(append.status, 200);
  assert.deepEqual(calls.map(([name, context]) => [name, context.input]), [["search", searchBody], ["read", { tour_id: tourId }], ["append", payload]]);
  assert.ok(calls.every(([, context]) => context.actor === ACTOR && context.input.actor === undefined));
  for (const bad of [{ ...searchBody, counties: ["Leon"] }, { ...searchBody, tenant: "other" },
    { ...searchBody, min_square_feet: 9000, max_square_feet: 1000 }]) {
    assert.equal((await surface.fetch(request("/api/tours/properties/search", { method: "POST", headers: postHeaders, body: JSON.stringify(bad) }), env, {}, ACTOR, SESSION)).status, 400);
  }
  for (const bad of [{ ...payload, property_ids: [stopA, stopA] }, { ...payload, actor_id: "other" },
    { ...payload, selection_digest: "bad" }]) {
    assert.equal((await surface.fetch(request("/api/tours/selection-cart", { method: "POST", headers: postHeaders, body: JSON.stringify(bad) }), env, {}, ACTOR, SESSION)).status, 400);
  }
  assert.equal((await surface.fetch(request(`/api/tours/selection-cart?tour_id=${tourId}&tenant=other`), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request("/api/tours/properties/search", { method: "POST", headers: { ...postHeaders, "x-carr-csrf": "wrong" }, body: JSON.stringify(searchBody) }), env, {}, ACTOR, SESSION)).status, 403);
});

test("property evidence, property search, and selection cart coexist behind the authenticated Tour adapter", async () => {
  const seen = [];
  const capture = seam => async context => {
    seen.push({ seam, ...context });
    return { ok: true, data: { available: true } };
  };
  const surface = handler({
    readPropertyEvidenceFn: capture("evidence"), searchTourPropertiesFn: capture("search"),
    readTourSelectionCartFn: capture("cart-read"), appendTourSelectionCartVersionFn: capture("cart-write"),
  });
  const env = { APP_HOST: "app.doctorcre.com" };
  const calls = [
    ["evidence", `/api/tours/property-evidence/v1?property_id=${tourId}&as_of=2026-09-29T12%3A00%3A00.000Z`, {}],
    ["search", "/api/tours/properties/search", { method: "POST", headers: postHeaders, body: JSON.stringify({ query: "medical", counties: [], property_types: [], min_square_feet: null, max_square_feet: null, availability: [], entrance_verified: null, public_projection_ready: null, photos_available: null, sort: "address_asc", cursor: null, limit: 20 }) }],
    ["cart-read", `/api/tours/selection-cart?tour_id=${tourId}`, {}],
    ["cart-write", "/api/tours/selection-cart", { method: "POST", headers: postHeaders, body: JSON.stringify({ tour_id: tourId, base_selection_version_id: null, expected_selection_version: 0, property_ids: [stopA], selection_digest: digest, idempotency_key: grantId }) }],
  ];
  for (const [seam, path, options] of calls) {
    assert.equal(isTourInternalRequest(request(path, options)), true, seam);
    assert.equal((await surface.fetch(request(path, options), env, {}, ACTOR, SESSION)).status, 200, seam);
    assert.equal(seen.at(-1).seam, seam);
    assert.deepEqual(seen.at(-1).actor, ACTOR);
    assert.equal((await surface.fetch(request(path, options), env, {}, undefined, undefined)).status, 401, seam);
  }
  assert.deepEqual(seen.map(call => call.seam), calls.map(([seam]) => seam));
});

test("internal Tour surface requires an injected authenticated actor and CSRF session", async () => {
  const surface = handler(); const assets = new Assets();
  for (const args of [[undefined, undefined], [ACTOR, undefined], [{}, SESSION]]) {
    const response = await surface.fetch(request("/tours"), { APP_HOST: "app.doctorcre.com", ASSETS: assets }, {}, ...args);
    assert.equal(response.status, 401);
  }
  assert.equal((await surface.fetch(request("/tours"), { APP_HOST: "app.doctorcre.com", ASSETS: assets }, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(assets.paths, ["/tours/index.html"]);
});

test("versioned property evidence read accepts only a property and as-of time in the authenticated session", async () => {
  const seen = [];
  const surface = handler({ readPropertyEvidenceFn: async context => {
    seen.push(context);
    return { ok: true, data: { schema: "tour-property-evidence.v1", property_id: tourId, facts: {} } };
  } });
  const env = { APP_HOST: "app.doctorcre.com", ASSETS: new Assets() };
  const asOf = "2026-09-29T12:00:00.000Z";
  const path = `/api/tours/property-evidence/v1?property_id=${tourId}&as_of=${encodeURIComponent(asOf)}`;
  assert.equal((await surface.fetch(request(path), env, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(seen[0].input, { property_id: tourId, as_of: asOf });
  assert.deepEqual(seen[0].actor, ACTOR);
  assert.equal((await surface.fetch(request(`${path}&tenant=other`), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request(`/api/tours/property-evidence/v1?property_id=${tourId}&as_of=bad`), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request(path), env, {}, undefined, undefined)).status, 401);
  assert.equal((await surface.fetch(request(path, { method: "POST" }), env, {}, ACTOR, SESSION)).status, 405);
});

test("property panel module and stylesheet are served through the authenticated Tour asset gate", async () => {
  const paths = [];
  const env = { APP_HOST: "app.doctorcre.com", ASSETS: { async fetch(assetRequest) {
    paths.push(new URL(assetRequest.url).pathname);
    return new Response("panel asset", { status: 200 });
  } } };
  const surface = handler();
  for (const path of ["/tours/property-panel.js", "/tours/property-panel.css"]) {
    assert.equal(isTourInternalRequest(request(path)), true);
    assert.equal((await surface.fetch(request(path), env, {}, ACTOR, SESSION)).status, 200);
    assert.equal((await surface.fetch(request(path), env, {}, undefined, undefined)).status, 401);
  }
  assert.deepEqual(paths, ["/tours/property-panel.js", "/tours/property-panel.css"]);
});

test("exact routes, methods, CSRF, and JSON bodies remain bounded", async () => {
  const surface = handler(); const env = { APP_HOST: "app.doctorcre.com" };
  assert.equal(isTourInternalRequest(request("/tours")), true);
  assert.equal(isTourInternalRequest(request("/api/tours/library")), true);
  assert.equal(isTourInternalRequest(request("/api/v1/tours")), false);
  assert.equal(isTourInternalRequest(request("/api/tours/library/extra")), false);
  assert.equal(isTourInternalRequest(request("/api/tours/interactions/review")), false);
  assert.equal((await surface.fetch(request("/api/tours/share/issue"), env, {}, ACTOR, SESSION)).status, 405);
  assert.equal((await surface.fetch(request("/api/tours/nope"), env, {}, ACTOR, SESSION)).status, 404);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: { ...postHeaders, "x-carr-csrf": "wrong" }, body: JSON.stringify(issueBody) }), env, {}, ACTOR, SESSION)).status, 403);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: { ...postHeaders, origin: "https://elsewhere.example" }, body: JSON.stringify(issueBody) }), env, {}, ACTOR, SESSION)).status, 403);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: postHeaders, body: JSON.stringify({ ...issueBody, actor: "chosen-by-browser" }) }), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: postHeaders, body: JSON.stringify({ ...issueBody, token: "never-permitted" }) }), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request("/api/tours/detail?tour_id=" + tourId + "&x=1"), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: { ...postHeaders, "content-length": "40000" }, body: JSON.stringify(issueBody) }), env, {}, ACTOR, SESSION)).status, 413);
});

test("only a SHA-256 digest crosses the confidential share issue seam", async () => {
  let received;
  const surface = handler({ issueShareGrantFn: async (context) => { received = context; return { ok: true, data: { share_grant_id: grantId } }; } });
  const response = await surface.fetch(request("/api/tours/share/issue", { method: "POST", headers: postHeaders, body: JSON.stringify(issueBody) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION);
  assert.equal(response.status, 200);
  assert.deepEqual(received.input, issueBody);
  assert.equal(received.session, undefined);
  assert.match(received.input.token_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(JSON.stringify(received).includes("token="), false);
  assert.equal(Object.hasOwn(received.input, "token"), false);
  assert.deepEqual(received.actor, ACTOR, "actor is server-injected, not selected in body");
});

test("static shell has no raw-token persistence/logging and stays in dealroom/tours assets", async () => {
  const [html, js, css, handlerSource] = await Promise.all([
    readFile(new URL("../../dealroom/tours/index.html", import.meta.url), "utf8"), readFile(new URL("../../dealroom/tours/app.js", import.meta.url), "utf8"),
    readFile(new URL("../../dealroom/tours/app.css", import.meta.url), "utf8"), readFile(new URL("../src/tour-internal-web.js", import.meta.url), "utf8"),
  ]);
  assert.equal(TOUR_INTERNAL_ASSET_DIRECTORY, "../out/doctorcre-artifacts/current/tours");
  assert.match(html, /\/tours\/app\.js/); assert.match(html, /\/tours\/app\.css/); assert.ok(css.length > 300);
  assert.match(js, /new Uint8Array\(32\)/); assert.match(js, /crypto\.getRandomValues/); assert.match(js, /crypto\.subtle\.digest\("SHA-256"/);
  assert.match(js, /https:\/\/reports\.doctorcre\.com\/share#token=\$\{raw\}/);
  assert.match(html, /value="view_map"/);
  assert.match(html, /id="share-grants"/);
  assert.match(js, /grant\.status === "active"/);
  assert.match(js, /dataset\.shareGrantId/);
  assert.match(js, /issueShare\(id\(state\.shareGrantId\)\)/);
  assert.match(js, /stops\(\)\.filter\(\(stop\) => stop\.stop_state === "active"\)\.map/);
  assert.match(js, /state\.cheatDirty \? "Unsaved changes"/);
  assert.match(js, /#cheat-content"\)\.addEventListener\("input"/);
  assert.match(js, /if \(!state\.cheatDirty \|\| state\.cheatDraftTourId !== tour\.id\)/);
  assert.doesNotMatch(html, /value="(?:download_pdf|react)"/);
  assert.match(html, /Shortlist and Comment let the client mark preferred properties/);
  assert.match(css, /#002F6C/); assert.match(css, /#F57F29/);
  for (const source of [js, handlerSource]) { assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|console\.(?:log|warn|error)/); }
  assert.doesNotMatch(handlerSource, /\/api\/v1/);
  assert.doesNotMatch(js, /\/api\/v1|(?:mapbox|leaflet|google\.maps)/i);
});

test("route seams enforce their exact body contracts", async () => {
  const calls = []; const surface = handler({ createRouteVersionFn: async (value) => { calls.push(value); return { ok: true, data: { route_version_id: routeId } }; } });
  const body = { tour_id: tourId, expected_route_version: 0, stop_ids: [stopA, stopB], idempotency_key: routeId };
  const response = await surface.fetch(request("/api/tours/route-version", { method: "POST", headers: postHeaders, body: JSON.stringify(body) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION);
  assert.equal(response.status, 200); assert.deepEqual(calls[0].input, body);
  const oneStop = { ...body, stop_ids: [stopA] };
  assert.equal((await surface.fetch(request("/api/tours/route-version", { method: "POST", headers: postHeaders, body: JSON.stringify(oneStop) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION)).status, 200);
  const duplicate = { ...body, stop_ids: [stopA, stopA] };
  assert.equal((await surface.fetch(request("/api/tours/route-version", { method: "POST", headers: postHeaders, body: JSON.stringify(duplicate) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION)).status, 400);
});

test("known optimistic races remain conflicts rather than service outages", async () => {
  const body = { tour_id: tourId, expected_route_version: 1, stop_ids: [stopA], idempotency_key: routeId };
  for (const message of [
    "tour route preparation refuses stale state",
    "route version refuses concurrent or stale route state",
    "route acceptance refuses concurrent or stale route state",
    "route acceptance refuses changed draft contents",
    "cheat sheet revision refuses concurrent or stale version",
    "cheat sheet restore refuses unavailable or stale revision",
    "tour selection refuses stale version",
  ]) {
    const surface = handler({ createRouteVersionFn: async () => { throw new Error(message); } });
    const response = await surface.fetch(request("/api/tours/route-version", { method: "POST", headers: postHeaders, body: JSON.stringify(body) }), { APP_HOST: "app.doctorcre.com" }, {}, ACTOR, SESSION);
    assert.equal(response.status, 409, message);
    assert.deepEqual(await response.json(), { error: "conflict" });
  }
});

test("internal PDF routes require exact authority-safe contracts and accepted download state", async () => {
  let renderInput; let reviewInput;
  const surface = handler({
    renderPdfFn: async context => { renderInput = context.input; return { ok: true, data: { render_job_id: routeId, status: "review_ready" } }; },
    reviewPdfFn: async context => { reviewInput = context.input; return { ok: true, data: { decision: "accept" } }; },
  });
  const env = { APP_HOST: "app.doctorcre.com" };
  const renderBody = { projection_id: projectionId, idempotency_key: routeId };
  assert.equal((await surface.fetch(request("/api/tours/pdf/render", { method: "POST", headers: postHeaders, body: JSON.stringify(renderBody) }), env, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(renderInput, renderBody);
  const reviewBody = { render_job_id: routeId, qc_run_digest: digest, decision: "accept", reviewed_at: "2027-01-02T03:04:05.000Z", review_receipt_digest: digest, reason: "Human checked the rendered pages", idempotency_key: grantId };
  assert.equal((await surface.fetch(request("/api/tours/pdf/review", { method: "POST", headers: postHeaders, body: JSON.stringify(reviewBody) }), env, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(reviewInput, reviewBody);
  assert.equal((await surface.fetch(request(`/api/tours/pdf/download?render_job_id=${routeId}`), env, {}, ACTOR, SESSION)).headers.get("content-type"), "application/pdf");
  assert.match((await surface.fetch(request(`/api/tours/pdf/preview?render_job_id=${routeId}`), env, {}, ACTOR, SESSION)).headers.get("content-disposition"), /inline/);
  assert.equal((await surface.fetch(request(`/api/tours/pdf/status?render_job_id=${routeId}&x=1`), env, {}, ACTOR, SESSION)).status, 400);
  assert.equal((await surface.fetch(request("/api/tours/pdf/review", { method: "POST", headers: postHeaders, body: JSON.stringify({ ...reviewBody, decision: "publish" }) }), env, {}, ACTOR, SESSION)).status, 400);
});

test("projection approval binds the human action to the exact reviewed candidate digest", async () => {
  let readInput; let sealInput;
  const surface = handler({
    readProjectionCandidatesFn: async context => { readInput = context.input; return { ok: true, data: { projection_id: projectionId, candidate_digest: digest, preview: [] } }; },
    sealProjectionFn: async context => { sealInput = context.input; return { ok: true, data: { projection_id: projectionId, status: "approved" } }; },
  });
  const env = { APP_HOST: "app.doctorcre.com" };
  assert.equal((await surface.fetch(request(`/api/tours/projection/candidates?projection_id=${projectionId}`), env, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(readInput, { projection_id: projectionId });
  const body = { projection_id: projectionId, candidate_digest: digest, receipt_digest: digest, idempotency_key: routeId };
  assert.equal((await surface.fetch(request("/api/tours/projection/seal", { method: "POST", headers: postHeaders, body: JSON.stringify(body) }), env, {}, ACTOR, SESSION)).status, 200);
  assert.deepEqual(sealInput, body);
  assert.equal((await surface.fetch(request("/api/tours/projection/seal", { method: "POST", headers: postHeaders, body: JSON.stringify({ ...body, selected_facts: [] }) }), env, {}, ACTOR, SESSION)).status, 400);
});
