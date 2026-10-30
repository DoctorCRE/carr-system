// Test Deal Room host routing, authentication, and web endpoints.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createDealroomHandler, isDealroomRequest } from "../src/dealroom-web.js";
import { ALLOW_LIST, slugForEmail } from "../src/identity.js";
import { ToolError } from "../src/tool-error.ts";

const WRANGLER_PATH = fileURLToPath(new URL("../wrangler.toml", import.meta.url));
const INDEX_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));
const TEST_PARTNER_EMAIL = "partner@example.test";
const PRODUCTION_HOST = "dealroom.doctorcre.com";
const APP_HOST = "app.doctorcre.com";
const STAGING_HOST = "carr-mcp-staging.joe-bookout-carr-us.workers.dev";
const DOCTORCRE_STAGING_HOST = "doctorcre-app-staging.joe-bookout-carr-us.workers.dev";

class MemoryKv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) {
    const value = this.values.get(key);
    if (value === undefined) return null;
    return options?.type === "json" ? JSON.parse(value) : value;
  }
  async delete(key) { this.values.delete(key); }
}

class ShellAssets {
  async fetch(request) {
    const pathname = new URL(request.url).pathname;
    if (!pathname.startsWith("/public-shell/")) return new Response("missing", { status: 404 });
    return new Response(`<!doctype html><main>Asset fixture ${pathname}</main>`);
  }
}

function env(host = PRODUCTION_HOST) {
  return {
    DEALROOM_HOST: host,
    GOOGLE_CLIENT_ID: "google-client.test",
    GOOGLE_CLIENT_SECRET: "not-a-real-secret",
    OAUTH_KV: new MemoryKv(),
    ASSETS: new ShellAssets(),
  };
}

function setCookies(response) {
  return typeof response.headers.getSetCookie === "function"
    ? response.headers.getSetCookie()
    : [response.headers.get("set-cookie")].filter(Boolean);
}

function namedCookie(response, name) {
  const header = setCookies(response).find((value) => value.startsWith(`${name}=`));
  assert.ok(header, `missing ${name} cookie`);
  return header.split(";", 1)[0];
}

async function login(handler, environment, email, { host = environment.DEALROOM_HOST, returnTo = "/" } = {}) {
  const start = await handler.fetch(new Request(
    `https://${host}/auth/login?return_to=${encodeURIComponent(returnTo)}`,
  ), environment, {});
  assert.equal(start.status, 302);
  const google = new URL(start.headers.get("location"));
  assert.equal(google.hostname, "accounts.google.com");
  assert.equal(google.searchParams.get("redirect_uri"), `https://${host}/auth/callback`);
  const state = google.searchParams.get("state");
  const pendingCookie = namedCookie(start, "__Host-dealroom_oauth");
  const callback = await handler.fetch(new Request(
    `https://${host}/auth/callback?state=${state}&code=stub-code`,
    { headers: { cookie: pendingCookie, "x-test-email": email } },
  ), environment, {});
  return callback;
}

function identityOverrides(email, clock) {
  return {
    slugForEmailFn: value => value === TEST_PARTNER_EMAIL ? "joe" : slugForEmail(value),
    exchangeGoogleCodeFn: async () => ({ id_token: "stub-id-token" }),
    verifyGoogleIdTokenFn: async () => ({ email, email_verified: true, sub: `sub:${email}` }),
    now: () => clock?.now ?? 1_800_000_000_000,
    mcpHandler: async (_request, _env, _ctx, actor) =>
      new Response(JSON.stringify({ surface: "mcp", actor }), { headers: { "content-type": "application/json" } }),
    pipelineHandler: async (_request, _env, _ctx, actor) =>
      new Response(JSON.stringify({ surface: "pipeline", actor }), { headers: { "content-type": "application/json" } }),
  };
}

function identityHandler(email, clock) {
  return createDealroomHandler(identityOverrides(email, clock));
}

