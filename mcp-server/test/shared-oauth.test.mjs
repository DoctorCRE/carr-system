import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { createServer } from "node:http";
import { launchChrome } from "../../dealroom/test/chrome-launch.mjs";
import { handleAuthorize, s256 } from "../src/google-oidc.js";
import * as policy from "../src/oauth-policy.js";
import { OAuthConsentState } from "../src/oauth-consent-state.js";

// Substitute only the deployment identity allow-list with synthetic accounts.
// Signature verification, callback and approval code execute unchanged.
const oidcSource = await readFile(new URL("../src/google-oidc.js", import.meta.url), "utf8");
const syntheticOidc = await import(`data:text/javascript;base64,${Buffer.from(oidcSource
  .replace(/import \{ slugForEmail[^\n]+/, `const slugForEmail = email => email === "partner@example.invalid" ? "synthetic-partner" : null;
const propsForSlug = (slug, extra) => ({ slug, ...extra });
const agentSlugForClient = () => null;
const verifiedAgentSlugForClient = () => null;`)
  .replace('"./oauth-policy.js"', JSON.stringify(new URL("../src/oauth-policy.js", import.meta.url).href))
  .replace('"./oauth-consent-state.js"', JSON.stringify(new URL("../src/oauth-consent-state.js", import.meta.url).href))
).toString("base64")}`);

// Only the Workers host class is stubbed. The pinned provider's parsing,
// encryption, KV storage, audience checks and exchanges execute unchanged.
const providerSource = await readFile(new URL("../node_modules/@cloudflare/workers-oauth-provider/dist/oauth-provider.js", import.meta.url), "utf8");
assert.ok(providerSource.startsWith('import { WorkerEntrypoint } from "cloudflare:workers";'));
const { OAuthProvider, getOAuthApi } = await import(`data:text/javascript;base64,${Buffer.from(
  providerSource.replace('import { WorkerEntrypoint } from "cloudflare:workers";', "class WorkerEntrypoint {}")
).toString("base64")}`);

const ORIGIN = "https://oauth.example";
const REDIRECT = "https://client.example/callback";
const VERIFIER = "a".repeat(43);
const ctx = () => ({ waitUntil() {} });
class MemoryKV {
  values = new Map();
  async put(key, value, options = {}) {
    this.values.set(key, { value, expires: options.expiration ??
      (options.expirationTtl ? Date.now() / 1000 + options.expirationTtl : Infinity) });
  }
  async get(key, options) {
    const item = this.values.get(key);
    if (!item || item.expires <= Date.now() / 1000) return null;
    return (options === "json" || options?.type === "json") ? JSON.parse(item.value) : item.value;
  }
  async delete(key) { this.values.delete(key); }
  async list({ prefix = "" } = {}) {
    return { keys: [...this.values.keys()].filter(k => k.startsWith(prefix)).map(name => ({ name })), list_complete: true };
  }
}
async function fixture(extraOptions = {}) {
  const objects = new Map();
  const env = { OAUTH_KV: new MemoryKV(), GOOGLE_CLIENT_ID: "synthetic-config", GOOGLE_CLIENT_SECRET: "synthetic-config",
    OAUTH_CONSENT_STATE: { idFromName: id => id, get: id => {
      if (!objects.has(id)) {
        const values = new Map();
        let queue = Promise.resolve();
        const storage = { values, get: async k => structuredClone(values.get(k)), put: async (k, v) => values.set(k, structuredClone(v)),
          delete: async k => values.delete(k), deleteAll: async () => values.clear(), setAlarm: async () => {},
          transaction: fn => { const result = queue.then(() => fn(storage)); queue = result.catch(() => {}); return result; } };
        objects.set(id, new OAuthConsentState({ storage }));
      }
      return objects.get(id);
    } } };
  const options = { apiRoute: ["/mcp", "/doc/mcp", "/pipeline/changes"],
    apiHandler: { fetch: async () => new Response("accepted") },
    defaultHandler: { fetch: handleAuthorize }, authorizeEndpoint: "/authorize",
    tokenEndpoint: "/token", clientRegistrationEndpoint: "/register", allowPlainPKCE: false,
    accessTokenTTL: 3600, refreshTokenTTL: 7776000, clientIdMetadataDocumentEnabled: false, ...extraOptions };
  env.OAUTH_PROVIDER = getOAuthApi(options, env);
  const client = await env.OAUTH_PROVIDER.createClient({ clientName: "Synthetic Client", redirectUris: [REDIRECT], tokenEndpointAuthMethod: "none" });
  const rawProvider = new OAuthProvider(options);
  const provider = { fetch: (request, env, ctx) => policy.sharedOAuthFetch
    ? policy.sharedOAuthFetch(rawProvider, request, env, ctx) : rawProvider.fetch(request, env, ctx) };
  return { env, client, provider, rawProvider, objects };
}
async function authorization(client, changes = {}) {
  const params = new URLSearchParams({ response_type: "code", client_id: client.clientId,
    state: "synthetic-client-state",
    redirect_uri: REDIRECT, code_challenge_method: "S256", code_challenge: await s256(VERIFIER),
    resource: `${ORIGIN}/mcp`, ...changes });
  for (const [k, v] of Object.entries(changes)) if (v === null) params.delete(k);
  return new Request(`${ORIGIN}/authorize?${params}`);
}

