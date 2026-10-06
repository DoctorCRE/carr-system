import test from "node:test";
import assert from "node:assert/strict";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";

// The DoctorCRE app Worker serves its own HTML but first forwards every page
// route here, unchanged, as its sign-in gate and requires a 200. This suite
// pins that each app page path is admitted, shares the workspace flag, and
// answers exactly like Home: redirect when signed out, 404 with the flag off,
// 200 with a session. Session bootstrap copied from work-inventory-census-web.
const HOST = "dealroom.doctorcre.com";
const APP_PAGES = ["/work-inventory", "/tasks", "/business", "/pipeline", "/control-room", "/incidents", "/notifications", "/conversations"];

class Kv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) { const value = this.values.get(key); return value == null ? null : options?.type === "json" ? JSON.parse(value) : value; }
  async delete(key) { this.values.delete(key); }
}
class Assets {
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/index.html") return new Response("<main>Deal Room</main>", { headers: { "content-type": "text/html" } });
    return new Response("missing", { status: 404 });
  }
}
const makeEnvironment = () => ({
  DEALROOM_HOST: HOST, GOOGLE_CLIENT_ID: "client", GOOGLE_CLIENT_SECRET: "secret",
  CORRELATION_ID: "corr-web", OAUTH_KV: new Kv(), ASSETS: new Assets(),
  WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true",
});
const overrides = (email) => ({
  exchangeGoogleCodeFn: async () => ({ id_token: "stub" }),
  verifyGoogleIdTokenFn: async () => ({ email, email_verified: true, sub: `sub:${email}` }),
  commandCenterReader: async (env, actor) => ({ viewer: actor.slug, needs_you_now: [], this_week: [], metrics: [], recent_calls: [], doc_at_work: [], recent_activity: [] }),
});
const cookie = (response) => (typeof response.headers.getSetCookie === "function" ? response.headers.getSetCookie() : [response.headers.get("set-cookie")]).find((value) => value?.startsWith("__Host-dealroom_session=")).split(";", 1)[0];

async function signIn(handler, environment) {
  const start = await handler.fetch(new Request(`https://${HOST}/auth/login`), environment, {});
  const google = new URL(start.headers.get("location"));
  const pending = typeof start.headers.getSetCookie === "function" ? start.headers.getSetCookie()[0] : start.headers.get("set-cookie");
  const callback = await handler.fetch(new Request(`https://${HOST}/auth/callback?state=${google.searchParams.get("state")}&code=stub`, { headers: { cookie: pending, "x-test-email": "joe.bookout.carr.us@gmail.com" } }), environment, {});
  return cookie(callback);
}

test("every DoctorCRE app page route is a Deal Room request; an unlisted page is not", () => {
  for (const path of APP_PAGES) {
    assert.equal(isDealroomRequest(new Request(`https://${HOST}${path}`), { DEALROOM_HOST: HOST }), true, `${path} must be admitted`);
  }
  assert.equal(isDealroomRequest(new Request(`https://${HOST}/not-an-app-page`), { DEALROOM_HOST: HOST }), false);
  // The typed shared Meeting page was removed from the app (decision
  // 7dc47eea): the pipeline conversation is recorded by Deal Room Call Mode.
  assert.equal(isDealroomRequest(new Request(`https://${HOST}/meeting`), { DEALROOM_HOST: HOST }), false,
    "/meeting is retired and must not be admitted by the gate");
});

test("an app page route answers like Home: sign-in redirect, flag-gated 404, then 200 for a session", async () => {
  const environment = makeEnvironment();
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com"));
  const page = (path, init = {}) => new Request(`https://${HOST}${path}`, { headers: { accept: "text/html", ...(init.headers || {}) } });

  for (const path of APP_PAGES) {
    const anonymous = await handler.fetch(page(path), environment, {});
    assert.equal(anonymous.status, 302, `${path} signed out must redirect`);
    assert.match(anonymous.headers.get("location"), /\/auth\/login\?return_to=/);
  }

  const session = await signIn(handler, environment);
  for (const path of APP_PAGES) {
    const off = await handler.fetch(page(path, { headers: { cookie: session } }), { ...environment, WORKSPACE_COMMAND_CENTER_READ_ENABLED: "false" }, {});
    assert.equal(off.status, 404, `${path} with the workspace flag off must not exist`);

    const on = await handler.fetch(page(path, { headers: { cookie: session } }), environment, {});
    assert.equal(on.status, 200, `${path} with a session must pass the gate`);
    assert.match(on.headers.get("content-type"), /text\/html/);
  }
});