test("comparison app routes require authentication and CSRF and name a blocked field", async () => {
  const environment = env();
  environment.WORKSPACE_COMMAND_CENTER_READ_ENABLED = "true";
  environment.DEALROOM_PROGRAM6_ACTIONS_ENABLED = "true";
  environment.VERB_REQUEST_BUDGET_HUMAN = { limit: async () => ({ success: true }) };
  environment.VERB_REQUEST_BUDGET = {
    idFromName: name => name,
    get: () => ({ fetch: async () => Response.json({ allowed: true }) }),
  };
  const invoked = [];
  const handler = createDealroomHandler({ ...identityOverrides(TEST_PARTNER_EMAIL),
    factReviewCaller: async (_env, actor, verb) => {
      invoked.push([actor.slug, verb]);
      if (verb === "prepare-terms-comparison")
        throw new ToolError({ error: "comparison_fact_blocked", field: "term_months", reason: "unresolved" });
      return { ok: true, status: "draft" };
    },
  });
  const url = `https://${PRODUCTION_HOST}/api/v1/fact-review/comparison`;
  assert.equal((await handler.fetch(new Request(url, { method: "POST", body: "{}" }), environment, {})).status, 401);
  const session = namedCookie(await login(handler, environment, TEST_PARTNER_EMAIL), "__Host-dealroom_session");
  const bootstrap = await (await handler.fetch(new Request(`https://${PRODUCTION_HOST}/api/system-work/session`,
    { headers: { cookie: session } }), environment, {})).json();
  const headers = { cookie: session, origin: `https://${PRODUCTION_HOST}`, "sec-fetch-site": "same-origin",
    "content-type": "application/json" };
  assert.equal((await handler.fetch(new Request(url, { method: "POST", headers, body: "{}" }), environment, {})).status, 403);
  headers["x-carr-csrf"] = bootstrap.csrf_token;
  const blocked = await handler.fetch(new Request(url, { method: "POST", headers, body: "{}" }), environment, {});
  assert.deepEqual(await blocked.json(), { error: "comparison_fact_blocked", field: "term_months", reason: "unresolved" });
  const validated = await handler.fetch(new Request(url + "/validate", { method: "POST", headers, body: "{}" }), environment, {});
  assert.equal((await validated.json()).status, "draft");
  assert.deepEqual(invoked, [["joe", "prepare-terms-comparison"], ["joe", "validate-terms-comparison"]]);
});

test("browser MCP passes the verified session key independently of caller budget hints", async () => {
  const email = TEST_PARTNER_EMAIL, environment = env();
  const sessions = [];
  const handler = createDealroomHandler({ ...identityOverrides(email),
    mcpHandler: async (_request, _env, _ctx, actor, sessionKey) => {
      assert.equal(actor.human, true);
      assert.ok(environment.OAUTH_KV.values.has(sessionKey));
      sessions.push(sessionKey);
      return new Response("{}");
    },
  });
  const first = namedCookie(await login(handler, environment, email), "__Host-dealroom_session");
  const send = cookie => handler.fetch(new Request(`https://${PRODUCTION_HOST}/mcp?budget=agent`, {
    method: "POST", headers: { cookie, origin: `https://${PRODUCTION_HOST}`, "x-session-key": "caller-choice" },
  }), environment, {});
  assert.equal((await send(first)).status, 200);
  assert.equal((await send(first)).status, 200);
  const second = namedCookie(await login(handler, environment, email), "__Host-dealroom_session");
  assert.equal((await send(second)).status, 200);
  assert.equal(sessions[0], sessions[1]);
  assert.notEqual(sessions[0], sessions[2]);
  assert.notEqual(sessions[0], "caller-choice");
});

test("Deal Room host is explicit per environment and request matching fails closed", async () => {
  const wrangler = await readFile(WRANGLER_PATH, "utf8");
  assert.match(wrangler, /\[vars\]\nCARR_ENV = "production"\nAPP_HOST = "app\.doctorcre\.com"\nLEGACY_DEALROOM_HOST = "dealroom\.doctorcre\.com"/);
  assert.match(wrangler,
    /\[env\.staging\.vars\]\nCARR_ENV = "staging"\nAPP_HOST = "carr-mcp-staging\.joe-bookout-carr-us\.workers\.dev"[\s\S]*DOCTORCRE_APP_HOST = "doctorcre-app-staging\.joe-bookout-carr-us\.workers\.dev"/);

  assert.equal(isDealroomRequest(new Request(`https://${PRODUCTION_HOST}/`), { DEALROOM_HOST: PRODUCTION_HOST }), true);
  assert.equal(isDealroomRequest(new Request(`https://${APP_HOST}/`), { APP_HOST, LEGACY_DEALROOM_HOST: PRODUCTION_HOST }), true);
  assert.equal(isDealroomRequest(new Request(`https://${PRODUCTION_HOST}/`), { APP_HOST, LEGACY_DEALROOM_HOST: PRODUCTION_HOST }), true);
  assert.equal(isDealroomRequest(new Request(`https://${STAGING_HOST}/`), { DEALROOM_HOST: STAGING_HOST }), true);
  assert.equal(isDealroomRequest(new Request(`https://${DOCTORCRE_STAGING_HOST}/`), {
    APP_HOST: STAGING_HOST, DOCTORCRE_APP_HOST: DOCTORCRE_STAGING_HOST,
  }), true);
  assert.equal(isDealroomRequest(new Request(`https://${STAGING_HOST}/`), { DEALROOM_HOST: PRODUCTION_HOST }), false);
  assert.equal(isDealroomRequest(new Request(`https://${PRODUCTION_HOST}/`), {}), false);
  assert.equal(isDealroomRequest(new Request(`https://${PRODUCTION_HOST}/`), { DEALROOM_HOST: "https://bad.example" }), false);

  const handler = identityHandler("joe.bookout.carr.us@gmail.com");
  const mismatch = await handler.fetch(new Request(`https://${STAGING_HOST}/auth/login`), env(PRODUCTION_HOST), {});
  assert.equal(mismatch.status, 404);
  const missing = await handler.fetch(new Request(`https://${PRODUCTION_HOST}/auth/login`), {
    ...env(), DEALROOM_HOST: undefined,
  }, {});
  assert.equal(missing.status, 404);
});

