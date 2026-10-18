import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";
import { mcpOriginRefusal } from "../src/oauth-policy.js";

const CARR_HOST = "carr-mcp-staging.joe-bookout-carr-us.workers.dev";
const APP_HOST = "doctorcre-app-staging.joe-bookout-carr-us.workers.dev";
const SECRET = "synthetic-e2e-session-secret-for-tests-only";
const NOW = 1_800_000_000_000;

class MemoryKv {
  values = new Map();
  writes = [];
  async put(key, value, options) { this.values.set(key, value); this.writes.push({ key, value, options }); }
  async get(key, options) {
    const value = this.values.get(key);
    return value === undefined ? null : options?.type === "json" ? JSON.parse(value) : value;
  }
  async delete(key) { this.values.delete(key); }
}

function environment(overrides = {}) {
  return { CARR_ENV: "staging", APP_HOST: CARR_HOST, DOCTORCRE_APP_HOST: APP_HOST,
    E2E_SESSION_SECRET: SECRET, OAUTH_KV: new MemoryKv(),
    WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true", ...overrides };
}

function handler(overrides = {}) {
  return createDealroomHandler({ now: () => NOW,
    commandCenterReader: async (_env, actor) => ({ viewer: actor.slug, human: actor.human }),
    mcpHandler: async (_request, _env, _ctx, actor) => Response.json({ actor }),
    ...overrides });
}

function exchange(server, env, { host = APP_HOST, authorization = `Bearer ${SECRET}`, ...init } = {}) {
  return server.fetch(new Request(`https://${host}/auth/e2e-session`, {
    method: "POST", ...init, headers: { authorization, ...init.headers },
  }), env, {});
}

function cookie(response) {
  const value = response.headers.get("set-cookie");
  assert.match(value, /^__Host-dealroom_session=[A-Za-z0-9_-]+; Path=\/; Max-Age=43200; Secure; HttpOnly; SameSite=Lax$/);
  assert.equal(value.includes("Domain="), false);
  return value.split(";", 1)[0];
}

test("E2E exchange mints the normal host-pinned partner session without Google", async () => {
  const env = environment();
  const server = handler({ exchangeGoogleCodeFn: () => { throw new Error("Google must not run"); } });
  const response = await exchange(server, env, { headers: { "x-actor": "dell" } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true });
  assert.equal(response.headers.get("cache-control"), "no-store");
  const sessionCookie = cookie(response);
  assert.equal(env.OAUTH_KV.writes.length, 1);
  const written = env.OAUTH_KV.writes[0];
  assert.match(written.key, /^dealroom_session:[a-f0-9]{64}$/);
  assert.equal(written.options.expirationTtl, 43200);
  const stored = JSON.parse(written.value);
  assert.equal(stored.props.slug, "joe");
  assert.equal(stored.props.via, "dealroom-cookie");
  assert.equal(stored.props.client_id, "dealroom-pwa");
  assert.equal(stored.origin, `https://${APP_HOST}`);
  assert.equal(stored.e2ePrincipal, "e2e-joe");
  assert.equal(written.value.includes(SECRET), false);

  const bootstrap = await server.fetch(new Request(`https://${APP_HOST}/auth/session`, {
    headers: { cookie: sessionCookie },
  }), env, {});
  assert.equal(bootstrap.status, 200);
  const state = await bootstrap.json();
  assert.deepEqual(state.actor, { slug: "joe", display: "E2E Joe" });
  assert.equal(state.e2e_principal, "e2e-joe");
  assert.equal(state.reauth_required, false);
  assert.ok(state.csrf_token);
  const api = await server.fetch(new Request(`https://${APP_HOST}/api/v1/command-center`, {
    headers: { cookie: sessionCookie },
  }), env, {});
  assert.equal(api.status, 200);
  assert.deepEqual(await api.json(), { viewer: "joe", human: true });
  const replay = await server.fetch(new Request(`https://${CARR_HOST}/auth/session`, {
    headers: { cookie: sessionCookie },
  }), env, {});
  assert.equal(replay.status, 401);
});

test("E2E exchange refuses production even with the secret and mislabeled hosts", async () => {
  for (const overrides of [
    { CARR_ENV: "production" }, { CARR_ENV: undefined }, { CARR_ENV: "STAGING" },
    { APP_HOST: "app.doctorcre.com", DOCTORCRE_APP_HOST: APP_HOST },
    { DOCTORCRE_APP_HOST: "app.doctorcre.com" },
  ]) {
    const env = environment(overrides);
    const response = await exchange(handler(), env);
    assert.equal(response.status, 404, JSON.stringify(overrides));
    assert.equal(response.headers.get("set-cookie"), null);
    assert.equal(env.OAUTH_KV.writes.length, 0);
  }
  const env = environment({ CARR_ENV: "production", APP_HOST: "app.doctorcre.com", DOCTORCRE_APP_HOST: undefined });
  const response = await exchange(handler(), env, { host: "app.doctorcre.com" });
  assert.equal(response.status, 404);
  assert.equal(env.OAUTH_KV.writes.length, 0);
});

test("E2E exchange accepts the zero-byte body supplied by normal HTTP POST clients", async () => {
  const response = await exchange(handler(), environment(), { body: "", headers: { "content-length": "0" } });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true });
  cookie(response);
});

