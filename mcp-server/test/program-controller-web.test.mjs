// WR-000110 — F02-ROUTE-REACHABLE, plus the route halves of T-ERROR-CLASS and
// T-AUTO-RELEASE.
//
// Cloned from atlas-inventory-graph-web.test.mjs, because the program-controller
// census route is mounted exactly like the atlas graph: same feature flag, same
// verified-session requirement, same typed error-to-status map, same injected
// reader. Anything that diverges from that pattern is a defect, so the proof is
// the same proof.

import test from "node:test";
import assert from "node:assert/strict";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";
import { PROGRAM_CONTROLLER_PATH } from "../src/program-controller-census.v5.js";

const HOST = "dealroom.doctorcre.com";

class Kv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) {
    const value = this.values.get(key);
    return value == null ? null : options?.type === "json" ? JSON.parse(value) : value;
  }
  async delete(key) { this.values.delete(key); }
}
class Assets {
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/workspace.html")
      return new Response("<main>CARR Command Center</main>", { headers: { "content-type": "text/html" } });
    return new Response("missing", { status: 404 });
  }
}
const makeEnvironment = () => ({
  DEALROOM_HOST: HOST, GOOGLE_CLIENT_ID: "client", GOOGLE_CLIENT_SECRET: "secret",
  CORRELATION_ID: "corr-web", OAUTH_KV: new Kv(), ASSETS: new Assets(),
  WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true",
});

const PAYLOAD = {
  schema_version: "doctorcre-v5-program-controller-census.v1",
  correlation_id: "corr-web",
  census: { source: "live_lease_census", observed_at: "2026-09-17T18:00:00.000Z",
    origin_main_sha: "a".repeat(40), repository_root: "/Users/booko/carr-system",
    active_leases: [] },
  width_answer: null, admission_answer: null, resume_answer: null, release_answer: null,
  coverage: [{ answer: "census", complete: true, missing_reason: null, detail: null }],
};

const typedError = code => { const error = new Error(code); error.code = code; return error; };

const overrides = (email, reader = null) => ({
  exchangeGoogleCodeFn: async () => ({ id_token: "stub" }),
  verifyGoogleIdTokenFn: async () => ({ email, email_verified: true, sub: `sub:${email}` }),
  commandCenterReader: async (env, actor) => ({ viewer: actor.slug, needs_you_now: [],
    this_week: [], metrics: [], recent_calls: [], doc_at_work: [], recent_activity: [] }),
  programControllerReader: reader || (async (env, actor, correlationId, params) => ({
    ...PAYLOAD, viewer: actor.slug, correlation_echo: correlationId, params_echo: params,
  })),
});

const cookie = response => (typeof response.headers.getSetCookie === "function"
  ? response.headers.getSetCookie()
  : [response.headers.get("set-cookie")])
  .find(value => value?.startsWith("__Host-dealroom_session=")).split(";", 1)[0];

async function signIn(handler, environment) {
  const start = await handler.fetch(new Request(`https://${HOST}/auth/login`), environment, {});
  const google = new URL(start.headers.get("location"));
  const pending = typeof start.headers.getSetCookie === "function"
    ? start.headers.getSetCookie()[0] : start.headers.get("set-cookie");
  const callback = await handler.fetch(new Request(
    `https://${HOST}/auth/callback?state=${google.searchParams.get("state")}&code=stub`,
    { headers: { cookie: pending, "x-test-email": "joe.bookout.carr.us@gmail.com" } }),
  environment, {});
  return cookie(callback);
}