test("Jev deal read requires a partner session and same-origin one-deal request", async () => {
  const environment = env();
  environment.VERB_REQUEST_BUDGET_HUMAN = { limit: async () => ({ success: true }) };
  environment.VERB_REQUEST_BUDGET = {
    idFromName: key => key,
    get: () => ({ fetch: async () => Response.json({ allowed: true }) }),
  };
  environment.TYPESAFE_API_KEY = "synthetic";
  let readCount = 0;
  const handler = createDealroomHandler({
    ...identityOverrides("joe.bookout.carr.us@gmail.com"),
    jevDealRecordReader: async (deal) => { readCount++; return { deal_id: deal }; },
    jevDealRead: async () => ({ schema: "carr.jev-deal-reading.v1", judged: false,
      reason: "insufficient_recorded_evidence" }),
  });
  const deal = "00000000-0000-4000-8000-000000000001";
  const url = `https://${PRODUCTION_HOST}/api/v1/jev-deal-reading`;
  const anonymous = await handler.fetch(new Request(url, { method: "POST", body: JSON.stringify({ deal }) }), environment, {});
  assert.equal(anonymous.status, 401);
  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com");
  const cookie = namedCookie(callback, "__Host-dealroom_session");
  const post = (origin, body) => handler.fetch(new Request(url, { method: "POST",
    headers: { cookie, origin, "content-type": "application/json" }, body: JSON.stringify(body),
  }), environment, {});
  assert.equal((await post("https://elsewhere.example", { deal })).status, 403);
  assert.equal((await post(`https://${PRODUCTION_HOST}`, { deal: "not-an-id" })).status, 400);
  assert.equal(readCount, 0);
  const response = await post(`https://${PRODUCTION_HOST}`, { deal });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).reason, "insufficient_recorded_evidence");
  assert.equal(readCount, 1);
});

test("Jev refuses an exhausted browser budget before record or external AI work", async () => {
  const environment = env();
  environment.VERB_REQUEST_BUDGET_HUMAN = { limit: async () => ({ success: true }) };
  environment.VERB_REQUEST_BUDGET = {
    idFromName: key => key,
    get: () => ({ fetch: async () => Response.json({ allowed: false, retryAfter: 23 }) }),
  };
  const handler = createDealroomHandler({
    ...identityOverrides(TEST_PARTNER_EMAIL),
    jevDealRecordReader: async () => assert.fail("refused Jev request read deal records"),
    jevDealRead: async () => assert.fail("refused Jev request called external AI"),
  });
  const callback = await login(handler, environment, TEST_PARTNER_EMAIL);
  const response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/v1/jev-deal-reading", {
    method: "POST", headers: { origin: "https://dealroom.doctorcre.com",
      cookie: namedCookie(callback, "__Host-dealroom_session") },
    body: JSON.stringify({ deal: "00000000-0000-4000-8000-000000000001" }),
  }), environment, {});
  assert.equal(response.status, 429);
  assert.equal(response.headers.get("retry-after"), "23");
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("independent DoctorCRE host owns its OAuth origin and cannot replay sessions across allowed hosts", async () => {
  const environment = {
    ...env(STAGING_HOST),
    APP_HOST: STAGING_HOST,
    DOCTORCRE_APP_HOST: DOCTORCRE_STAGING_HOST,
    DEALROOM_PROGRAM6_ACTIONS_ENABLED: "true",
  };
  let tourHost = null;
  const handler = createDealroomHandler({
    ...identityOverrides("joe.bookout.carr.us@gmail.com"),
    tourHandler: { fetch: async (_request, envArg) => {
      tourHost = envArg.APP_HOST;
      return new Response(JSON.stringify({ ok: true }));
    } },
  });
  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com", {
    host: DOCTORCRE_STAGING_HOST,
    returnTo: "/system-work.html",
  });
  assert.equal(callback.headers.get("location"), `https://${DOCTORCRE_STAGING_HOST}/system-work.html`);
  const session = namedCookie(callback, "__Host-dealroom_session");

  const bootstrapResponse = await handler.fetch(new Request(
    `https://${DOCTORCRE_STAGING_HOST}/api/system-work/session`, { headers: { cookie: session } },
  ), environment, {});
  assert.equal(bootstrapResponse.status, 200);
  const bootstrap = await bootstrapResponse.json();
  const target = { action: "accept-ready-plan", human_ref: "WR-41", base_version: 3,
    idempotency_key: "31000000-0000-0000-0000-000000000041", plan_hash: `sha256:${"a".repeat(64)}` };
  const accepted = await handler.fetch(new Request(
    `https://${DOCTORCRE_STAGING_HOST}/api/system-work/challenge`, {
      method: "POST",
      headers: { cookie: session, origin: `https://${DOCTORCRE_STAGING_HOST}`,
        "sec-fetch-site": "same-origin", "content-type": "application/json", "x-carr-csrf": bootstrap.csrf_token },
      body: JSON.stringify(target),
    },
  ), environment, {});
  assert.equal(accepted.status, 200);

  const tour = await handler.fetch(new Request(
    `https://${DOCTORCRE_STAGING_HOST}/api/tours/library`, { headers: { cookie: session } },
  ), environment, {});
  assert.equal(tour.status, 200);
  assert.equal(tourHost, DOCTORCRE_STAGING_HOST);

  const crossHostReplay = await handler.fetch(new Request(
    `https://${STAGING_HOST}/api/system-work/session`, { headers: { cookie: session } },
  ), environment, {});
  assert.equal(crossHostReplay.status, 401);
});