test("authorization refuses missing S256 challenge before starting Google sign-in", async () => {
  const { env, client, provider } = await fixture();
  const response = await provider.fetch(await authorization(client, { code_challenge: null }), env, ctx());
  assert.equal(response.status, 400);
  assert.equal(response.headers.has("location"), false);
});

async function signedIdentity(email = "partner@example.invalid") {
  const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const encode = data => Buffer.from(JSON.stringify(data)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const message = `${encode({ alg: "RS256", kid: "synthetic" })}.${encode({ iss: "https://accounts.google.com",
    aud: "synthetic-config", exp: now + 3600, iat: now, email, email_verified: true, sub: "synthetic-subject" })}`;
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, new TextEncoder().encode(message));
  return { id_token: `${message}.${Buffer.from(signature).toString("base64url")}`,
    jwks: { keys: [{ ...await crypto.subtle.exportKey("jwk", pair.publicKey), kid: "synthetic" }] } };
}
async function consentFixture(email, clientName = "Synthetic Client", redirectUri = REDIRECT) {
  const f = await fixture();
  await f.env.OAUTH_PROVIDER.updateClient(f.client.clientId, { clientName, redirectUris: [redirectUri] });
  const start = await f.provider.fetch(await authorization(f.client, { redirect_uri: redirectUri }), f.env, ctx());
  const state = new URL(start.headers.get("location")).searchParams.get("state");
  const cookie = start.headers.get("set-cookie")?.split(";")[0] || "";
  const identity = await signedIdentity(email);
  await f.env.OAUTH_KV.put("google_jwks_cache", JSON.stringify(identity.jwks));
  const original = globalThis.fetch;
  globalThis.fetch = async url => {
    assert.equal(url, "https://oauth2.googleapis.com/token");
    return Response.json({ id_token: identity.id_token });
  };
  let response;
  try { response = await syntheticOidc.handleCallback(new Request(`${ORIGIN}/callback?state=${state}&code=synthetic-code`, { headers: { cookie } }), f.env); }
  finally { globalThis.fetch = original; }
  const html = await response.text();
  const form = new URLSearchParams();
  for (const match of html.matchAll(/name="([^"]+)" value="([^"]*)"/g)) form.set(match[1], match[2]);
  return { ...f, response, html, form, cookie };
}
async function approve(f, { cookie = f.cookie, origin = ORIGIN, form = f.form, method = "POST", decision = "approve" } = {}) {
  return syntheticOidc.handleConsent(new Request(`${ORIGIN}/consent`, { method,
    headers: { cookie, origin, "content-type": "application/x-www-form-urlencoded" },
    ...(method === "POST" ? { body: new URLSearchParams({ ...Object.fromEntries(form), decision }) } : {}) }), f.env);
}
async function handoffUrl(response) {
  return new URL((await response.text()).match(/href="([^"]+)"/)[1].replaceAll("&amp;", "&"));
}
test("verified identity reaches client-specific consent before any grant and approval issues a bound code", async () => {
  const f = await consentFixture();
  assert.equal(f.response.status, 200);
  for (const value of ["Synthetic Client", REDIRECT, `${ORIGIN}/mcp`, "Read and write"]) assert.ok(f.html.includes(value));
  assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
  const accepted = await approve(f);
  assert.equal(accepted.status, 200);
  const code = (await handoffUrl(accepted)).searchParams.get("code");
  const tokens = await (await exchange(f, { grant_type: "authorization_code", code, code_verifier: VERIFIER })).json();
  assert.equal(tokens.resource, `${ORIGIN}/mcp`);
  assert.equal((await access(f, tokens.access_token, "/mcp")).status, 200);
  assert.equal((await approve(f)).status, 400);
});
test("consent page lets the browser send its Origin on the approval POST", async () => {
  // Fetch standard: a form POST from a no-referrer page carries `Origin: null`,
  // which the Origin check refuses. Chrome confirmed 2026-10-06 (Dell's reconnect).
  const f = await consentFixture();
  assert.equal(f.response.headers.get("referrer-policy"), "same-origin");
});
test("consent rejects absent/wrong browser cookie, cross-site POST, tampered nonce, expiry and GET", async () => {
  const f = await consentFixture();
  assert.equal(f.response.status, 200);
  for (const options of [{ cookie: "" }, { cookie: "wrong=browser" }, { origin: "https://hostile.example" },
    { form: new URLSearchParams({ ...Object.fromEntries(f.form), csrf: "tampered" }) }, { method: "GET" }]) {
    assert.ok([400, 403, 405].includes((await approve(f, options)).status));
    assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
  }
  for (const object of f.objects.values()) { const pending = object.storage.values.get("pending"); pending.expiresAt = 1; }
  assert.equal((await approve(f)).status, 400);
});
test("wrong Google identity is denied and configured approved-client IDs restrict authorizations", async () => {
  const denied = await consentFixture("outsider@example.invalid");
  assert.equal(denied.response.status, 403);
  assert.equal((await denied.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
  const f = await fixture();
  for (const config of ["[]", "invalid-json", '["other-client"]']) {
    f.env.CARR_OAUTH_APPROVED_CLIENT_IDS = config;
    assert.equal((await f.provider.fetch(await authorization(f.client), f.env, ctx())).status, 400);
  }
  f.env.CARR_OAUTH_APPROVED_CLIENT_IDS = JSON.stringify([f.client.clientId]);
  assert.equal((await f.provider.fetch(await authorization(f.client), f.env, ctx())).status, 302);
});

test("denying consent never issues a grant and untrusted client labels are escaped", async () => {
  const f = await consentFixture(undefined, '<script>synthetic()</script>');
  assert.ok(f.html.includes("&lt;script&gt;synthetic()&lt;/script&gt;"));
  assert.equal(f.html.includes("<script>"), false);
  assert.equal(f.response.headers.get("x-frame-options"), "DENY");
  assert.match(f.response.headers.get("content-security-policy"), /frame-ancestors 'none'/);
  assert.equal((await approve(f, { decision: "deny" })).status, 200);
  assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
  assert.equal((await approve(f)).status, 400);
});

test("consent cannot replace the approved redirect or bypass a changed server client policy", async () => {
  const f = await consentFixture();
  const form = new URLSearchParams({ ...Object.fromEntries(f.form), redirect_uri: "https://hostile.example/callback", resource: `${ORIGIN}/doc/mcp` });
  const accepted = await approve(f, { form });
  const target = await handoffUrl(accepted);
  assert.equal(target.origin, "https://client.example");
  const code = target.searchParams.get("code");
  assert.equal((await (await exchange(f, { grant_type: "authorization_code", code, code_verifier: VERIFIER })).json()).resource, `${ORIGIN}/mcp`);
  const changed = await consentFixture();
  changed.env.CARR_OAUTH_APPROVED_CLIENT_IDS = "[]";
  assert.equal((await approve(changed)).status, 403);
  assert.equal((await changed.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
});

test("approval revalidates a client's registration and fails closed if its redirect changed", async () => {
  const f = await consentFixture();
  await f.env.OAUTH_PROVIDER.updateClient(f.client.clientId, { redirectUris: ["https://replacement.example/callback"] });
  const refused = await approve(f);
  assert.equal(refused.status, 400);
  assert.equal(refused.headers.has("location"), false);
  assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
});

test("Google callback refuses a stolen state from another browser before exchanging anything", async () => {
  const f = await fixture();
  const start = await f.provider.fetch(await authorization(f.client), f.env, ctx());
  const state = new URL(start.headers.get("location")).searchParams.get("state");
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("No network may be called"); };
  try {
    assert.equal((await syntheticOidc.handleCallback(new Request(`${ORIGIN}/callback?state=${state}&code=synthetic-code`), f.env)).status, 403);
  } finally { globalThis.fetch = original; }
});

test("pinned baseline accepts missing challenge, broad/no audience and reflects hostile Origin", async () => {
  const f = await fixture();
  const parsed = await f.env.OAUTH_PROVIDER.parseAuthRequest(await authorization(f.client, { code_challenge: null }));
  assert.equal(parsed.codeChallenge, undefined);
  for (const resource of [null, ORIGIN]) {
    const code = await codeGrant(f, resource);
    const raw = await f.rawProvider.fetch(new Request(`${ORIGIN}/token`, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "authorization_code", client_id: f.client.clientId, code, code_verifier: VERIFIER }) }), f.env, ctx());
    const tokens = await raw.json();
    for (const path of ["/mcp", "/doc/mcp", "/pipeline/changes"]) {
      const response = await f.rawProvider.fetch(new Request(ORIGIN + path, { headers: { authorization: `Bearer ${tokens.access_token}`, origin: "https://hostile.example" } }), f.env, ctx());
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("access-control-allow-origin"), "https://hostile.example");
    }
  }
});

