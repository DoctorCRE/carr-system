import test from "node:test";
import assert from "node:assert/strict";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";
import { METERING_PATH } from "../src/cost-ledger-projection.v5.js";

const HOST = "dealroom.doctorcre.com";
// WR-000111 — MTR-ROUTE-CONTRACT. Cloned from atlas-inventory-graph-web.test.mjs so
// the two routes keep ONE contract.
// Session bootstrap copied from work-inventory-census-web.test.mjs: the route must
// be reachable only behind the same verified cookie session.
class Kv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) { const value = this.values.get(key); return value == null ? null : options?.type === "json" ? JSON.parse(value) : value; }
  async delete(key) { this.values.delete(key); }
}
class Assets {
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/workspace.html") return new Response("<main>CARR Command Center</main>", { headers: { "content-type": "text/html" } });
    return new Response("missing", { status: 404 });
  }
}
const makeEnvironment = () => ({
  DEALROOM_HOST: HOST, GOOGLE_CLIENT_ID: "client", GOOGLE_CLIENT_SECRET: "secret",
  CORRELATION_ID: "corr-web", OAUTH_KV: new Kv(), ASSETS: new Assets(),
  WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true",
});
const PAYLOAD = {
  schema_version: "doctorcre-v5-metering.v1",
  provider: "carr", account: "wr111-metering", project: "wr111:web", product: "root",
  period: null,
  as_of: { quantity: 55, observed_at: "2026-09-17T12:00:00.000Z" },
  allowance: { ceiling_units: 1000, effective_ceiling_units: 1000 },
  policy: { cap_units: 1000, source: "ops.cost_scope_node.authorization_ceiling_units" },
  estimate: { units: 60 },
  charge_projection: { units: 55 },
  coverage: { complete: true, sources: [{ source: "ops.cost_ledger_entry:wr111:web", enumerable: true, reason_id: null }] },
  hierarchy_overdrawn: false, requires: [],
};
const overrides = (email, reader = null) => ({
  exchangeGoogleCodeFn: async () => ({ id_token: "stub" }),
  verifyGoogleIdTokenFn: async () => ({ email, email_verified: true, sub: `sub:${email}` }),
  commandCenterReader: async (env, actor) => ({ viewer: actor.slug, needs_you_now: [], this_week: [], metrics: [], recent_calls: [], doc_at_work: [], recent_activity: [] }),
  meteringReader: reader || (async (env, actor, correlationId, params) => ({
    ...PAYLOAD, viewer: actor.slug, correlation_echo: correlationId, params_echo: params,
  })),
});
const cookie = (response) => (typeof response.headers.getSetCookie === "function" ? response.headers.getSetCookie() : [response.headers.get("set-cookie")]).find((value) => value?.startsWith("__Host-dealroom_session=")).split(";", 1)[0];

async function signIn(handler, environment) {
  const start = await handler.fetch(new Request(`https://${HOST}/auth/login`), environment, {});
  const google = new URL(start.headers.get("location"));
  const pending = typeof start.headers.getSetCookie === "function" ? start.headers.getSetCookie()[0] : start.headers.get("set-cookie");
  const callback = await handler.fetch(new Request(`https://${HOST}/auth/callback?state=${google.searchParams.get("state")}&code=stub`, { headers: { cookie: pending, "x-test-email": "joe.bookout.carr.us@gmail.com" } }), environment, {});
  return cookie(callback);
}

test("MTR-ROUTE-CONTRACT: the metering route is authenticated, flag-gated, GET-only and a pass-through", async () => {
  assert.equal(METERING_PATH, "/api/v1/metering");
  assert.equal(isDealroomRequest(new Request(`https://${HOST}${METERING_PATH}`), { DEALROOM_HOST: HOST }), true);
  const environment = makeEnvironment();
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com"));

  let response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}`), environment, {});
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "AUTHENTICATION_REQUIRED");

  const session = await signIn(handler, environment);

  // Flag off: the route does not exist, it is not merely empty.
  response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}`, { headers: { cookie: session } }), { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: "false" }, {});
  assert.equal(response.status, 404);
  response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}`, { headers: { cookie: session } }), { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: "yes" }, {});
  assert.equal(response.status, 404);

  response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}`, { method: "POST", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 405);
  assert.equal((await response.json()).error, "METHOD_NOT_ALLOWED");
  assert.equal(response.headers.get("allow"), "GET, HEAD, OPTIONS");

  response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}`, { method: "OPTIONS", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("allow"), "GET, HEAD, OPTIONS");

  response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.viewer, "joe");
  assert.equal(body.schema_version, "doctorcre-v5-metering.v1");
  assert.equal(body.as_of.quantity, 55);
  // MTR-CAP-NOT-USAGE at the route boundary: the cap travels under policy and
  // is not folded into the quantity or the projection.
  assert.equal(body.policy.cap_units, 1000);
  assert.equal(body.charge_projection.units, 55);
  assert.equal(body.coverage.complete, true);
  assert.equal(body.correlation_echo, "corr-web");
  assert.deepEqual(body.params_echo, { tree_ref: null, period: null });

  // The two query parameters reach the reader verbatim; the route parses nothing.
  response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}?tree_ref=wr111:web&period=2026-09`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).params_echo, { tree_ref: "wr111:web", period: "2026-09" });

  // An unknown parameter is a refusal, not a silently ignored field.
  response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}?nope=1`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, "AUTHORIZATION_REFUSED");

  // HEAD answers with the same status and no body.
  response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}`, { method: "HEAD", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "");
});

test("MTR-ROUTE-CONTRACT: typed reader refusals map to the same statuses the sibling read routes use", async () => {
  const environment = makeEnvironment();
  const session = await signIn(createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com")), environment);
  const cases = [
    ["DEPENDENCY_UNAVAILABLE", 503], ["AUTHORIZATION_REFUSED", 403],
    ["TENANT_SCOPE_REFUSED", 404], ["FRESHNESS_UNKNOWN", 409], ["INTERNAL_ERROR", 500],
  ];
  for (const [code, status] of cases) {
    const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com", async () => {
      throw Object.assign(new Error(code), { code });
    }));
    const response = await handler.fetch(new Request(`https://${HOST}${METERING_PATH}`, { headers: { cookie: session } }), environment, {});
    assert.equal(response.status, status, `${code} must map to ${status}`);
    assert.equal((await response.json()).error, code);
  }
  // An untyped throw never leaks its message.
  const opaque = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com", async () => { throw new Error("connection string"); }));
  const response = await opaque.fetch(new Request(`https://${HOST}${METERING_PATH}`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 500);
  assert.equal((await response.json()).error, "INTERNAL_ERROR");

  // With no injected reader the route falls back to its own default reader, which
  // needs DATABASE_URL_READER. Absent that, the answer is a typed refusal — never
  // a fabricated empty projection, and never a leaked connection detail.
  const bare = createDealroomHandler({ ...overrides("joe.bookout.carr.us@gmail.com"), meteringReader: null });
  const missing = await bare.fetch(new Request(`https://${HOST}${METERING_PATH}`, { headers: { cookie: session } }), environment, {});
  assert.equal(missing.status, 500);
  assert.deepEqual(await missing.json(), { error: "INTERNAL_ERROR" });
});