test("legacy Deal Room host redirects only browser document paths to the canonical app", async () => {
  const environment = { ...env(APP_HOST), APP_HOST, LEGACY_DEALROOM_HOST: PRODUCTION_HOST };
  const handler = identityHandler("joe.bookout.carr.us@gmail.com");
  const loginStart = await handler.fetch(new Request(`https://${APP_HOST}/auth/login`), environment, {});
  assert.equal(new URL(loginStart.headers.get("location")).searchParams.get("redirect_uri"), `https://${APP_HOST}/auth/callback`);
  for (const method of ["GET", "HEAD"]) {
    const response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}/?stale=1`, { method }), environment, {});
    assert.equal(response.status, 302, method);
    assert.equal(response.headers.get("location"), `https://${APP_HOST}/deals`, method);
  }
  for (const [method, path] of [["POST", "/"], ["GET", "/api/system-work/session"], ["POST", "/mcp"], ["GET", "/manifest.webmanifest"]]) {
    const response = await handler.fetch(new Request(`https://${PRODUCTION_HOST}${path}`, { method }), environment, {});
    assert.equal(response.status, 404, `${method} ${path} must fail closed`);
    assert.equal(response.headers.get("location"), null, `${method} ${path} must not redirect`);
  }
});

test("shared staging origin routes only exact Deal Room surfaces", () => {
  const environment = { DEALROOM_HOST: STAGING_HOST };
  const request = (path, headers = {}) => new Request(`https://${STAGING_HOST}${path}`, { headers });

  for (const path of ["/", "/index.html", "/leads", "/leads.html", "/system-work.html", "/auth/login", "/auth/callback",
    "/api/system-work/session", "/css/app.css", "/js/app.js", "/data/board-seed.json",
    "/manifest.webmanifest", "/sw.js", "/offline.html", "/icons/dealroom.svg"]) {
    assert.equal(isDealroomRequest(request(path), environment), true, path);
  }
  for (const path of ["/release", "/health", "/authorize", "/token", "/register", "/callback",
    "/.well-known/oauth-authorization-server", "/unknown"]) {
    assert.equal(isDealroomRequest(request(path), environment), false, path);
  }

  for (const path of ["/mcp", "/pipeline/changes"]) {
    assert.equal(isDealroomRequest(request(path), environment), false, `${path} without browser session`);
    assert.equal(isDealroomRequest(request(path, { authorization: "Bearer provider-token" }), environment), false,
      `${path} OAuth bearer`);
    assert.equal(isDealroomRequest(request(path, { cookie: "__Host-dealroom_session=browser-session" }), environment), true,
      `${path} browser cookie`);
    assert.equal(isDealroomRequest(request(path, {
      cookie: "__Host-dealroom_session=browser-session", authorization: "Bearer provider-token",
    }), environment), false, `${path} bearer wins over browser cookie`);
  }
});