test("authorization accepts canonical S256 and refuses malformed challenges and methods", async () => {
  const { env, client, provider } = await fixture();
  assert.equal((await provider.fetch(await authorization(client), env, ctx())).status, 302);
  for (const change of [ { code_challenge: "x" }, { code_challenge: "!".repeat(43) },
    { code_challenge: "a".repeat(43) }, { code_challenge_method: "plain" },
    { code_challenge_method: "bogus" }, { response_type: "token" } ]) {
    assert.equal((await provider.fetch(await authorization(client, change), env, ctx())).status, 400);
  }
});

async function codeGrant(f, resource = `${ORIGIN}/mcp`, props = {}) {
  const request = await f.env.OAUTH_PROVIDER.parseAuthRequest(await authorization(f.client, { resource }));
  const { redirectTo } = await f.env.OAUTH_PROVIDER.completeAuthorization({ request,
    userId: "synthetic-partner", scope: [], props, revokeExistingGrants: false });
  return new URL(redirectTo).searchParams.get("code");
}
async function exchange(f, params) {
  return f.provider.fetch(new Request(`${ORIGIN}/token`, { method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: f.client.clientId, ...params }) }), f.env, ctx());
}
test("pinned provider requires the matching verifier for a stored S256 challenge", async () => {
  const f = await fixture();
  const code = await codeGrant(f);
  for (const change of [{}, { code_verifier: "b".repeat(43) }]) {
    const response = await exchange(f, { grant_type: "authorization_code", code, ...change });
    assert.equal(response.status, 400);
  }
  assert.equal((await exchange(f, { grant_type: "authorization_code", code, code_verifier: VERIFIER })).status, 200);
});

