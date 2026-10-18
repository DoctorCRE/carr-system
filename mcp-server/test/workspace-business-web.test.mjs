import test from "node:test";
import assert from "node:assert/strict";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";
import { parseBusinessQuery } from "../src/workspace-business-read.js";

const HOST = "dealroom.doctorcre.com";

test('relationship snapshot uses the authenticated read-only business route',async()=>{
 const env=environment();let reads=0;
 const handler=createDealroomHandler(overrides(async(_env,actor,request)=>{
  reads++;assert.equal(actor.slug,'joe');assert.equal(new URL(request.url).pathname,'/api/v1/business/relationships');
  return {schema:'carr-relationship-network.v1',nodes:[],edges:[],referrals:[],suggestions:[]};
 }));
 const path=`https://${HOST}/api/v1/business/relationships?contract=relationship-network.v1`;
 assert.equal((await handler.fetch(new Request(path),env,{})).status,401);assert.equal(reads,0);
 const session=await signIn(handler,env);
 const response=await handler.fetch(new Request(path,{headers:{cookie:session}}),env,{});
 assert.equal(response.status,200);assert.equal((await response.json()).schema,'carr-relationship-network.v1');assert.equal(reads,1);
 assert.equal((await handler.fetch(new Request(path,{method:'POST',headers:{cookie:session}}),env,{})).status,405);assert.equal(reads,1);
});

test('clients refuse vendor-only territory filtering and sorting through the authenticated route', async () => {
 const env=environment();
 const handler=createDealroomHandler(overrides(async (_env,actor,request)=>{
  parseBusinessQuery('clients',new URL(request.url).searchParams,actor.slug);
  return payloadFor('clients',actor);
 }));
 const session=await signIn(handler,env);
 for(const query of ['contract=vendor-directory.v1&territory=Synthetic%20North','contract=vendor-directory.v1&sort=territory']) {
  const response=await handler.fetch(new Request(`https://${HOST}/api/v1/business/clients?${query}`,{headers:{cookie:session}}),env,{});
  assert.equal(response.status,400);assert.equal((await response.json()).error,'QUERY_INVALID');
 }
});
const ID = "3f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8";
const JOE_EMAIL = "joe.bookout.carr.us@gmail.com";

class Kv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) { const value = this.values.get(key); return value == null ? null : options?.type === "json" ? JSON.parse(value) : value; }
  async delete(key) { this.values.delete(key); }
}
class Assets {
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/business.html") return new Response("<main>DoctorCRE business records</main>", { headers: { "content-type": "text/html" } });
    if (path === "/workspace.html") return new Response("<main>CARR Command Center</main>", { headers: { "content-type": "text/html" } });
    if (path === "/index.html") return new Response("<main>Deal Room</main>", { headers: { "content-type": "text/html" } });
    return new Response("missing", { status: 404 });
  }
}

const environment = () => ({ DEALROOM_HOST: HOST, GOOGLE_CLIENT_ID: "client", GOOGLE_CLIENT_SECRET: "secret",
  CORRELATION_ID: "corr-business", OAUTH_KV: new Kv(), ASSETS: new Assets(), WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true" });

const payloadFor = (dataset, actor) => ({
  viewer: actor.slug, dataset, query: { dataset, scope: "team", q: null, sort: "name", page: 1, page_size: 25, status: null, type: null, pipeline: "any" },
  total: 1, page: 1, page_size: 25, page_count: 1, out_of_range: false,
  rows: [{ id: ID, name: "Ridgeline Dental" }], facets: { statuses: [], types: [] }, partial: null,
  recorded_field_note: "recorded, not a lifecycle transition",
  source: { source: dataset === "clients" ? "client" : "vendor", source_ref: "client+party", observed_at: "2026-09-10T15:00:00.000Z",
    valid_until: "2026-09-10T15:01:00.000Z", freshness: "fresh", correlation_id: "corr-business", safe_explanation: "fresh" },
});

const overrides = (reader = null, calls = []) => ({
  exchangeGoogleCodeFn: async () => ({ id_token: "stub" }),
  verifyGoogleIdTokenFn: async () => ({ email: JOE_EMAIL, email_verified: true, sub: `sub:${JOE_EMAIL}` }),
  businessReader: reader || (async (env, actor, request, correlationId) => {
    const url = new URL(request.url);
    calls.push({ actor, pathname: url.pathname, search: url.search, correlationId });
    return payloadFor(url.pathname.includes("/vendors") ? "vendors" : "clients", actor);
  }),
});

const cookie = (response) => (typeof response.headers.getSetCookie === "function" ? response.headers.getSetCookie() : [response.headers.get("set-cookie")])
  .find((value) => value?.startsWith("__Host-dealroom_session=")).split(";", 1)[0];

async function signIn(handler, env) {
  const start = await handler.fetch(new Request(`https://${HOST}/auth/login`), env, {});
  const google = new URL(start.headers.get("location"));
  const pending = typeof start.headers.getSetCookie === "function" ? start.headers.getSetCookie()[0] : start.headers.get("set-cookie");
  const callback = await handler.fetch(new Request(`https://${HOST}/auth/callback?state=${google.searchParams.get("state")}&code=stub`, { headers: { cookie: pending } }), env, {});
  return cookie(callback);
}

test("Clients and Vendors are authenticated routes on the same host as the rest of the workspace", async () => {
  for (const path of ["/clients", "/vendors", "/api/v1/business/clients", `/api/v1/business/vendors/${ID}`]) {
    assert.equal(isDealroomRequest(new Request(`https://${HOST}${path}`), { DEALROOM_HOST: HOST }), true, path);
  }
  const env = environment();
  const handler = createDealroomHandler(overrides());
  // Signed out, the views send the reader to sign in and the API refuses in typed JSON.
  let response = await handler.fetch(new Request(`https://${HOST}/clients?q=ridge`, { headers: { accept: "text/html" } }), env, {});
  assert.equal(response.status, 302);
  assert.match(response.headers.get("location"), /\/auth\/login\?return_to=%2Fclients%3Fq%3Dridge/);
  response = await handler.fetch(new Request(`https://${HOST}/api/v1/business/clients`), env, {});
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "AUTHENTICATION_REQUIRED");

  const session = await signIn(handler, env);
  for (const [path, expected] of [["/clients", /business records/], ["/vendors", /business records/]]) {
    response = await handler.fetch(new Request(`https://${HOST}${path}`, { headers: { cookie: session, accept: "text/html" } }), env, {});
    assert.equal(response.status, 200, path);
    assert.match(await response.text(), expected);
  }
  // The asset file is not a second address for the same view.
  response = await handler.fetch(new Request(`https://${HOST}/business.html`, { headers: { cookie: session, accept: "text/html" } }), env, {});
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("location"), `https://${HOST}/clients`);
});