test("authenticated Lead Board route serves its own asset and preserves sign-in return", async () => {
  const environment = env();
  environment.ASSETS = {
    async fetch(request) {
      const pathname = new URL(request.url).pathname;
      if (pathname === "/leads.html") {
        return new Response("<!doctype html><title>Lead Board</title><main>Lead Board</main>", {
          headers: { "content-type": "text/html" },
        });
      }
      return new ShellAssets().fetch(request);
    },
  };
  const handler = identityHandler("joe.bookout.carr.us@gmail.com");

  const signedOut = await handler.fetch(new Request("https://dealroom.doctorcre.com/leads", {
    headers: { accept: "text/html" },
  }), environment, {});
  assert.equal(signedOut.status, 302);
  const loginUrl = new URL(signedOut.headers.get("location"));
  assert.equal(loginUrl.pathname, "/auth/login");
  assert.equal(loginUrl.searchParams.get("return_to"), "/leads");

  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com", { returnTo: "/leads" });
  assert.equal(callback.headers.get("location"), "https://dealroom.doctorcre.com/leads");
  const session = namedCookie(callback, "__Host-dealroom_session");
  const board = await handler.fetch(new Request("https://dealroom.doctorcre.com/leads", {
    headers: { cookie: session, accept: "text/html" },
  }), environment, {});
  assert.equal(board.status, 200);
  assert.match(await board.text(), /<main>Lead Board<\/main>/);
  assert.equal(board.headers.get("cache-control"), "no-cache");
  assert.match(board.headers.get("content-security-policy"), /script-src 'self'/);
});

test("machine-token MCP dispatch precedes browser routing on the shared host", async () => {
  const index = await readFile(INDEX_PATH, "utf8");
  const machineDoors = index.indexOf('if (url.pathname === "/mcp")');
  const browserDoor = index.indexOf("if (isDealroomRequest(request, env))");
  assert.ok(machineDoors >= 0 && browserDoor > machineDoors,
    "probe/review/Hermes/agent/local MCP doors must run before the Deal Room browser door");
});