async function access(f, token, path) {
  return f.provider.fetch(new Request(`${ORIGIN}${path}`, { headers: { authorization: `Bearer ${token}` } }), f.env, ctx());
}
test("legacy missing or origin-wide audiences work only on mcp and migrate silently at refresh", async () => {
  for (const legacyResource of [null, ORIGIN, `${ORIGIN}/`]) {
    const f = await fixture();
    const code = await codeGrant(f, legacyResource);
    // Mint a pre-policy token with the real provider, then cross the new seam.
    const legacyResponse = await f.rawProvider.fetch(new Request(`${ORIGIN}/token`, { method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ client_id: f.client.clientId, grant_type: "authorization_code", code, code_verifier: VERIFIER }) }), f.env, ctx());
    const legacy = await legacyResponse.json();
    assert.equal((await access(f, legacy.access_token, "/mcp")).status, 200);
    assert.equal((await access(f, legacy.access_token, "/doc/mcp")).status, 401);
    assert.equal((await access(f, legacy.access_token, "/pipeline/changes")).status, 401);
    const before = [...f.env.OAUTH_KV.values].map(([k, v]) => [k, v.value]);
    const refused = await exchange(f, { grant_type: "refresh_token", refresh_token: legacy.refresh_token, resource: `${ORIGIN}/doc/mcp` });
    assert.equal(refused.status, 400);
    assert.deepEqual([...f.env.OAUTH_KV.values].map(([k, v]) => [k, v.value]), before);
    const refreshed = await (await exchange(f, { grant_type: "refresh_token", refresh_token: legacy.refresh_token,
      ...(legacyResource ? { resource: legacyResource } : {}) })).json();
    assert.equal(refreshed.resource, `${ORIGIN}/mcp`);
    assert.equal((await access(f, refreshed.access_token, "/mcp")).status, 200);
    for (const path of ["/doc/mcp", "/pipeline/changes"]) assert.equal((await access(f, refreshed.access_token, path)).status, 401);
    const again = await (await exchange(f, { grant_type: "refresh_token", refresh_token: refreshed.refresh_token })).json();
    assert.equal(again.resource, `${ORIGIN}/mcp`);
  }
});

