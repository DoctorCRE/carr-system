// Tests retired metering and program controller web routes.
import test from "node:test";
import assert from "node:assert/strict";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";

const HOST = "dealroom.doctorcre.com";
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
const overrides = (email, reader = null) => ({
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

test("retired metering and program-controller routes return not_found for anonymous and authenticated requests", async () => {
  const environment = makeEnvironment();
  const handler = createDealroomHandler(overrides("joe.bookout.carr.us@gmail.com"));
  const session = await signIn(handler, environment);
  for (const path of ["/api/v1/metering", "/api/v1/program-controller"]) {
    assert.equal(isDealroomRequest(new Request(`https://${HOST}${path}`), environment), true);
    const anonymous = await handler.fetch(new Request(`https://${HOST}${path}`), environment, {});
    assert.equal(anonymous.status, 404);
    for (const method of ["GET", "HEAD", "POST", "OPTIONS"]) {
      const response = await handler.fetch(new Request(`https://${HOST}${path}`, {
        method, headers: { cookie: session },
      }), environment, {});
      assert.equal(response.status, 404, `${method} ${path}`);
      assert.deepEqual(await response.json(), { error: "not_found" });
    }
  }
});