test("staging host controls OAuth callback, safe return, reauth, signout, and CSRF origin", async () => {
  const environment = env(STAGING_HOST);
  environment.DEALROOM_PROGRAM6_ACTIONS_ENABLED = "true";
  const handler = identityHandler("joe.bookout.carr.us@gmail.com");

  const unauthenticatedReauth = await handler.fetch(new Request(
    `https://${STAGING_HOST}/auth/reauth?return_to=%2Fsystem-work`,
  ), environment, {});
  assert.equal(unauthenticatedReauth.status, 302);
  assert.match(unauthenticatedReauth.headers.get("location"),
    new RegExp(`^https://${STAGING_HOST}/auth/login\\?`));

  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com", {
    host: STAGING_HOST,
    returnTo: `https://${PRODUCTION_HOST}/not-on-staging`,
  });
  assert.equal(callback.status, 302);
  assert.equal(callback.headers.get("location"), `https://${STAGING_HOST}/`);
  const session = namedCookie(callback, "__Host-dealroom_session");

  const reauth = await handler.fetch(new Request(
    `https://${STAGING_HOST}/auth/reauth?return_to=%2Fsystem-work`, { headers: { cookie: session } },
  ), environment, {});
  assert.equal(reauth.status, 302);
  assert.equal(new URL(reauth.headers.get("location")).searchParams.get("redirect_uri"),
    `https://${STAGING_HOST}/auth/callback`);

  const bootstrap = await (await handler.fetch(new Request(
    `https://${STAGING_HOST}/api/system-work/session`, { headers: { cookie: session } },
  ), environment, {})).json();
  const target = { action: "accept-ready-plan", human_ref: "WR-41", base_version: 3,
    idempotency_key: "30000000-0000-0000-0000-000000000041", plan_hash: `sha256:${"a".repeat(64)}` };
  const headers = { cookie: session, "content-type": "application/json", "x-carr-csrf": bootstrap.csrf_token,
    "sec-fetch-site": "same-origin" };
  let response = await handler.fetch(new Request(`https://${STAGING_HOST}/api/system-work/challenge`, {
    method: "POST", headers: { ...headers, origin: `https://${PRODUCTION_HOST}` }, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).reason, "origin_mismatch");
  response = await handler.fetch(new Request(`https://${STAGING_HOST}/api/system-work/challenge`, {
    method: "POST", headers: { ...headers, origin: `https://${STAGING_HOST}` }, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 200);

  const signedOut = await handler.fetch(new Request(`https://${STAGING_HOST}/auth/signout`, {
    method: "POST", headers: { cookie: session, origin: `https://${STAGING_HOST}`,
      "sec-fetch-site": "same-origin", "x-carr-csrf": bootstrap.csrf_token },
  }), environment, {});
  assert.equal(signedOut.status, 302);
  assert.equal(signedOut.headers.get("location"), `https://${STAGING_HOST}/`);
});

test("Deal Room gate refuses a verified non-partner without returning deal data", async () => {
  const environment = env();
  const callback = await login(identityHandler("someone@example.com"), environment, "someone@example.com");
  assert.equal(callback.status, 403);
  assert.match(callback.headers.get("content-type"), /^text\/html/);
  const html = await callback.text();
  assert.doesNotMatch(html, /pipeline|Deal Alpha|someone@example\.com/);
  assert.equal(setCookies(callback).some((cookie) => cookie.startsWith("__Host-dealroom_session=")), false);
});

test("Deal Room gate admits exactly both partner identities with their existing actors", async () => {
  for (const [email, slug, display] of [
    ["joe.bookout.carr.us@gmail.com", "joe", "Joe"],
    ["dell.mccraney.carr.us@gmail.com", "dell", "Dell"],
  ]) {
    const environment = env();
    const callback = await login(identityHandler(email), environment, email);
    assert.equal(callback.status, 302);
    const session = namedCookie(callback, "__Host-dealroom_session");
    assert.match(setCookies(callback).find((cookie) => cookie.startsWith("__Host-dealroom_session=")),
      /; Secure; HttpOnly; SameSite=Lax/);
    const api = await identityHandler(email).fetch(new Request("https://dealroom.doctorcre.com/mcp",
      { method: "POST", headers: { cookie: session, origin: "https://dealroom.doctorcre.com" } }), environment, {});
    assert.equal(api.status, 200);
    assert.deepEqual((await api.json()).actor,
      { slug, display, human: true, via: "dealroom-cookie", client_id: "dealroom-pwa",
        human_slug: null, sponsoring_human_slug: null, sponsor_required: false });
  }
});

test("MCP operation-bound CSRF header refuses a cookie switch before dispatch", async () => {
  const email = Object.keys(ALLOW_LIST).find(email => ALLOW_LIST[email] === "joe"), environment = env();
  let dispatched = 0;
  const handler = createDealroomHandler({ ...identityOverrides(email),
    mcpHandler: async () => { dispatched++; return new Response("{}"); },
  });
  const first = namedCookie(await login(handler, environment, email), "__Host-dealroom_session");
  const session = async cookie => (await (await handler.fetch(new Request(`https://${PRODUCTION_HOST}/auth/session`,
    { headers: { cookie } }), environment, {})).json());
  const firstRead = await session(first);
  const second = namedCookie(await login(handler, environment, email), "__Host-dealroom_session");
  const secondRead = await session(second);
  assert.notEqual(firstRead.csrf_token, secondRead.csrf_token);
  const send = (cookie, csrf) => handler.fetch(new Request(`https://${PRODUCTION_HOST}/mcp`, {
    method: "POST", headers: { cookie, origin: `https://${PRODUCTION_HOST}`, "content-type": "application/json",
      ...(csrf === undefined ? {} : { "x-carr-csrf": csrf }) },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "set-next-step", arguments: {} } }),
  }), environment, {});
  const refused = await send(second, firstRead.csrf_token);
  assert.equal(refused.status, 403);
  assert.equal((await refused.json()).reason, "csrf_mismatch");
  assert.equal(dispatched, 0, "the stale operation cannot acquire the new cookie's actor");
  assert.equal((await send(first, firstRead.csrf_token)).status, 200);
  assert.equal((await send(second, secondRead.csrf_token)).status, 200);
  assert.equal((await send(second, undefined)).status, 200, "legacy clients may omit the opt-in binding header");
  assert.equal(dispatched, 3);
});