test("each exact resource token is refused on both other routes and cannot expand at refresh", async () => {
  const f = await fixture();
  const paths = ["/mcp", "/doc/mcp", "/pipeline/changes"];
  for (const path of paths) {
    const tokens = await (await exchange(f, { grant_type: "authorization_code", code: await codeGrant(f, `${ORIGIN}${path}`), code_verifier: VERIFIER })).json();
    for (const target of paths) {
      assert.equal((await access(f, tokens.access_token, target)).status, target === path ? 200 : 401);
      if (target !== path) {
        const before = [...f.env.OAUTH_KV.values].map(([k, v]) => [k, v.value]);
        assert.equal((await exchange(f, { grant_type: "refresh_token", refresh_token: tokens.refresh_token, resource: `${ORIGIN}${target}` })).status, 400);
        assert.deepEqual([...f.env.OAUTH_KV.values].map(([k, v]) => [k, v.value]), before);
      }
    }
    const refreshed = await (await exchange(f, { grant_type: "refresh_token", refresh_token: tokens.refresh_token })).json();
    assert.equal(refreshed.resource, `${ORIGIN}${path}`);
    assert.equal((await access(f, refreshed.access_token, path)).status, 200);
  }
});

test("authorization binds omitted resource to legacy mcp and rejects broad, foreign and multiple resources", async () => {
  const f = await fixture();
  for (const resource of [ORIGIN, `${ORIGIN}/`, `${ORIGIN}/doc`, "https://elsewhere.example/mcp", `${ORIGIN}/mcp#fragment`]) {
    assert.equal((await f.provider.fetch(await authorization(f.client, { resource }), f.env, ctx())).status, 400);
  }
  const missing = await f.provider.fetch(await authorization(f.client, { resource: null }), f.env, ctx());
  assert.equal(missing.status, 302);
  const state = new URL(missing.headers.get("location")).searchParams.get("state");
  assert.equal((await f.env.OAUTH_KV.get(`pending_auth:${state}`, "json")).req.resource, `${ORIGIN}/mcp`);
  const duplicate = new URL((await authorization(f.client)).url);
  duplicate.searchParams.append("resource", `${ORIGIN}/doc/mcp`);
  assert.equal((await f.provider.fetch(new Request(duplicate), f.env, ctx())).status, 400);
});

test("Origin policy refuses hostile, null and malformed origins before every protected-route method", async () => {
  const f = await fixture();
  for (const path of ["/mcp", "/doc/mcp", "/pipeline/changes", "/mcp/nested"]) {
    for (const method of ["GET", "POST", "OPTIONS", "DELETE"]) {
      for (const origin of ["https://hostile.example", "null", "https://chatgpt.com.evil.example", "https://claude.ai/path", "https://user@claude.ai"]) {
        const response = await f.provider.fetch(new Request(ORIGIN + path, { method, headers: { origin } }), f.env, ctx());
        assert.equal(response.status, 403);
        assert.equal(response.headers.get("access-control-allow-origin"), null);
      }
    }
  }
});