test("the read is bound to the session actor and the caller cannot name another owner", async () => {
  const calls = [];
  const env = environment();
  const handler = createDealroomHandler(overrides(null, calls));
  const session = await signIn(handler, env);
  let response = await handler.fetch(new Request(`https://${HOST}/api/v1/business/clients?scope=mine&q=ridge`, { headers: { cookie: session } }), env, {});
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.viewer, "joe");
  assert.equal(body.total, 1);
  assert.equal(calls[0].actor.slug, "joe");
  assert.equal(calls[0].search, "?scope=mine&q=ridge");
  assert.equal(calls[0].correlationId, "corr-business");

  // The route hands the whole query to the read model, which owns the refusal.
  const strict = createDealroomHandler(overrides(async (_env, actor, request) => {
    const url = new URL(request.url);
    parseBusinessQuery("clients", url.searchParams, actor.slug);
    return payloadFor("clients", actor);
  }));
  const strictSession = await signIn(strict, env);
  response = await strict.fetch(new Request(`https://${HOST}/api/v1/business/clients?viewer=dell`, { headers: { cookie: strictSession } }), env, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, "AUTHORIZATION_REFUSED");
  response = await strict.fetch(new Request(`https://${HOST}/api/v1/business/clients?owner=dell`, { headers: { cookie: strictSession } }), env, {});
  assert.equal(response.status, 200);
  assert.equal((await response.json()).viewer, "joe", "owner is a list filter, never an actor selector");
  response = await strict.fetch(new Request(`https://${HOST}/api/v1/business/clients?viewer=joe`, { headers: { cookie: strictSession } }), env, {});
  assert.equal(response.status, 200);
});

test("each read-model refusal keeps its own status, and none of them becomes an empty list", async () => {
  const env = environment();
  const cases = [
    ["QUERY_INVALID", 400], ["AUTHORIZATION_REFUSED", 403], ["TENANT_SCOPE_REFUSED", 404], ["RECORD_NOT_FOUND", 404],
    ["VIEWER_OWNER_UNKNOWN", 409], ["FRESHNESS_UNKNOWN", 409], ["DEPENDENCY_UNAVAILABLE", 503], ["INTERNAL_ERROR", 500],
  ];
  for (const [code, status] of cases) {
    const handler = createDealroomHandler(overrides(async () => { throw Object.assign(new Error(code), { code }); }));
    const session = await signIn(handler, env);
    const response = await handler.fetch(new Request(`https://${HOST}/api/v1/business/clients`, { headers: { cookie: session } }), env, {});
    assert.equal(response.status, status, code);
    assert.equal((await response.json()).error, code);
  }
  // An untyped failure is an internal error, never a 200 with nothing in it.
  const surprising = createDealroomHandler(overrides(async () => { throw new Error("boom"); }));
  const session = await signIn(surprising, env);
  const response = await surprising.fetch(new Request(`https://${HOST}/api/v1/business/clients`), { ...env }, {});
  assert.equal(response.status, 401);
  const authed = await surprising.fetch(new Request(`https://${HOST}/api/v1/business/clients`, { headers: { cookie: session } }), env, {});
  assert.equal(authed.status, 500);
  assert.equal((await authed.json()).error, "INTERNAL_ERROR");
});