test("opaque cookie session round-trips, refreshes, expires, and signs out", async () => {
  const clock = { now: 1_800_000_000_000 };
  const environment = env();
  const handler = identityHandler("joe.bookout.carr.us@gmail.com", clock);
  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com");
  const session = namedCookie(callback, "__Host-dealroom_session");
  assert.doesNotMatch(session, /joe|gmail/i);

  environment.DEALROOM_PROGRAM6_ACTIONS_ENABLED = "true";
  const bootstrap = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {});
  const { csrf_token: csrfToken } = await bootstrap.json();

  let api = await handler.fetch(new Request("https://dealroom.doctorcre.com/pipeline/changes",
    { headers: { cookie: session } }), environment, {});
  assert.equal(api.status, 200);
  assert.equal((await api.json()).actor.slug, "joe");

  clock.now += 11.5 * 60 * 60 * 1000;
  api = await handler.fetch(new Request("https://dealroom.doctorcre.com/pipeline/changes",
    { headers: { cookie: session } }), environment, {});
  assert.equal(api.status, 200);
  assert.ok(setCookies(api).some((cookie) => cookie.startsWith("__Host-dealroom_session=")));

  const signedOut = await handler.fetch(new Request("https://dealroom.doctorcre.com/auth/signout",
    { method: "POST", headers: { cookie: session, origin: "https://dealroom.doctorcre.com",
      "sec-fetch-site": "same-origin", "x-carr-csrf": csrfToken } }), environment, {});
  assert.equal(signedOut.status, 302);
  api = await handler.fetch(new Request("https://dealroom.doctorcre.com/pipeline/changes",
    { headers: { cookie: session } }), environment, {});
  assert.equal(api.status, 401);

  const expiryEnv = env();
  const expiryClock = { now: 1_800_000_000_000 };
  const expiryHandler = identityHandler("joe.bookout.carr.us@gmail.com", expiryClock);
  const expiryCallback = await login(expiryHandler, expiryEnv, "joe.bookout.carr.us@gmail.com");
  const expiringSession = namedCookie(expiryCallback, "__Host-dealroom_session");
  expiryClock.now += 7 * 24 * 60 * 60 * 1000 + 1;
  api = await expiryHandler.fetch(new Request("https://dealroom.doctorcre.com/pipeline/changes",
    { headers: { cookie: expiringSession } }), expiryEnv, {});
  assert.equal(api.status, 401);
});

test("Program 6 browser bootstrap is feature-gated and its typed boundary refuses cross-site or unbound writes", async () => {
  const environment = env();
  const handler = identityHandler("joe.bookout.carr.us@gmail.com");
  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com");
  const session = namedCookie(callback, "__Host-dealroom_session");
  let response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 404);
  assert.equal((await response.json()).error, "program6_actions_disabled");

  environment.DEALROOM_PROGRAM6_ACTIONS_ENABLED = "true";
  response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {});
  assert.equal(response.status, 200);
  const bootstrap = await response.json();
  assert.equal(bootstrap.actor.slug, "joe");
  assert.match(bootstrap.csrf_token, /^[0-9a-f]{64}$/);
  assert.equal(bootstrap.reauth_required, false);
  assert.equal(bootstrap.challenge_ttl_seconds, 300);

  const challengeBody = JSON.stringify({ action: "accept-ready-plan", human_ref: "WR-41", base_version: 3,
    idempotency_key: "10000000-0000-0000-0000-000000000041",
    plan_hash: `sha256:${"a".repeat(64)}` });
  const headers = { cookie: session, "content-type": "application/json", "x-carr-csrf": bootstrap.csrf_token };
  response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/challenge", {
    method: "POST", headers, body: challengeBody,
  }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).reason, "origin_mismatch");

  response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/challenge", {
    method: "POST", headers: { ...headers, origin: "https://dealroom.doctorcre.com", "sec-fetch-site": "same-site" }, body: challengeBody,
  }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).reason, "fetch_metadata_mismatch");

  response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/challenge", {
    method: "POST", headers: { ...headers, origin: "https://dealroom.doctorcre.com", "sec-fetch-site": "same-origin" }, body: challengeBody,
  }), environment, {});
  assert.equal(response.status, 200);
  assert.match((await response.json()).challenge, /^[0-9a-f]{64}$/);
});