test("E2E exchange has no caller-selected identity and refuses missing or wrong credentials", async () => {
  for (const authorization of ["", "Bearer wrong", "Basic synthetic", SECRET]) {
    const env = environment();
    assert.equal((await exchange(handler(), env, { authorization })).status, 401);
    assert.equal(env.OAUTH_KV.writes.length, 0);
  }
  for (const overrides of [{ E2E_SESSION_SECRET: undefined }, { E2E_SESSION_SECRET: "short" }, { OAUTH_KV: undefined }]) {
    assert.equal((await exchange(handler(), environment(overrides))).status, 503);
  }
  const env = environment();
  assert.equal((await exchange(handler(), env, { body: JSON.stringify({ actor: "dell" }) })).status, 400);
  assert.equal((await handler().fetch(new Request(`https://${APP_HOST}/auth/e2e-session?actor=dell`, {
    method: "POST", headers: { authorization: `Bearer ${SECRET}` },
  }), env, {})).status, 400);
  assert.equal((await exchange(handler(), env, { headers: { origin: "https://elsewhere.example" } })).status, 403);
  assert.equal((await exchange(handler(), env, { method: "GET" })).status, 405);
  assert.equal(env.OAUTH_KV.writes.length, 0);
  assert.equal(isDealroomRequest(new Request(`https://${APP_HOST}/auth/e2e-session`), env), true);
});

test("E2E-marked sessions cannot authenticate in production or outlive normal expiry", async () => {
  const env = environment();
  const sessionCookie = cookie(await exchange(handler(), env));
  const bootstrap = (server, targetEnv) => server.fetch(new Request(`https://${APP_HOST}/auth/session`, {
    headers: { cookie: sessionCookie },
  }), targetEnv, {});
  assert.equal((await bootstrap(handler(), { ...env, CARR_ENV: "production" })).status, 401);
  assert.equal((await bootstrap(handler({ now: () => NOW + 43200 * 1000 }), env)).status, 401);
});

test("staging's MCP origin policy admits its normal browser cookie without widening production", async () => {
  const config = await readFile(new URL("../wrangler.toml", import.meta.url), "utf8");
  const [production, staging] = config.split("\n[env.staging.vars]\n");
  assert.equal(production.includes("CARR_MCP_ALLOWED_ORIGINS"), false);
  const allowed = staging.match(/^CARR_MCP_ALLOWED_ORIGINS = '(.*)'$/m)?.[1];
  assert.deepEqual(JSON.parse(allowed), [`https://${CARR_HOST}`, `https://${APP_HOST}`]);
  for (const host of [CARR_HOST, APP_HOST]) {
    const request = new Request(`https://${host}/mcp`, { headers: { origin: `https://${host}` } });
    assert.equal(mcpOriginRefusal(request, { CARR_MCP_ALLOWED_ORIGINS: allowed }), null);
    assert.equal(mcpOriginRefusal(request, {}).status, 403);
  }
});
