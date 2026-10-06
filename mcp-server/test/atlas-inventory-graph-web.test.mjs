import test from "node:test";
import assert from "node:assert/strict";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";
import { ATLAS_GRAPH_PATH } from "../src/atlas-inventory-graph.v5.js";

const HOST = "dealroom.doctorcre.com";
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
  version: { bundle_digest: "a".repeat(64), registry_version: "scac-mutation-registry.v35" },
  observed_at: "2026-09-16T12:00:00.000Z", viewer: "joe", tenant: "carr-internal",
  layer: ["declared", "installed", "observed"], q: null, include_retired: false, limit: 500,
  nodes: [{ id: "verb:add-loop", class: "verb", key: "add-loop", title: "Open a new loop",
    layer: "declared", status: "write", retired_at: null,
    source_ref: "mcp-server/src/tools.js", evidence: "declared", unlinked: false }],
  edges: [{ from: "verb:add-loop", to: "mutation:add-loop", type: "mutates_through",
    evidence: "declared", source_ref: "mcp-server/src/mutation-registry.js", observed_at: null }],
  index: { declared: { verb: ["verb:add-loop"] } },
  coverage: [{ source_ref: "ops.service", evidence_class: "installed", node_count: 3,
    edge_count: 0, complete: true, missing_reason: null }],
  truncated: true, next_cursor: "Y3Vyc29y",
  source: { source: "atlas_inventory_graph", freshness: "fresh" },
};
const overrides = (email, reader = null) => ({
  exchangeGoogleCodeFn: async () => ({ id_token: "stub" }),
  verifyGoogleIdTokenFn: async () => ({ email, email_verified: true, sub: `sub:${email}` }),
  commandCenterReader: async (env, actor) => ({ viewer: actor.slug, needs_you_now: [], this_week: [], metrics: [], recent_calls: [], doc_at_work: [], recent_activity: [] }),
  atlasGraphReader: reader || (async (env, actor, correlationId, params) => ({
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

test("the atlas route is authenticated, flag-gated, GET-only and a pass-through", async () => {
  assert.equal(ATLAS_GRAPH_PATH, "/api/v1/atlas-graph");
  assert.equal(isDealroomRequest(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`), { DEALROOM_HOST: HOST }), true);
  const environment = makeEnvironment();
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com"));

  let response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`), environment, {});
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "AUTHENTICATION_REQUIRED");

  const session = await signIn(handler, environment);

  // Flag off: the route does not exist, it is not merely empty.
  response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`, { headers: { cookie: session } }), { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: "false" }, {});
  assert.equal(response.status, 404);
  response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`, { headers: { cookie: session } }), { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: "yes" }, {});
  assert.equal(response.status, 404);

  response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`, { method: "POST", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 405);
  assert.equal((await response.json()).error, "METHOD_NOT_ALLOWED");
  assert.equal(response.headers.get("allow"), "GET, HEAD, OPTIONS");

  response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`, { method: "OPTIONS", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 204);
  assert.equal(response.headers.get("allow"), "GET, HEAD, OPTIONS");

  response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.viewer, "joe");
  assert.equal(body.version.bundle_digest, "a".repeat(64));
  assert.equal(body.truncated, true);
  assert.equal(body.next_cursor, "Y3Vyc29y");
  assert.deepEqual(body.nodes.map((item) => item.id), ["verb:add-loop"]);
  assert.deepEqual(body.edges.map((item) => item.type), ["mutates_through"]);
  assert.deepEqual(body.index, { declared: { verb: ["verb:add-loop"] } });
  assert.deepEqual(body.coverage.map((entry) => [entry.source_ref, entry.complete]), [["ops.service", true]]);
  assert.equal(body.correlation_echo, "corr-web");
  assert.deepEqual(body.params_echo, { layer: null, q: null, include_retired: null, limit: null, cursor: null });

  // The five query parameters reach the reader verbatim; the route parses nothing.
  response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}?layer=installed&q=worker&include_retired=true&limit=25&cursor=abc`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).params_echo, {
    layer: "installed", q: "worker", include_retired: "true", limit: "25", cursor: "abc",
  });

  // An unknown parameter is a refusal, not a silently ignored field.
  response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}?viewer=dell`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, "AUTHORIZATION_REFUSED");

  // HEAD answers with the same status and no body.
  response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`, { method: "HEAD", headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "");
});

test("typed reader refusals map to the same statuses the census route uses", async () => {
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
    const response = await handler.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`, { headers: { cookie: session } }), environment, {});
    assert.equal(response.status, status, `${code} must map to ${status}`);
    assert.equal((await response.json()).error, code);
  }
  // An untyped throw never leaks its message.
  const opaque = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com", async () => { throw new Error("connection string"); }));
  const response = await opaque.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`, { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 500);
  assert.equal((await response.json()).error, "INTERNAL_ERROR");

  // With no injected reader the route falls back to its own default reader, which
  // needs DATABASE_URL_READER. Absent that, the answer is a typed refusal — never
  // a fabricated empty atlas, and never a leaked connection detail.
  const bare = createDealroomHandler({ ...overrides("joe.bookout.carr.us@gmail.com"), atlasGraphReader: null });
  const missing = await bare.fetch(new Request(`https://${HOST}${ATLAS_GRAPH_PATH}`, { headers: { cookie: session } }), environment, {});
  assert.equal(missing.status, 500);
  assert.deepEqual(await missing.json(), { error: "INTERNAL_ERROR" });
});