test("reauthentication is session-bound, and approval challenges are one-time and exact", async () => {
  const clock = { now: 1_800_000_000_000 };
  const environment = env();
  environment.DEALROOM_PROGRAM6_ACTIONS_ENABLED = "true";
  const handler = identityHandler("joe.bookout.carr.us@gmail.com", clock);
  const callback = await login(handler, environment, "joe.bookout.carr.us@gmail.com");
  const session = namedCookie(callback, "__Host-dealroom_session");
  const bootstrap = await (await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {})).json();
  const target = { action: "accept-outcome-feedback", human_ref: "WR-41", base_version: 3,
    idempotency_key: "20000000-0000-0000-0000-000000000041",
    feedback_hash: `sha256:${"b".repeat(64)}` };
  const csrfHeaders = { cookie: session, origin: "https://dealroom.doctorcre.com", "sec-fetch-site": "same-origin",
    "content-type": "application/json", "x-carr-csrf": bootstrap.csrf_token };
  let response = await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/challenge", {
    method: "POST", headers: csrfHeaders, body: JSON.stringify(target),
  }), environment, {});
  const { challenge } = await response.json();

  // Model Cloudflare KV's non-atomic visibility: both concurrent-looking
  // requests may still read the same challenge even after delete. The atomic
  // redemption seam below, not KV, must permit exactly one.
  const kvDelete = environment.OAUTH_KV.delete.bind(environment.OAUTH_KV);
  environment.OAUTH_KV.delete = async (key) => {
    if (!String(key).startsWith("dealroom_action_challenge:")) await kvDelete(key);
  };

  const redeemed = new Set();
  const controller = createDealroomHandler({
    ...identityOverrides("joe.bookout.carr.us@gmail.com", clock),
    program6Handler: async (request, envArg, _ctx, actor, sessionState) => {
      const parsed = await request.json();
      const { authorizeProgram6Action } = await import("../src/dealroom-web.js");
      const refusal = await authorizeProgram6Action({ request, env: envArg, actor, session: sessionState,
        action: "accept-outcome-feedback", args: parsed, now: clock.now,
        redeemChallenge: async ({ tokenDigest }) => {
          if (redeemed.has(tokenDigest)) return { ok: false };
          redeemed.add(tokenDigest); return { ok: true };
        } });
      return refusal || new Response(JSON.stringify({ ok: true }));
    },
  });
  const approvalHeaders = { ...csrfHeaders, "x-carr-action-challenge": challenge };
  response = await controller.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/accept-outcome-feedback", {
    method: "POST", headers: approvalHeaders, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 200);
  response = await controller.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/accept-outcome-feedback", {
    method: "POST", headers: approvalHeaders, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 403);
  assert.equal((await response.json()).error, "invalid_action_challenge");
  assert.equal(redeemed.size, 1);

  clock.now += 10 * 60 * 1000 + 1;
  response = await controller.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/accept-outcome-feedback", {
    method: "POST", headers: { ...csrfHeaders, "x-carr-action-challenge": "not-used" }, body: JSON.stringify(target),
  }), environment, {});
  assert.equal(response.status, 401);
  assert.equal((await response.json()).error, "reauth_required");

  const reauth = await handler.fetch(new Request("https://dealroom.doctorcre.com/auth/reauth?return_to=%2Fsystem-work",
    { headers: { cookie: session } }), environment, {});
  assert.equal(reauth.status, 302);
  const reauthGoogle = new URL(reauth.headers.get("location"));
  const reauthState = reauthGoogle.searchParams.get("state");
  const reauthPending = namedCookie(reauth, "__Host-dealroom_oauth");
  const reauthCallback = await handler.fetch(new Request(
    `https://dealroom.doctorcre.com/auth/callback?state=${reauthState}&code=reauth-code`,
    { headers: { cookie: `${session}; ${reauthPending}` } },
  ), environment, {});
  assert.equal(reauthCallback.status, 302);
  assert.equal(reauthCallback.headers.get("location"), "https://dealroom.doctorcre.com/system-work");
  const refreshed = await (await handler.fetch(new Request("https://dealroom.doctorcre.com/api/system-work/session",
    { headers: { cookie: session } }), environment, {})).json();
  assert.equal(refreshed.reauth_required, false);
});

test("manifest and service worker are public with PWA-safe headers", async () => {
  const environment = env();
  const handler = identityHandler("joe.bookout.carr.us@gmail.com");
  const manifestResponse = await handler.fetch(new Request("https://dealroom.doctorcre.com/manifest.webmanifest"), environment, {});
  assert.equal(manifestResponse.status, 200);
  assert.match(manifestResponse.headers.get("content-type"), /^application\/manifest\+json/);

  const swResponse = await handler.fetch(new Request("https://dealroom.doctorcre.com/sw.js"), environment, {});
  assert.equal(swResponse.status, 200);
  assert.match(swResponse.headers.get("content-type"), /^application\/javascript/);
  assert.equal(swResponse.headers.get("service-worker-allowed"), "/");
  assert.match(swResponse.headers.get("content-security-policy"), /script-src 'self'/);
  assert.doesNotMatch(swResponse.headers.get("content-security-policy"), /unsafe-inline/);
  assert.equal(swResponse.headers.get("x-content-type-options"), "nosniff");
  assert.equal(swResponse.headers.get("x-frame-options"), "DENY");
  const csp = swResponse.headers.get("content-security-policy");
  assert.match(csp, /(^|; )connect-src 'self'(;|$)/, "connect-src is same-origin only");
  assert.doesNotMatch(csp, /127\.0\.0\.1|localhost/, "no loopback target in the hosted CSP");
  assert.equal(swResponse.headers.get("referrer-policy"), "no-referrer");

});
