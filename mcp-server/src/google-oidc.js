// CARR MCP server — the upstream identity leg: Google sign-in.
//
// The OAuthProvider library is the OAuth SERVER to the Claude client. This file
// is the OAuth CLIENT to Google. Two endpoints, both served by the Worker's
// default handler (they are not API routes, so no access token is involved):
//
//   GET /authorize  — the Claude client lands here; we park the request and
//                     bounce the human to Google.
//   GET /callback   — Google returns; we verify the identity token, apply the
//                     allow-list, and show client-specific approval.
//   POST /consent — browser-bound approval, then ask the library to issue.
//
// Google identity and downstream-client consent are separate gates.

import { slugForEmail, propsForSlug, agentSlugForClient, verifiedAgentSlugForClient } from "./identity.js";
import { validateAuthorizationRequest, requireApprovedClient } from "./oauth-policy.js";
import { consentState } from "./oauth-consent-state.js";

const GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";
const GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs";
const GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"];

const PENDING_PREFIX = "pending_auth:"; // our keys; library owns client:/grant:/token:
const PENDING_TTL = 600; // 10 minutes to finish a sign-in
const cookieName = state => `__Host-carr-oauth-${state}`;
const cookieValue = (request, name) => (request.headers.get("cookie") || "").split(";")
  .map(part => part.trim()).find(part => part.startsWith(`${name}=`))?.slice(name.length + 1);
const browserCookie = (state, value, maxAge = PENDING_TTL) =>
  `${cookieName(state)}=${value}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=${maxAge}`;
const JWKS_CACHE_KEY = "google_jwks_cache";
const JWKS_CACHE_TTL = 3600;

const CLOCK_SKEW = 60; // seconds tolerated on exp
const IAT_SKEW = 300; // seconds tolerated on a future iat

// ---------- small helpers ----------

const enc = new TextEncoder();
const dec = new TextDecoder();

export function randomString(bytes) {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return [...buf].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function b64urlToBytes(s) {
  const pad = s.length % 4 === 0 ? "" : "=".repeat(4 - (s.length % 4));
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytesToB64url(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function s256(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", enc.encode(verifier));
  return bytesToB64url(new Uint8Array(digest));
}

// The random HttpOnly browser cookie is the key material; stores retain only
// its hash. AES-GCM binds the ciphertext to this unique consent id. No new
// standing secret or plaintext identity is required for a pending connection.
async function consentCipher(browser, id, value, decrypt = false) {
  const key = await crypto.subtle.importKey("raw", await crypto.subtle.digest("SHA-256", enc.encode(`carr-oauth-consent-encryption-v1:${browser}`)),
    "AES-GCM", false, [decrypt ? "decrypt" : "encrypt"]);
  const iv = decrypt ? b64urlToBytes(value.iv) : crypto.getRandomValues(new Uint8Array(12));
  const algorithm = { name: "AES-GCM", iv, additionalData: enc.encode(`carr-oauth-consent-v1:${id}`) };
  if (decrypt) return JSON.parse(dec.decode(await crypto.subtle.decrypt(algorithm, key, b64urlToBytes(value.ciphertext))));
  const ciphertext = await crypto.subtle.encrypt(algorithm, key, enc.encode(JSON.stringify(value)));
  return { iv: bytesToB64url(iv), ciphertext: bytesToB64url(new Uint8Array(ciphertext)) };
}

// Must match the redirect URI registered in the Google Cloud console EXACTLY,
// or Google refuses with redirect_uri_mismatch. Production is HTTPS-only, so
// the scheme is pinned rather than inherited from the request — a single
// plain-HTTP request must not be able to produce a URI Google will not accept.
function callbackUri(request) {
  const u = new URL("/callback", request.url);
  if (u.hostname !== "localhost" && u.hostname !== "127.0.0.1") u.protocol = "https:";
  return u.toString();
}

/** Build the shared Google OIDC authorization request used by both surfaces. */
export async function googleAuthorizationUrl({ clientId, redirectUri, state, verifier }) {
  const u = new URL(GOOGLE_AUTH_URL);
  u.searchParams.set("client_id", clientId);
  u.searchParams.set("redirect_uri", redirectUri);
  u.searchParams.set("response_type", "code");
  u.searchParams.set("scope", "openid email");
  u.searchParams.set("state", state);
  u.searchParams.set("code_challenge", await s256(verifier));
  u.searchParams.set("code_challenge_method", "S256");
  u.searchParams.set("access_type", "online");
  u.searchParams.set("prompt", "select_account");
  return u;
}

/** Exchange one Google authorization code using the same PKCE contract. */
export async function exchangeGoogleCode({ code, clientId, clientSecret, redirectUri, verifier }, fetchImpl = fetch) {
  const res = await fetchImpl(GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: redirectUri,
      grant_type: "authorization_code",
      code_verifier: verifier,
    }),
  });
  if (!res.ok) throw new Error(`google token endpoint returned ${res.status}`);
  return res.json();
}