test("absent Origin and exact ChatGPT/Claude/configured origins reach OAuth authentication", async () => {
  const f = await fixture();
  f.env.CARR_MCP_ALLOWED_ORIGINS = JSON.stringify(["https://browser.example"]);
  for (const origin of [null, "https://chatgpt.com", "https://chat.openai.com", "https://claude.ai", "https://browser.example"]) {
    const headers = origin ? { origin } : {};
    assert.equal((await f.provider.fetch(new Request(`${ORIGIN}/mcp`, { headers }), f.env, ctx())).status, 401);
  }
  f.env.CARR_MCP_ALLOWED_ORIGINS = '["*"]';
  assert.equal((await f.provider.fetch(new Request(`${ORIGIN}/mcp`, { headers: { origin: "https://chatgpt.com" } }), f.env, ctx())).status, 403);
});

test("authenticated access and refresh revocation retain pinned provider behavior", async () => {
  for (const hint of ["access_token", "refresh_token"]) {
    const f = await fixture();
    const tokens = await (await exchange(f, { grant_type: "authorization_code", code: await codeGrant(f), code_verifier: VERIFIER })).json();
    const revoke = params => exchange(f, { token: tokens[hint], token_type_hint: hint, ...params });
    assert.equal((await revoke({ client_id: "wrong-client" })).status, 401);
    assert.equal((await access(f, tokens.access_token, "/mcp")).status, 200);
    assert.equal((await revoke({})).status, 200);
    assert.equal((await access(f, tokens.access_token, "/mcp")).status, 401);
    if (hint === "refresh_token") assert.equal((await exchange(f, { grant_type: "refresh_token", refresh_token: tokens.refresh_token })).status, 400);
  }
});

test("token media types are case insensitive, parameter aware and exact", async () => {
  for (const type of ["Application/X-Www-Form-Urlencoded", "application/x-www-form-urlencoded; charset=UTF-8", "Application/Json; charset=UTF-8",
    "application/json-evil", "application/x-www-form-urlencoded-evil"]) {
    const f = await fixture();
    const params = { client_id: f.client.clientId, grant_type: "authorization_code", code: await codeGrant(f), code_verifier: VERIFIER };
    const response = await f.provider.fetch(new Request(`${ORIGIN}/token`, { method: "POST", headers: { "content-type": type },
      body: type.toLowerCase().includes("json") ? JSON.stringify(params) : new URLSearchParams(params) }), f.env, ctx());
    assert.equal(response.status, type.includes("-evil") ? 400 : 200, type);
  }
});

test("OAuth storage outages propagate to the server failure boundary instead of invalid credentials", async () => {
  const f = await fixture();
  const unavailable = new Error("synthetic storage unavailable");
  f.env.OAUTH_KV.get = async () => { throw unavailable; };
  await assert.rejects(access(f, "synthetic:grant:token", "/mcp"), e => e === unavailable);
});

test("client lookup outage after signed callback propagates instead of claiming deregistration", async () => {
  const f = await fixture();
  const start = await f.provider.fetch(await authorization(f.client), f.env, ctx());
  const state = new URL(start.headers.get("location")).searchParams.get("state");
  const identity = await signedIdentity();
  await f.env.OAUTH_KV.put("google_jwks_cache", JSON.stringify(identity.jwks));
  const unavailable = new Error("synthetic client lookup unavailable");
  f.env.OAUTH_PROVIDER.lookupClient = async () => { throw unavailable; };
  const original = globalThis.fetch;
  globalThis.fetch = async () => Response.json(identity);
  try {
    await assert.rejects(syntheticOidc.handleCallback(new Request(`${ORIGIN}/callback?state=${state}&code=synthetic-code`,
      { headers: { cookie: start.headers.get("set-cookie").split(";")[0] } }), f.env), e => e === unavailable);
  } finally { globalThis.fetch = original; }
});

