import test from "node:test";
import assert from "node:assert/strict";
import { createReportsWebHandler, isReportsHostRequest, isReportsRequest, REPORTS_ORIGIN } from "../src/reports-web.js";


class ReportAssets {
  constructor() { this.requests = []; }
  async fetch(request) {
    this.requests.push(request);
    const pathname = new URL(request.url).pathname;
    return new Response(`Asset fixture ${pathname}`, { headers: { "access-control-allow-origin": "*" } });
  }
}

const request = (path, options = {}) => new Request(`${REPORTS_ORIGIN}${path}`, options);
const sameOriginJson = { origin: REPORTS_ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json" };
const cookies = response => typeof response.headers.getSetCookie === "function" ? response.headers.getSetCookie() : [response.headers.get("set-cookie")].filter(Boolean);
const CARR_STAGING_HOST = "carr-mcp-staging.joe-bookout-carr-us.workers.dev";
const APP_STAGING_HOST = "doctorcre-app-staging.joe-bookout-carr-us.workers.dev";
const STAGING = { CARR_ENV: "staging", APP_HOST: CARR_STAGING_HOST, DOCTORCRE_APP_HOST: APP_STAGING_HOST };

async function sha256Digest(value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return "sha256:" + [...digest].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function handler(overrides = {}) {
  return createReportsWebHandler({
    exchangeShareTokenFn: async () => ({ ok: true }),
    readShareFn: async () => ({ ok: true, data: { title: "Client tour", items: [] } }),
    readMapFn: async () => ({ ok: true, data: { points: [] } }),
    ...overrides,
  });
}

test("reports adapter is limited to the reports origin and scoped routes", async () => {
  assert.equal(isReportsRequest(request("/share")), true);
  assert.equal(isReportsRequest(request("/api/share/report")), true);
  assert.equal(isReportsRequest(request("/api/share/map")), true);
  for (const path of ["/api/share/pdf", "/api/share/reaction", "/sw.js"])
    assert.equal(isReportsRequest(request(path)), false, path);
  for (const path of ["/api/share/feedback", "/api/share/shortlist", "/api/share/comment"])
    assert.equal(isReportsRequest(request(path)), true, path);
  assert.equal(isReportsRequest(new Request("https://app.doctorcre.com/share")), false);
  assert.equal(isReportsHostRequest(request("/mcp")), true);
  assert.equal(isReportsHostRequest(request("/oauth/authorize")), true);
  assert.equal(isReportsHostRequest(new Request("https://api.doctorcre.com/mcp")), false);

  let exchanges = 0;
  const surface = handler({ exchangeShareTokenFn: async () => { exchanges += 1; return { ok: true }; } });
  const assets = new ReportAssets();
  const share = await surface.fetch(request("/share", { headers: { cookie: "__Host-tour_share_session=not-forwarded", "x-private-header": "not-forwarded" } }), { ASSETS: assets });
  assert.equal(share.status, 200);
  assert.equal(exchanges, 0);
  assert.equal(assets.requests[0].headers.get("cookie"), null);
  assert.equal(assets.requests[0].headers.get("x-private-header"), null);
  assert.equal(share.headers.get("access-control-allow-origin"), null);
  assert.match(share.headers.get("content-security-policy"), /worker-src 'self'/);
  assert.equal((await surface.fetch(request("/api/share/pdf"), {})).status, 404);
});

test("staging reports use the existing share exchange and scoped routes on staging browser hosts", async () => {
  for (const host of [CARR_STAGING_HOST, APP_STAGING_HOST]) {
    const origin = `https://${host}`;
    const exchange = new Request(`${origin}/api/share/exchange`, { method: "POST",
      headers: { origin, "sec-fetch-site": "same-origin", "content-type": "application/json" },
      body: JSON.stringify({ token: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ" }) });
    assert.equal(isReportsRequest(exchange, STAGING), true);
    assert.equal(isReportsHostRequest(exchange, STAGING), true);
    const response = await handler().fetch(exchange, STAGING);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true });
    const cookie = response.headers.get("set-cookie");
    assert.match(cookie, /^__Host-tour_share_session=[A-Za-z0-9_-]{43}; Path=\/; Secure; HttpOnly; SameSite=Lax$/);
    assert.equal(cookie.includes("Domain="), false);
    const read = await handler().fetch(new Request(`${origin}/api/share/report`, { headers: { cookie: cookie.split(";", 1)[0] } }), STAGING);
    assert.equal(read.status, 200);
    assert.deepEqual(await read.json(), { data: { title: "Client tour", items: [] } });
    assert.equal(read.headers.get("access-control-allow-origin"), null);
    let feedbackInput;
    const feedback = await handler({ shortlistFn: async value => { feedbackInput = value; return { ok: true, data: { saved: true } }; } })
      .fetch(new Request(`${origin}/api/share/shortlist`, { method: "POST",
        headers: { origin, "sec-fetch-site": "same-origin", "content-type": "application/json", cookie: cookie.split(";", 1)[0] },
        body: JSON.stringify({ projection_ref: `projection:public:${"p".repeat(32)}`,
          property_ref: `property:public:${"a".repeat(32)}`, shortlisted: true,
          idempotency_key: "10000000-0000-4000-8000-000000000001" }),
      }), STAGING);
    assert.equal(feedback.status, 200);
    assert.deepEqual(await feedback.json(), { data: { saved: true } });
    assert.equal(feedbackInput.sessionDigest, await sha256Digest(cookie.split(";", 1)[0].slice("__Host-tour_share_session=".length)));
    for (const path of ["/auth/e2e-session", "/auth/session", "/mcp", "/oauth/authorize", "/control-room", "/api/share/pdf"]) {
      assert.equal(isReportsHostRequest(new Request(`${origin}${path}`), STAGING), false, path);
    }
  }
});

test("production refuses staging reports even when staging hosts are configured", async () => {
  const request = new Request(`https://${APP_STAGING_HOST}/api/share/report`, {
    headers: { cookie: "__Host-tour_share_session=synthetic-session" },
  });
  for (const env of [{ ...STAGING, CARR_ENV: "production" }, { ...STAGING, CARR_ENV: undefined },
    { ...STAGING, APP_HOST: "app.doctorcre.com" }, { ...STAGING, DOCTORCRE_APP_HOST: "app.doctorcre.com" }]) {
    assert.equal(isReportsRequest(request, env), false);
    assert.equal(isReportsHostRequest(request, env), false);
    assert.equal((await handler().fetch(request, env)).status, 404);
  }
  assert.equal(isReportsHostRequest(new Request(`${REPORTS_ORIGIN}/mcp`), STAGING), true);
  assert.equal(isReportsRequest(new Request("https://app.doctorcre.com/api/share/report"), STAGING), false);
  const productionPost = await handler().fetch(new Request(`${REPORTS_ORIGIN}/api/share/exchange`, {
    method: "POST", headers: { ...sameOriginJson, origin: `https://${APP_STAGING_HOST}` },
    body: JSON.stringify({ token: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ" }),
  }), STAGING);
  assert.equal(productionPost.status, 403);
});

test("staging report mutations still require their exact browser origin", async () => {
  for (const origin of [REPORTS_ORIGIN, `https://${CARR_STAGING_HOST}`, "https://elsewhere.example"]) {
    const response = await handler().fetch(new Request(`https://${APP_STAGING_HOST}/api/share/exchange`, {
      method: "POST", headers: { ...sameOriginJson, origin },
      body: JSON.stringify({ token: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ" }),
    }), STAGING);
    assert.equal(response.status, 403);
    assert.equal(response.headers.get("set-cookie"), null);
  }
});

test("authenticated map read forwards only the opaque session digest", async () => {
  let input;
  const surface = handler({ readMapFn: async value => { input = value; return { ok: true, data: { points: [] } }; } });
  const cookie = "__Host-tour_share_session=session_abcdefghijklmnopqrstuvwxyz";
  const response = await surface.fetch(request("/api/share/map", { headers: { cookie } }), {});
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { data: { points: [] } });
  assert.deepEqual(Object.keys(input).sort(), ["env", "sessionDigest"]);
  assert.equal(input.sessionDigest, await sha256Digest(cookie.slice("__Host-tour_share_session=".length)));
});

test("exchange passes only SHA-256 digests and sets a host-only session cookie", async () => {
  const rawToken = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ";
  let exchangeInput;
  const now = 1_800_000_000_000;
  const response = await handler({ now: () => now, exchangeShareTokenFn: async input => { exchangeInput = input; return { ok: true }; } })
    .fetch(request("/api/share/exchange", { method: "POST", headers: sameOriginJson, body: JSON.stringify({ token: rawToken }) }), {});
  assert.equal(response.status, 200);
  assert.deepEqual(Object.keys(exchangeInput).sort(), ["auditDigest", "env", "sessionDigest", "sessionExpiresAt", "tokenDigest"]);
  assert.equal(exchangeInput.tokenDigest, await sha256Digest(rawToken));
  const cookie = cookies(response).find(value => value.startsWith("__Host-tour_share_session="));
  assert.match(cookie, /^__Host-tour_share_session=[A-Za-z0-9_-]{43}; Path=\/; Secure; HttpOnly; SameSite=Lax$/);
  const session = cookie.slice("__Host-tour_share_session=".length).split(";", 1)[0];
  assert.equal(exchangeInput.sessionDigest, await sha256Digest(session));
  assert.equal(cookie.includes(rawToken), false);
});

test("authenticated report read forwards only the opaque session digest", async () => {
  let input;
  const surface = handler({ readShareFn: async value => { input = value; return { ok: true, data: { title: "Client tour", items: [] } }; } });
  const cookie = "__Host-tour_share_session=session_abcdefghijklmnopqrstuvwxyz";
  assert.equal((await surface.fetch(request("/api/share/report"), {})).status, 401);
  const response = await surface.fetch(request("/api/share/report", { headers: { cookie } }), {});
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { data: { title: "Client tour", items: [] } });
  assert.deepEqual(Object.keys(input).sort(), ["env", "sessionDigest"]);
  assert.equal(input.sessionDigest, await sha256Digest(cookie.slice("__Host-tour_share_session=".length)));
  assert.equal(input.session, undefined);
  assert.equal(input.request, undefined);
});