// ---------- pages (plain, no theater) ----------

function page(title, lines, status, refreshUrl) {
  const body = lines.map((l) => `    <p>${l}</p>`).join("\n");
  const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    ${refreshUrl ? `<meta http-equiv="refresh" content="0;url=${escapeHtml(refreshUrl)}" />` : ""}
    <title>${title}</title>
    <style>
      body { font: 16px/1.5 -apple-system, system-ui, sans-serif; margin: 0; padding: 3rem 1.25rem;
             color: #e7edf4; background: #101722; }
      main { max-width: 32rem; margin: 0 auto; }
      h1 { font-size: 1.25rem; margin: 0 0 1rem; }
      p { margin: 0 0 0.85rem; }
      code { background: #202b3a; padding: 0.1em 0.35em; border-radius: 3px; overflow-wrap: anywhere; }
      button { font: inherit; padding: .55rem 1rem; margin-right: .6rem; cursor: pointer; }
    </style>
  </head>
  <body>
    <main>
    <h1>${title}</h1>
${body}
    </main>
  </body>
</html>`;
  return new Response(html, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
      // same-origin, not no-referrer: a form POST from a no-referrer page sends
      // `Origin: null`, which handleConsent's Origin check refuses. Cross-origin
      // navigations (Google, the client handoff) still carry no Referer.
      "referrer-policy": "same-origin", "x-frame-options": "DENY",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" },
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function clientHandoff(redirectTo, state, title) {
  // End the form submission with a document. A document navigation (and the
  // accessible fallback link) is not a form redirect chain, so form-action
  // stays restricted to this origin without blocking the verified client.
  const response = page(title, [`<a href="${escapeHtml(redirectTo)}">Continue to the requesting app</a>`], 200, redirectTo);
  response.headers.set("set-cookie", browserCookie(state, "", 0));
  return response;
}

function refusalPage(email, why) {
  return page(
    "Not authorized",
    [
      "This connector is limited to CARR's two partner accounts.",
      why
        ? escapeHtml(why)
        : `The Google account you signed in with${email ? ` (<code>${escapeHtml(email)}</code>)` : ""} is not one of them.`,
      "Nothing was issued and no access was granted.",
      "If you meant to use a different Google account, sign out of Google and start the connection again.",
    ],
    403,
  );
}

// ---------- Google identity token verification ----------

function pickKey(jwks, kid) {
  if (!jwks || !Array.isArray(jwks.keys)) return null;
  if (kid) return jwks.keys.find((k) => k.kid === kid) || null;
  return jwks.keys.length === 1 ? jwks.keys[0] : null;
}

async function googleJwks(env, force) {
  if (!force) {
    const cached = await env.OAUTH_KV.get(JWKS_CACHE_KEY, { type: "json" });
    if (cached && Array.isArray(cached.keys) && cached.keys.length) return cached;
  }
  const res = await fetch(GOOGLE_JWKS_URL);
  if (!res.ok) throw new Error(`google jwks fetch failed: ${res.status}`);
  const jwks = await res.json();
  if (!jwks || !Array.isArray(jwks.keys) || !jwks.keys.length) throw new Error("google jwks empty");
  await env.OAUTH_KV.put(JWKS_CACHE_KEY, JSON.stringify(jwks), { expirationTtl: JWKS_CACHE_TTL });
  return jwks;
}

/**
 * Verifies a Google-issued OIDC id_token: RS256 signature against Google's
 * published JWKS, then issuer, audience, expiry and email presence.
 * Throws on any failure. Returns the claims on success.
 *
 * `fetchJwks` is injectable so the verification path can be exercised in a test
 * harness against a synthetic key set; production always passes the KV-cached
 * Google set.
 */
export async function verifyGoogleIdToken(idToken, clientId, env, opts = {}) {
  const nowSec = Math.floor((opts.now ?? Date.now()) / 1000);
  const getJwks = opts.fetchJwks || ((force) => googleJwks(env, force));

  if (typeof idToken !== "string") throw new Error("id_token: missing");
  const parts = idToken.split(".");
  if (parts.length !== 3) throw new Error("id_token: not a compact JWS");

  let header, claims;
  try {
    header = JSON.parse(dec.decode(b64urlToBytes(parts[0])));
    claims = JSON.parse(dec.decode(b64urlToBytes(parts[1])));
  } catch {
    throw new Error("id_token: undecodable");
  }
  if (header.alg !== "RS256") throw new Error(`id_token: unexpected alg ${header.alg}`);

  // Google rotates keys; a miss on the cached set is a reason to refetch once.
  let jwk = pickKey(await getJwks(false), header.kid);
  if (!jwk) jwk = pickKey(await getJwks(true), header.kid);
  if (!jwk) throw new Error("id_token: no matching Google signing key");

  const key = await crypto.subtle.importKey(
    "jwk",
    { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const ok = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    b64urlToBytes(parts[2]),
    enc.encode(`${parts[0]}.${parts[1]}`),
  );
  if (!ok) throw new Error("id_token: signature verification failed");

  if (!GOOGLE_ISSUERS.includes(claims.iss)) throw new Error(`id_token: untrusted issuer ${claims.iss}`);
  const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!clientId || !aud.includes(clientId)) throw new Error("id_token: audience is not this client");
  if (typeof claims.exp !== "number" || claims.exp + CLOCK_SKEW < nowSec) throw new Error("id_token: expired");
  if (typeof claims.iat === "number" && claims.iat - IAT_SKEW > nowSec) throw new Error("id_token: issued in the future");
  if (typeof claims.email !== "string" || !claims.email) throw new Error("id_token: no email claim");

  return claims;
}

// ---------- /authorize ----------

export async function handleAuthorize(request, env) {
  if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) {
    return page(
      "Sign-in is not configured",
      [
        "This server has no Google OAuth client configured yet, so it cannot start a sign-in.",
        "Nothing was issued.",
      ],
      503,
    );
  }

  let oauthReq;
  try {
    // Also validates the client and its registered redirect_uri, and throws if either is wrong.
    oauthReq = await env.OAUTH_PROVIDER.parseAuthRequest(request);
    validateAuthorizationRequest(request, oauthReq);
    requireApprovedClient(oauthReq.clientId, env);
  } catch (e) {
    return page("Bad authorization request", [escapeHtml(String(e.message || e)), "Nothing was issued."], 400);
  }

  const state = randomString(24);
  const verifier = randomString(48);
  const browser = randomString(32);
  await env.OAUTH_KV.put(
    PENDING_PREFIX + state,
    JSON.stringify({ req: oauthReq, verifier, browser: await s256(browser), expiresAt: Date.now() + PENDING_TTL * 1000 }),
    { expirationTtl: PENDING_TTL },
  );

  // Always offer the account chooser: the allow-list is per-identity, so the
  // human has to be able to see and pick which identity they are using.
  const u = await googleAuthorizationUrl({ clientId: env.GOOGLE_CLIENT_ID,
    redirectUri: callbackUri(request), state, verifier });

  return new Response(null, { status: 302, headers: {
    location: u.toString(), "set-cookie": browserCookie(state, browser), "cache-control": "no-store" } });
}

// ---------- /callback ----------

export async function handleCallback(request, env) {
  const url = new URL(request.url);

  const googleError = url.searchParams.get("error");
  if (googleError) {
    return page(
      "Sign-in did not complete",
      [`Google returned <code>${escapeHtml(googleError)}</code>.`, "Nothing was issued."],
      400,
    );
  }

  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  if (!state || !code) {
    return page("Sign-in did not complete", ["The response from Google was missing its code or state.", "Nothing was issued."], 400);
  }

  const key = PENDING_PREFIX + state;
  const pending = await env.OAUTH_KV.get(key, { type: "json" });
  if (!pending || !pending.req || pending.expiresAt < Date.now()) {
    return page(
      "That sign-in link has expired",
      ["Sign-in links are good for ten minutes and can only be used once.", "Start the connection again from the requesting app."],
      400,
    );
  }
  const browser = cookieValue(request, cookieName(state)) || "";
  if (!pending.browser || await s256(browser) !== pending.browser)
    return page("Sign-in browser did not match", ["Start the connection again in this browser. Nothing was issued."], 403);
  await env.OAUTH_KV.delete(key);

  let tok;
  try {
    tok = await exchangeGoogleCode({ code, clientId: env.GOOGLE_CLIENT_ID,
      clientSecret: env.GOOGLE_CLIENT_SECRET, redirectUri: callbackUri(request),
      verifier: pending.verifier });
  } catch (e) {
    return page("Could not complete Google sign-in", [escapeHtml(String(e.message || e)), "Nothing was issued."], 502);
  }

  let claims;
  try {
    claims = await verifyGoogleIdToken(tok.id_token, env.GOOGLE_CLIENT_ID, env);
  } catch (e) {
    console.warn(`google identity rejected: ${String(e.message || e)}`);
    return page("Identity could not be verified", ["Google's response did not verify.", "Nothing was issued."], 401);
  }

  if (claims.email_verified !== true && claims.email_verified !== "true") {
    return refusalPage(claims.email, "That Google account's email address is not verified by Google.");
  }

  const humanSlug = slugForEmail(claims.email);
  if (!humanSlug) return refusalPage(claims.email);

  // Outside-model attribution override (loop #227). The identity check above
  // is complete and unweakened by any of this: humanSlug is already the
  // verified Google identity, checked against ALLOW_LIST exactly as before.
  // What follows only decides which actor slug a WRITE lands under —
  // ordinarily the human's own, but when the OAuth client holding this grant
  // is a recognized outside-model CLI (Codex, Grok — identity.js's
  // AGENT_CLIENT_NAMES), writes attribute to that tool's own actor row
  // instead, so "who actually wrote this" survives even when Joe or Dell is
  // the one driving the CLI. Consent requires client lookup: null means
  // deregistered, while operational failures propagate
  // to the server-failure boundary rather than claiming deregistration.
  const clientInfo = pending.req?.clientId
    ? await env.OAUTH_PROVIDER.lookupClient(pending.req.clientId)
    : null;
  const agentSlug = agentSlugForClient(clientInfo?.clientName);
  const verifiedAgentSlug = verifiedAgentSlugForClient(
    pending.req?.clientId,
    agentSlug,
    env.CARR_NATIVE_AGENT_OAUTH_CLIENTS,
  );
  const slug = agentSlug || humanSlug;

  const props = {
    email: claims.email, sub: claims.sub, via: "oauth-google",
    // client_id names the SURFACE holding this grant (Claude Code, a phone connector,
    // a script). Taken from the auth request, never from anything the caller sends.
    client_id: pending.req?.clientId || null,
  };
  if (agentSlug) {
    // Outside-model surfaces write as automation, never as a human actor —
    // teach/retire-rule/confirm-merge/reassign-deal (mcp.js's humanOnly gate)
    // stay Joe/Dell-only even on a session where Joe is the one driving the
    // CLI. human_slug keeps the verified human on record (identity.js's
    // actorFromProps carries it through) even though the write attributes
    // to codex/grok.
    props.human = false;
    // This is the only OAuth path that creates a personal-brain sponsor for a
    // runtime agent. humanSlug came from Google's verified allow-listed email;
    // neither the OAuth client, model, nor an MCP tool argument can choose it.
    props.sponsoring_human_slug = humanSlug;
    props.sponsor_required = true;
    // client_name remains attribution only. Partner authority is available
    // only when a server-held exact client-id binding corroborates it.
    if (verifiedAgentSlug === agentSlug) props.native_agent_verified = true;
  }

  const grant = {
    request: pending.req,
    // Grant OWNERSHIP always stays with the verified human, regardless of the
    // attribution override above: Joe/Dell list and revoke this grant from
    // their own account exactly as any other connector, and can find it even
    // if the CLI's self-declared client_name ever drifts.
    userId: humanSlug,
    // metadata is stored UNencrypted (it exists so grants can be enumerated and
    // revoked) — keep it to the label. The email rides in props, which is encrypted.
    metadata: { label: agentSlug ? `${humanSlug} via ${agentSlug}` : humanSlug,
                signed_in_at: new Date().toISOString() },
    // Grant exactly what was requested. Never widen on our own judgment.
    scope: Array.isArray(pending.req.scope) ? pending.req.scope : [],
    props: propsForSlug(slug, props),
    // Preserve independently approved connections when a CIMD ID is shared
    // across resources. Existing grants remain individually revocable.
    revokeExistingGrants: false,
  };
  try { requireApprovedClient(pending.req.clientId, env); }
  catch { return refusalPage(null, "This client is not approved by the server."); }
  if (!clientInfo) return refusalPage(null, "The requesting client is no longer registered.");
  const id = randomString(24);
  const csrf = randomString(32);
  const stored = await consentState(env, id, "create", { encryptedGrant: await consentCipher(browser, id, grant), state,
    browser: pending.browser, csrf, expiresAt: Date.now() + PENDING_TTL * 1000 });
  if (!stored.ok) throw new Error("Consent storage failed");
  const scope = pending.req.scope.length ? pending.req.scope.join(" ") : "none requested";
  const permissions = new URL(pending.req.resource).pathname === "/pipeline/changes"
    ? "Read pipeline changes" : "Read and write CARR records";
  const response = page("Approve this connection", [
    `Registered client name (provided by the client): <strong>${escapeHtml(clientInfo.clientName || pending.req.clientId)}</strong>`,
    `Client ID: <code>${escapeHtml(pending.req.clientId)}</code>`,
    `Verified registered redirect URI: <code>${escapeHtml(pending.req.redirectUri)}</code>`,
    `Resource: <code>${escapeHtml(pending.req.resource)}</code>`,
    `Permissions: <strong>${permissions}</strong>. Requested OAuth scopes: <code>${escapeHtml(scope)}</code>.`,
    "OAuth scope labels do not reduce tool authority. The resource's server policy determines available reads and writes.",
    `<form method="post" action="/consent"><input type="hidden" name="id" value="${id}"><input type="hidden" name="state" value="${state}"><input type="hidden" name="csrf" value="${csrf}"><button name="decision" value="approve">Approve connection</button><button name="decision" value="deny">Deny</button></form>`,
  ], 200);
  response.headers.set("set-cookie", browserCookie(state, browser));
  return response;
}

export async function handleConsent(request, env) {
  if (request.method !== "POST") return new Response(null, { status: 405, headers: { allow: "POST" } });
  if (request.headers.get("origin") !== new URL(request.url).origin)
    return page("Approval was refused", ["The approval must come from this server's page. Nothing was issued."], 403);
  let form;
  try { form = await request.formData(); } catch { return page("Invalid approval", ["Nothing was issued."], 400); }
  if (["id", "state", "csrf", "decision"].some(key => form.getAll(key).length !== 1) || !/^[a-f0-9]{48}$/.test(form.get("id") || "") || !/^[a-f0-9]{48}$/.test(form.get("state") || ""))
    return page("Invalid approval", ["Nothing was issued."], 400);
  const decision = form.get("decision");
  if (!["approve", "deny"].includes(decision)) return page("Invalid approval", ["Nothing was issued."], 400);
  const browser = cookieValue(request, cookieName(form.get("state"))) || "";
  const consumed = await consentState(env, form.get("id"), "consume", { state: form.get("state"),
    browser: await s256(browser), csrf: form.get("csrf") });
  if (consumed.status === 400) return page("Approval expired", ["Start the connection again. Nothing was issued."], 400);
  if (consumed.status === 403) return page("Approval was refused", ["The approval did not match this browser. Nothing was issued."], 403);
  if (!consumed.ok) throw new Error("Consent consumption failed");
  const pending = await consumed.json();
  pending.grant = await consentCipher(browser, form.get("id"), pending.encryptedGrant, true);
  try { requireApprovedClient(pending.grant.request.clientId, env); }
  catch { return refusalPage(null, "This client is no longer approved by the server."); }
  if (decision === "deny") {
    const auth = pending.grant.request;
    // Use the provider's registration/loopback rules, without issuing a grant.
    const params = new URLSearchParams({ response_type: "code", client_id: auth.clientId,
      redirect_uri: auth.redirectUri, code_challenge: auth.codeChallenge, code_challenge_method: "S256" });
    try { await env.OAUTH_PROVIDER.parseAuthRequest(new Request(`${new URL(request.url).origin}/authorize?${params}`)); }
    catch (error) {
      if (!["Invalid client. The clientId provided does not match to this client.",
        "Invalid redirect URI. The redirect URI provided does not match any registered URI for this client."].includes(error.message)) throw error;
      return page("Client registration changed", ["Start the connection again. Nothing was issued."], 400);
    }
    const redirect = new URL(auth.redirectUri);
    redirect.searchParams.set("error", "access_denied");
    if (auth.state) redirect.searchParams.set("state", auth.state);
    return clientHandoff(redirect.toString(), pending.state, "Connection denied");
  }
  // completeAuthorization revalidates the client's registered redirect URI.
  let redirectTo;
  try { ({ redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization(pending.grant)); }
  catch (error) {
    if (error.message !== "Invalid redirect URI. The redirect URI provided does not match any registered URI for this client.") throw error;
    return page("Client registration changed", ["Start the connection again. Nothing was issued."], 400);
  }

  return clientHandoff(redirectTo, pending.state, "Connection approved");
}