test("F02-ROUTE-REACHABLE: authenticated, flag-gated, GET-only, and a pass-through", async () => {
  assert.equal(PROGRAM_CONTROLLER_PATH, "/api/v1/program-controller");
  assert.equal(isDealroomRequest(new Request(`https://${HOST}${PROGRAM_CONTROLLER_PATH}`),
    { DEALROOM_HOST: HOST }), true);
  const environment = makeEnvironment();
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com"));

  // 401 with no session. The route is admitted to the sign-in gate's own list,
  // so it answers AUTHENTICATION_REQUIRED rather than redirecting a fetch.
  let response = await handler.fetch(
    new Request(`https://${HOST}${PROGRAM_CONTROLLER_PATH}`), environment, {});
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "AUTHENTICATION_REQUIRED");

  const session = await signIn(handler, environment);

  // Flag off: the route does not exist, rather than existing and being empty.
  for (const flag of ["false", "yes"]) {
    response = await handler.fetch(
      new Request(`https://${HOST}${PROGRAM_CONTROLLER_PATH}`, { headers: { cookie: session } }),
      { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: flag }, {});
    assert.equal(response.status, 404, `flag ${flag}`);
  }

  response = await handler.fetch(new Request(`https://${HOST}${PROGRAM_CONTROLLER_PATH}`,
    { method: "POST", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 405);
  assert.equal((await response.json()).error, "METHOD_NOT_ALLOWED");
  assert.equal(response.headers.get("allow"), "GET, HEAD, OPTIONS");

  response = await handler.fetch(new Request(`https://${HOST}${PROGRAM_CONTROLLER_PATH}`,
    { method: "OPTIONS", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("allow"), "GET, HEAD, OPTIONS");

  // 200, and the three admitted parameters reach the reader unparsed.
  response = await handler.fetch(new Request(
    `https://${HOST}${PROGRAM_CONTROLLER_PATH}?program_ref=wr:1&slice_ref=slice:one&release_ref=release:one`,
    { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.census.source, "live_lease_census");
  assert.equal(payload.viewer, "joe");
  assert.deepEqual(payload.params_echo,
    { program_ref: "wr:1", slice_ref: "slice:one", release_ref: "release:one" });

  response = await handler.fetch(new Request(`https://${HOST}${PROGRAM_CONTROLLER_PATH}`,
    { method: "HEAD", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
});

test("F02-ROUTE-REACHABLE: an unknown query parameter is refused before the reader runs", async () => {
  const environment = makeEnvironment();
  let reached = false;
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com",
    async () => { reached = true; return PAYLOAD; }));
  const session = await signIn(handler, environment);
  const response = await handler.fetch(new Request(
    `https://${HOST}${PROGRAM_CONTROLLER_PATH}?nope=1`, { headers: { cookie: session } }),
  environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, "AUTHORIZATION_REFUSED");
  assert.equal(reached, false, "an unknown parameter reached the reader");
});

test("T-AUTO-RELEASE (c): auto_release_requested is an unknown parameter, in both spellings", async () => {
  const environment = makeEnvironment();
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com"));
  const session = await signIn(handler, environment);
  for (const value of ["1", "0"]) {
    const response = await handler.fetch(new Request(
      `https://${HOST}${PROGRAM_CONTROLLER_PATH}?auto_release_requested=${value}`,
      { headers: { cookie: session } }), environment, {});
    assert.equal(response.status, 403, `auto_release_requested=${value}`);
    assert.equal((await response.json()).error, "AUTHORIZATION_REFUSED");
  }
});

test("T-ERROR-CLASS: a dependency is 503 and a privilege error is not an authorization refusal", async () => {
  const environment = makeEnvironment();
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com",
    async () => { throw typedError("DEPENDENCY_UNAVAILABLE"); }));
  const session = await signIn(handler, environment);
  const response = await handler.fetch(new Request(`https://${HOST}${PROGRAM_CONTROLLER_PATH}`,
    { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "DEPENDENCY_UNAVAILABLE");
  assert.notEqual(response.status, 403,
    "a missing grant reported as an authorization refusal buries a grant gap");
});

test("T-ERROR-CLASS: the whole typed error-to-status map is the atlas route's map", async () => {
  const environment = makeEnvironment();
  const expected = {
    AUTHORIZATION_REFUSED: 403, TENANT_SCOPE_REFUSED: 404, FRESHNESS_UNKNOWN: 409,
    DEPENDENCY_UNAVAILABLE: 503, INTERNAL_ERROR: 500,
  };
  for (const [code, status] of Object.entries(expected)) {
    const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com",
      async () => { throw typedError(code); }));
    const session = await signIn(handler, environment);
    const response = await handler.fetch(new Request(`https://${HOST}${PROGRAM_CONTROLLER_PATH}`,
      { headers: { cookie: session } }), environment, {});
    assert.equal(response.status, status, code);
    assert.equal((await response.json()).error, code);
  }
  // And an error the map does not name is OUR bug, never a caller refusal.
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com",
    async () => { throw typedError("SOMETHING_ELSE"); }));
  const session = await signIn(handler, environment);
  const response = await handler.fetch(new Request(`https://${HOST}${PROGRAM_CONTROLLER_PATH}`,
    { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 500);
  assert.equal((await response.json()).error, "INTERNAL_ERROR");
});