test("an unprovisioned read is a named 503, and no SQL or driver text reaches the browser", async () => {
  const env = environment();
  for (const [dependency, status] of [["read_access", 503], ["read_source", 503], ["read_credential", 503]]) {
    const handler = createDealroomHandler(overrides(async () => {
      throw Object.assign(new Error("permission denied for table client"), { code: "DEPENDENCY_NOT_PROVISIONED", detail: { dependency } });
    }));
    const session = await signIn(handler, env);
    const response = await handler.fetch(new Request(`https://${HOST}/api/v1/business/clients`, { headers: { cookie: session } }), env, {});
    assert.equal(response.status, status, dependency);
    const body = await response.json();
    assert.deepEqual(body, { error: "DEPENDENCY_NOT_PROVISIONED", dependency });
    assert.doesNotMatch(JSON.stringify(body), /select|permission|table|denied|stack/i);
  }
  // An unrecognised class is not echoed back, and neither is a driver message
  // smuggled in as the detail.
  const loose = createDealroomHandler(overrides(async () => {
    throw Object.assign(new Error("boom"), { code: "DEPENDENCY_NOT_PROVISIONED", detail: { dependency: "relation \"client\" does not exist" } });
  }));
  const session = await signIn(loose, env);
  const response = await loose.fetch(new Request(`https://${HOST}/api/v1/business/clients`, { headers: { cookie: session } }), env, {});
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "DEPENDENCY_NOT_PROVISIONED" });
});

test("the business read answers GET and HEAD only and never becomes a write door", async () => {
  const env = environment();
  const handler = createDealroomHandler(overrides());
  const session = await signIn(handler, env);
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    const response = await handler.fetch(new Request(`https://${HOST}/api/v1/business/clients`, { method, headers: { cookie: session } }), env, {});
    assert.equal(response.status, 405, method);
    assert.equal(response.headers.get("allow"), "GET, HEAD, OPTIONS");
    assert.equal((await response.json()).error, "METHOD_NOT_ALLOWED");
  }
  const options = await handler.fetch(new Request(`https://${HOST}/api/v1/business/clients`, { method: "OPTIONS", headers: { cookie: session } }), env, {});
  assert.equal(options.status, 204);
  const head = await handler.fetch(new Request(`https://${HOST}/api/v1/business/clients`, { method: "HEAD", headers: { cookie: session } }), env, {});
  assert.equal(head.status, 200);
  assert.equal(await head.text(), "");
});

test("only the two exact datasets exist, and the retired workspace API stays a 404", async () => {
  const env = environment();
  const handler = createDealroomHandler(overrides());
  const session = await signIn(handler, env);
  for (const path of ["/api/v1/business", "/api/v1/business/", "/api/v1/business/leads", "/api/v1/business/clients/not-a-uuid",
    "/api/v1/business/clients/extra/segment", "/api/v1/workspace/command-center", "/api/v1/anything"]) {
    const response = await handler.fetch(new Request(`https://${HOST}${path}`, { headers: { cookie: session } }), env, {});
    assert.equal(response.status, 404, path);
    assert.equal((await response.json()).error, "not_found", path);
  }
  const record = await handler.fetch(new Request(`https://${HOST}/api/v1/business/vendors/${ID}`, { headers: { cookie: session } }), env, {});
  assert.equal(record.status, 200);
  assert.equal((await record.json()).dataset, "vendors");
});

test("the workspace flag governs Clients and Vendors exactly as it governs Home", async () => {
  const env = environment();
  const handler = createDealroomHandler(overrides());
  const session = await signIn(handler, env);
  for (const flag of ["false", "yes", undefined]) {
    const off = { ...env, WORKSPACE_COMMAND_CENTER_READ_ENABLED: flag };
    for (const path of ["/clients", "/vendors", "/business.html", "/api/v1/business/clients", `/api/v1/business/clients/${ID}`]) {
      const response = await handler.fetch(new Request(`https://${HOST}${path}`, { headers: { cookie: session, accept: "text/html" } }), off, {});
      assert.equal(response.status, 404, `${path} @ ${flag}`);
    }
  }
  // And with the flag on, the same paths are live.
  const live = await handler.fetch(new Request(`https://${HOST}/clients`, { headers: { cookie: session, accept: "text/html" } }), env, {});
  assert.equal(live.status, 200);
});

test("a missing reader is an unavailable dependency, not an invented answer", async () => {
  const env = environment();
  const handler = createDealroomHandler({ ...overrides(), businessReader: null });
  const session = await signIn(handler, env);
  const response = await handler.fetch(new Request(`https://${HOST}/api/v1/business/clients`, { headers: { cookie: session } }), env, {});
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "DEPENDENCY_UNAVAILABLE");
});