test("OAuth errors are readable by exact approved origins only", async () => {
  const f = await fixture();
  f.env.CARR_MCP_ALLOWED_ORIGINS = '["https://browser.example"]';
  const tokens = await (await exchange(f, { grant_type: "authorization_code", code: await codeGrant(f), code_verifier: VERIFIER })).json();
  for (const [key, item] of f.env.OAUTH_KV.values) {
    if (key.startsWith("token:")) { const data = JSON.parse(item.value); data.expiresAt = 1; item.value = JSON.stringify(data); }
  }
  for (const origin of ["https://chatgpt.com", "https://browser.example", "https://hostile.example"]) {
    for (const token of ["invalid", tokens.access_token]) {
      const response = await f.provider.fetch(new Request(`${ORIGIN}/mcp`, { headers: { origin, authorization: `Bearer ${token}` } }), f.env, ctx());
      assert.equal(response.headers.get("access-control-allow-origin"), origin.includes("hostile") ? null : origin);
      if (!origin.includes("hostile")) assert.match(response.headers.get("vary"), /Origin/i);
    }
    const response = await f.provider.fetch(new Request(`${ORIGIN}/token`, { method: "POST", headers: { origin, "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ client_id: f.client.clientId, grant_type: "refresh_token", refresh_token: tokens.refresh_token, resource: `${ORIGIN}/doc/mcp` }) }), f.env, ctx());
    assert.equal(response.headers.get("access-control-allow-origin"), origin.includes("hostile") ? null : origin);
    assert.equal(response.status, origin.includes("hostile") ? 403 : 400);
  }
});

test("concurrent consent and stale KV snapshots cannot issue a second grant after approve or deny", async () => {
  const f = await consentFixture();
  const snapshot = new Map(f.env.OAUTH_KV.values);
  const responses = await Promise.all([approve(f), approve(f)]);
  assert.equal(responses.filter(r => r.status < 400).length, 1);
  assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 1);
  const get = f.env.OAUTH_KV.get.bind(f.env.OAUTH_KV);
  f.env.OAUTH_KV.get = async (key, options) => key.startsWith("pending_consent:") && snapshot.has(key)
    ? JSON.parse(snapshot.get(key).value) : get(key, options);
  assert.equal((await approve(f)).status, 400);
  const denied = await consentFixture();
  const stale = new Map(denied.env.OAUTH_KV.values);
  await approve(denied, { decision: "deny" });
  const deniedGet = denied.env.OAUTH_KV.get.bind(denied.env.OAUTH_KV);
  denied.env.OAUTH_KV.get = async (key, options) => key.startsWith("pending_consent:") && stale.has(key)
    ? JSON.parse(stale.get(key).value) : deniedGet(key, options);
  assert.equal((await approve(denied)).status, 400);
  assert.equal((await denied.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
});

test("pending identity is encrypted at rest without storing its browser decryption key", async () => {
  const f = await consentFixture();
  const storage = [...f.env.OAUTH_KV.values.values()].map(item => item.value).join("\n") +
    JSON.stringify([...f.objects.values()].map(object => [...object.storage.values]));
  for (const identity of ["partner@example.invalid", "synthetic-subject", "synthetic-partner", f.cookie.split("=")[1]])
    assert.equal(storage.includes(identity), false, `raw storage exposes ${identity}`);
  assert.equal((await approve(f)).status, 200);
});

test("stored browser authentication digest cannot decrypt pending identity", async () => {
  const f = await consentFixture();
  const pending = f.objects.get(f.form.get("id")).storage.values.get("pending");
  const key = await crypto.subtle.importKey("raw", Buffer.from(pending.browser, "base64url"), "AES-GCM", false, ["decrypt"]);
  await assert.rejects(crypto.subtle.decrypt({ name: "AES-GCM", iv: Buffer.from(pending.encryptedGrant.iv, "base64url"),
    additionalData: new TextEncoder().encode(`carr-oauth-consent-v1:${f.form.get("id")}`) }, key,
  Buffer.from(pending.encryptedGrant.ciphertext, "base64url")));
  assert.equal((await approve(f)).status, 200);
});

test("denial completes the registered client attempt with access_denied and original state", async () => {
  const f = await consentFixture();
  const denied = await approve(f, { decision: "deny" });
  assert.equal(denied.status, 200);
  const html = await denied.text();
  const target = new URL(html.match(/href="([^"]+)"/)[1].replaceAll("&amp;", "&"));
  assert.equal(target.origin + target.pathname, REDIRECT);
  assert.equal(target.searchParams.get("error"), "access_denied");
  assert.equal(target.searchParams.get("state"), "synthetic-client-state");
  assert.equal(target.searchParams.has("code"), false);
  assert.equal(target.searchParams.has("access_token"), false);
  assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
  const changed = await consentFixture();
  await changed.env.OAUTH_PROVIDER.updateClient(changed.client.clientId, { redirectUris: ["https://replacement.example/callback"] });
  const refused = await approve(changed, { decision: "deny" });
  assert.equal(refused.status, 400);
  assert.equal((await refused.text()).includes('href="https://client.example'), false);
});

test("Chrome completes approve and deny at an external client while consent form stays restricted", { timeout: 90000 }, async t => {
  const chrome = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"].filter(Boolean).find(existsSync);
  if (!chrome) { t.skip("Chrome is unavailable"); return; }
  const browser = await launchChrome(chrome);
  const socket = new WebSocket(browser.pageWsUrl);
  const servers = [];
  t.after(async () => {
    socket.close();
    try {
      await Promise.all(servers.map(server => new Promise(resolve => {
        server.closeAllConnections(); server.close(resolve);
      })));
    } finally {
      await browser.close();
    }
  });
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let serial = 0;
  const pending = new Map();
  socket.addEventListener("message", event => {
    const value = JSON.parse(String(event.data));
    if (!pending.has(value.id)) return;
    const { resolve, reject } = pending.get(value.id); pending.delete(value.id);
    if (value.error) reject(new Error(JSON.stringify(value.error))); else resolve(value.result);
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => { const id = ++serial; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
  const evaluate = async expression => {
    const result = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  for (const decision of ["approve", "deny"]) {
    let callback, posts = 0, serverError;
    const clientServer = createServer((req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      if (url.pathname === "/callback") callback = url;
      res.end("Client received OAuth result");
    });
    await new Promise(resolve => clientServer.listen(0, "127.0.0.1", resolve));
    servers.push(clientServer);
    const redirect = `http://127.0.0.1:${clientServer.address().port}/callback`;
    const f = await consentFixture(undefined, "Synthetic Client", redirect);
    const server = createServer(async (req, res) => {
      try {
        let response;
        if (req.method === "POST") {
          posts++;
          const chunks = []; for await (const chunk of req) chunks.push(chunk);
          // Forward the Origin Chrome actually sent, mapped from this test host to ORIGIN.
          // A page policy that makes Chrome send `null` must fail here.
          const sent = req.headers.origin;
          const origin = sent === `http://127.0.0.1:${server.address().port}` ? ORIGIN : String(sent);
          response = await syntheticOidc.handleConsent(new Request(`${ORIGIN}/consent`, { method: "POST",
            headers: { cookie: f.cookie, origin, "content-type": req.headers["content-type"] }, body: Buffer.concat(chunks) }), f.env);
        } else response = new Response(f.html, { headers: f.response.headers });
        res.writeHead(response.status, Object.fromEntries(response.headers)); res.end(await response.text());
      } catch (error) { serverError = error; res.writeHead(500).end(); }
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    servers.push(server);
    await call("Page.navigate", { url: `http://127.0.0.1:${server.address().port}/` });
    for (let i = 0; i < 300; i++) { if (await evaluate('!!document.querySelector("form")')) break; await new Promise(resolve => setTimeout(resolve, 10)); }
    assert.match(f.response.headers.get("content-security-policy"), /form-action 'self';/);
    await evaluate(`document.querySelector('button[value="${decision}"]').click()`);
    for (let i = 0; i < 300 && !callback; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.ifError(serverError);
    assert.equal(posts, 1);
    assert.ok(callback, `Chrome must reach external callback after ${decision}`);
    assert.equal(callback.searchParams.get("state"), "synthetic-client-state");
    assert.equal(callback.searchParams.has("code"), decision === "approve");
    assert.equal(callback.searchParams.get("error"), decision === "deny" ? "access_denied" : null);
  }
});
