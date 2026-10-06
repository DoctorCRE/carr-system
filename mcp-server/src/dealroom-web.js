// Browser entrypoint for the configured production app host (with a narrow
// legacy Deal Room compatibility door).
//
// Google proves identity; identity.js reduces it to one of the two partner
// actors; an opaque, server-side session lets the installed PWA reuse that
// actor for the existing MCP and pipeline handlers. No identity or deal data
// is stored in the cookie.

import { USAGE_PATH, usageResponse } from './usage-signals.js';
import {
  exchangeGoogleCode,
  googleAuthorizationUrl,
  randomString,
  verifyGoogleIdToken,
} from "./google-oidc.js";
import { authenticatedIdentity, personalScopeForActor, propsForSlug, slugForEmail } from "./identity.js";
import { normalizeRoomPaging, ROOM_BODY_MAX } from "./partner-room.js";
import { redeemProgram6BrowserChallenge } from "./program6-browser-challenge.js";
import { program6ActionsEnabled } from "./program6-feature-flag.js";
import { workspaceCommandCenterEnabled } from "./workspace-feature-flag.js";
import { COMMAND_CENTER_PATH } from "./workspace-command-center.js";
import { neon } from "@neondatabase/serverless";
import { readWorkInventoryCensus, WORK_INVENTORY_PATH } from "./work-inventory-census.v5.js";
import { ATLAS_GRAPH_PATH, readAtlasInventoryGraph } from "./atlas-inventory-graph.v5.js";
import { PROGRAM_CONTROLLER_PATH, readProgramControllerCensus } from "./program-controller-census.v5.js";
import { METERING_PATH, readMeteringProjection } from "./cost-ledger-projection.v5.js";
import {
  BUSINESS_ASSET_PATH, CLIENTS_ROUTE, VENDORS_ROUTE,
  createWorkspaceBusinessReader, isBusinessApiPath,
} from "./workspace-business-read.js";
import { isTourInternalRequest } from "./tour-internal-web.js";
import { executeRegisteredTool } from "./tools.js";
import { readDealWithJev } from "./jev-deal-reading.js";
import { callTool } from "./mcp.js";

export const DEALROOM_ASSET_DIRECTORY = "../out/doctorcre-artifacts/current"; // mirrors wrangler.toml [assets]

const SESSION_COOKIE = "__Host-dealroom_session";
const PENDING_COOKIE = "__Host-dealroom_oauth";
const PENDING_PREFIX = "dealroom_pending:";
const SESSION_PREFIX = "dealroom_session:";
const ACTION_CHALLENGE_PREFIX = "dealroom_action_challenge:";
const PENDING_TTL = 600;
const SESSION_IDLE_TTL = 12 * 60 * 60;
const SESSION_ABSOLUTE_TTL = 7 * 24 * 60 * 60;
const SESSION_REFRESH_WINDOW = 60 * 60;
const REAUTH_TTL = 10 * 60;
const ACTION_CHALLENGE_TTL = 5 * 60;
const SYSTEM_WORK_MAX_BODY = 16 * 1024;
const SYSTEM_WORK_PREFIX = "/api/system-work";
const COMMAND_CENTER_API_PREFIX = "/api/v1/";
const JEV_DEAL_READING_PATH = "/api/v1/jev-deal-reading";
// The Model Room observatory (Joe's ruling 0892c539). One read door onto the
// partner-room wire and one write door back into it, both behind the same
// cookie session the rest of this host uses.  The room's own body cap is
// counted in CHARACTERS; a request carrying 20,000 characters of UTF-8 can be
// several times that in bytes, so the transport cap is deliberately larger and
// the character cap is enforced separately below.
const ROOM_PREFIX = "/api/room";
const ROOM_MAX_BODY = 96 * 1024;
// The RECONNECT button's control turn (complete spec section 17). The panel
// names an ACTION and a DESK; it never supplies the JSON, the seat, the kind or
// the sponsor. This Worker mints the whole receipt body from the two validated
// values, so the widest thing a compromised page could ask for is a sign-in
// flow on a desk name that the bridge then has to find in its own registry.
const ROOM_CONTROL_ACTIONS = new Set(["login"]);
const ROOM_DESK_NAME = /^[a-z0-9][a-z0-9-]{0,47}$/;
const APPROVAL_ACTIONS = new Map([
  ["accept-ready-plan", "plan_hash"],
  ["accept-outcome-feedback", "feedback_hash"],
]);

const JSON_HEADERS = { "content-type": "application/json", "cache-control": "no-store" };
const PUBLIC_SHELL = new Map([
  ["/manifest.webmanifest", "/public-shell/manifest.webmanifest"],
  ["/sw.js", "/public-shell/sw.js"],
  ["/offline.html", "/public-shell/offline.html"],
]);
const DEALROOM_HOST_PATTERN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
// DoctorCRE's own page routes. The app Worker serves the HTML itself but first
// forwards the request here, unchanged, as its sign-in gate and requires a 200.
// A page path missing from this list falls through to the OAuth provider and the
// gate refuses it, whatever the app has shipped (defect 4ef6ce10: the V5-UX-C10
// inventory screen was delivered without this entry). Each new app page adds its
// route here; they share the workspace flag with Home, Clients and Vendors.
const APP_DOCUMENT_PATHS = new Set(["/work-inventory", "/tasks", "/business", "/pipeline", "/control-room", "/incidents", "/notifications", "/conversations"]);
const DEALROOM_EXACT_PATHS = new Set([
  "/", "/index.html", "/deals", "/leads", "/leads.html", "/workspace", "/workspace.html", "/system-work.html", "/room.html", "/queue.html",
  CLIENTS_ROUTE, VENDORS_ROUTE, BUSINESS_ASSET_PATH, ...APP_DOCUMENT_PATHS,
  "/manifest.webmanifest", "/sw.js", "/offline.html", "/tours",
]);
// Clients and Vendors are two views of one authenticated business asset. The
// route, not the file name, is the address a partner keeps, so the .html path
// itself is a redirect below rather than a second bookmarkable surface.
const DEALROOM_ROUTE_ASSETS = new Map([
  ["/deals", "/index.html"],
  ["/leads", "/leads.html"],
  ["/workspace", "/workspace.html"],
  [CLIENTS_ROUTE, BUSINESS_ASSET_PATH],
  [VENDORS_ROUTE, BUSINESS_ASSET_PATH],
]);
const BUSINESS_VIEW_PATHS = new Set([CLIENTS_ROUTE, VENDORS_ROUTE]);
// One typed refusal per read-model failure class, so the browser can tell a bad
// filter from a refused audience from an unavailable database.
const BUSINESS_ERROR_STATUS = {
  QUERY_INVALID: 400,
  AUTHORIZATION_REFUSED: 403,
  TENANT_SCOPE_REFUSED: 404,
  RECORD_NOT_FOUND: 404,
  VIEWER_OWNER_UNKNOWN: 409,
  FRESHNESS_UNKNOWN: 409,
  DEPENDENCY_UNAVAILABLE: 503,
  // A read the deployment has not been given access to is unavailable, not
  // broken: 503 like the other dependency answers, and it says which CLASS of
  // access is missing so an operator is not left guessing.
  DEPENDENCY_NOT_PROVISIONED: 503,
  INTERNAL_ERROR: 500,
};
// The only classes an unprovisioned read may name. Anything else answers with a
// bare code, so no driver message, statement text or schema name can reach a
// browser through this door.
const BUSINESS_DEPENDENCY_CLASSES = new Set(["read_access", "read_source", "read_credential"]);
const DEALROOM_PATH_PREFIXES = ["/auth/", "/api/system-work/", "/api/room/", "/api/tours/", "/tours/", COMMAND_CENTER_API_PREFIX, "/css/", "/js/", "/data/", "/icons/"];
const LEGACY_BROWSER_REDIRECT_PATHS = new Set([
  "/", "/index.html", "/deals", "/leads", "/leads.html", "/workspace", "/workspace.html",
  "/system-work.html", "/room.html", "/queue.html", "/auth/login", "/auth/reauth",
]);

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function redirect(location, cookies = []) {
  const headers = new Headers({ location, "cache-control": "no-store" });
  for (const cookie of cookies) headers.append("set-cookie", cookie);
  return new Response(null, { status: 302, headers });
}

function cookieValue(request, name) {
  const header = request.headers.get("cookie") || "";
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index < 0) continue;
    if (part.slice(0, index).trim() === name) return part.slice(index + 1).trim();
  }
  return null;
}

function sessionCookie(token, maxAge) {
  return `${SESSION_COOKIE}=${token}; Path=/; Max-Age=${maxAge}; Secure; HttpOnly; SameSite=Lax`;
}

function pendingCookie(state, maxAge = PENDING_TTL) {
  return `${PENDING_COOKIE}=${state}; Path=/; Max-Age=${maxAge}; Secure; HttpOnly; SameSite=Lax`;
}

function clearCookie(name) {
  return `${name}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax`;
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

// The host is a reviewed Worker environment setting, never a request Host
// header.  This keeps OAuth callbacks, browser redirects, and CSRF checks on
// the exact deployment that owns the session.  A missing or malformed setting
// deliberately closes the Deal Room route rather than falling back to prod.
function dealroomOrigin(env) {
  const host = env?.APP_HOST || env?.DEALROOM_HOST;
  if (typeof host !== "string" || !DEALROOM_HOST_PATTERN.test(host)) return null;
  return `https://${host}`;
}

function doctorcreAppOrigin(env) {
  const host = env?.DOCTORCRE_APP_HOST;
  if (typeof host !== "string" || !DEALROOM_HOST_PATTERN.test(host)) return null;
  return `https://${host}`;
}

function dealroomOriginForRequest(request, env) {
  let requestOrigin;
  try { requestOrigin = new URL(request.url).origin; }
  catch { return null; }
  for (const origin of [dealroomOrigin(env), doctorcreAppOrigin(env)]) {
    if (origin && requestOrigin === origin) return origin;
  }
  return null;
}

function envForDealroomOrigin(env, origin) {
  const scoped = Object.create(env || null);
  scoped.PRIMARY_APP_HOST = env?.APP_HOST || env?.DEALROOM_HOST;
  scoped.APP_HOST = new URL(origin).hostname;
  return scoped;
}

function legacyDealroomOrigin(env) {
  const host = env?.LEGACY_DEALROOM_HOST;
  if (typeof host !== "string" || !DEALROOM_HOST_PATTERN.test(host)) return null;
  return `https://${host}`;
}

function requestMatchesDealroomOrigin(request, origin) {
  try {
    return Boolean(origin) && new URL(request.url).origin === origin;
  } catch {
    return false;
  }
}

function callbackUri(origin) {
  return `${origin}/auth/callback`;
}

function safeReturnTo(value, origin) {
  if (!value) return "/";
  try {
    const parsed = new URL(value, origin);
    if (parsed.origin !== origin) return "/";
    return parsed.pathname + parsed.search + parsed.hash;
  } catch {
    return "/";
  }
}

function withHeaders(response, additions, status = response.status) {
  const headers = new Headers(response.headers);
  for (const [name, value] of Object.entries(additions)) headers.set(name, value);
  return new Response(response.body, { status, statusText: response.statusText, headers });
}

export const DEALROOM_CSP = [
  "default-src 'self'",
  "base-uri 'none'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "script-src 'self'",
  "style-src 'self' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com",
  "img-src 'self' data:",
  "connect-src 'self'",
  "worker-src 'self'",
  "manifest-src 'self'",
].join("; ");

function withSecurityHeaders(response) {
  const headers = new Headers(response.headers);
  // Deal Room is a static asset bundle.  The public shell uses external CSS
  // and JavaScript too, so neither scripts nor styles need unsafe-inline.
  // connect-src is same-origin only: no served page loads post-call-client.js
  // (the only loopback client), and that client refuses non-loopback pages.
  headers.set("content-security-policy", DEALROOM_CSP);
  headers.set("strict-transport-security", "max-age=31536000; includeSubDomains");
  headers.set("x-content-type-options", "nosniff");
  headers.set("x-frame-options", "DENY");
  headers.set("referrer-policy", "no-referrer");
  headers.set("permissions-policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()");
  headers.set("cross-origin-opener-policy", "same-origin");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function asset(env, request, pathname) {
  if (!env.ASSETS?.fetch) return null;
  const url = new URL(request.url);
  url.pathname = pathname;
  url.search = "";
  const response = await env.ASSETS.fetch(new Request(url, { method: "GET", headers: request.headers }));
  return response.status === 404 ? null : response;
}

async function publicShellAsset(env, request, pathname, status) {
  const mapped = pathname.startsWith("/icons/") ? `/public-shell${pathname}` : PUBLIC_SHELL.get(pathname);
  if (!mapped) return null;
  const response = await asset(env, request, mapped);
  if (!response) return null;
  const headers = { "cache-control": pathname === "/sw.js" ? "no-cache" : "public, max-age=3600" };
  if (pathname === "/sw.js") {
    headers["content-type"] = "application/javascript; charset=utf-8";
    headers["service-worker-allowed"] = "/";
  } else if (pathname === "/manifest.webmanifest") {
    headers["content-type"] = "application/manifest+json; charset=utf-8";
  } else if (pathname.endsWith(".png")) {
    headers["content-type"] = "image/png";
  }
  return withHeaders(response, headers, status ?? response.status);
}

async function refusal(env, request) {
  const response = await asset(env, request, "/public-shell/refusal.html");
  if (response) return withHeaders(response, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" }, 403);
  return new Response("<!doctype html><title>Not authorized</title><h1>Not authorized</h1><p>This Deal Room is limited to the two CARR partner accounts.</p>",
    { status: 403, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}

async function startLogin(request, env, pending = {}) {
  if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET || !env.OAUTH_KV) {
    return new Response("Sign-in is not configured.", { status: 503, headers: { "cache-control": "no-store" } });
  }
  const origin = dealroomOrigin(env);
  if (!origin) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const state = randomString(24);
  const verifier = randomString(48);
  await env.OAUTH_KV.put(PENDING_PREFIX + state, JSON.stringify({
    verifier,
    returnTo: safeReturnTo(url.searchParams.get("return_to"), origin),
    ...pending,
  }), { expirationTtl: PENDING_TTL });
  const google = await googleAuthorizationUrl({ clientId: env.GOOGLE_CLIENT_ID,
    redirectUri: callbackUri(origin), state, verifier });
  return redirect(google.toString(), [pendingCookie(state)]);
}

async function completeLogin(request, env, dependencies) {
  const origin = dealroomOrigin(env);
  if (!origin) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  const stateCookie = cookieValue(request, PENDING_COOKIE);
  const finish = (response) => {
    const headers = new Headers(response.headers);
    headers.append("set-cookie", clearCookie(PENDING_COOKIE));
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  };

  if (!state || !code || !stateCookie || stateCookie !== state) {
    return finish(new Response("That sign-in request is invalid or expired.",
      { status: 400, headers: { "cache-control": "no-store" } }));
  }
  const pendingKey = PENDING_PREFIX + state;
  const pending = await env.OAUTH_KV.get(pendingKey, { type: "json" });
  await env.OAUTH_KV.delete(pendingKey);
  if (!pending?.verifier) {
    return finish(new Response("That sign-in request is invalid or expired.",
      { status: 400, headers: { "cache-control": "no-store" } }));
  }

  let tokenResponse;
  let claims;
  try {
    tokenResponse = await dependencies.exchangeGoogleCodeFn({ code,
      clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET,
      redirectUri: callbackUri(origin), verifier: pending.verifier });
    claims = await dependencies.verifyGoogleIdTokenFn(tokenResponse.id_token, env.GOOGLE_CLIENT_ID, env);
  } catch {
    return finish(new Response("Google identity could not be verified.",
      { status: 401, headers: { "cache-control": "no-store" } }));
  }

  if (claims.email_verified !== true && claims.email_verified !== "true") return finish(await refusal(env, request));
  const slug = dependencies.slugForEmailFn(claims.email);
  if (!slug) return finish(await refusal(env, request));

  const now = dependencies.now();
  const opaque = randomString(32);
  const sessionKey = SESSION_PREFIX + await sha256(opaque);
  const props = dependencies.propsForSlugFn(slug, { email: claims.email, sub: claims.sub,
    via: "dealroom-cookie", client_id: "dealroom-pwa" });
  if (pending.purpose === "reauth") {
    const current = await sessionFor(request, env, dependencies);
    if (!current || current.key !== pending.sessionKey || current.actor?.slug !== slug) {
      return finish(new Response("That reauthentication request is invalid or expired.",
        { status: 400, headers: { "cache-control": "no-store" } }));
    }
    current.session.reauthAt = now;
    await env.OAUTH_KV.put(current.key, JSON.stringify(current.session), {
      expirationTtl: Math.max(1, Math.floor((current.session.expiresAt - now) / 1000)),
    });
    return finish(redirect(`${origin}${safeReturnTo(pending.returnTo, origin)}`));
  }

  await env.OAUTH_KV.put(sessionKey, JSON.stringify({
    props,
    origin,
    createdAt: now,
    expiresAt: now + SESSION_IDLE_TTL * 1000,
    csrfToken: randomString(32),
    reauthAt: now,
  }),
    { expirationTtl: SESSION_IDLE_TTL });
  return finish(redirect(`${origin}${safeReturnTo(pending.returnTo, origin)}`,
    [sessionCookie(opaque, SESSION_IDLE_TTL)]));
}

async function sessionFor(request, env, dependencies) {
  const opaque = cookieValue(request, SESSION_COOKIE);
  if (!opaque || !env.OAUTH_KV) return null;
  const key = SESSION_PREFIX + await sha256(opaque);
  const session = await env.OAUTH_KV.get(key, { type: "json" });
  if (!session) return null;
  const now = dependencies.now();
  const actor = dependencies.actorFromPropsFn(session.props);
  const currentOrigin = dealroomOrigin(env);
  const primaryHost = env?.PRIMARY_APP_HOST || env?.APP_HOST || env?.DEALROOM_HOST;
  const primaryOrigin = typeof primaryHost === "string" && DEALROOM_HOST_PATTERN.test(primaryHost)
    ? `https://${primaryHost}` : null;
  // Sessions issued before the independent host existed had no origin field.
  // They remain valid only on the primary host; new sessions stay pinned to
  // the host that issued their host-only cookie.
  if (session.origin ? session.origin !== currentOrigin : currentOrigin !== primaryOrigin) return null;
  const absoluteEnd = Number(session.createdAt) + SESSION_ABSOLUTE_TTL * 1000;
  if (!actor || !Number.isFinite(absoluteEnd) || session.expiresAt <= now || absoluteEnd <= now) {
    await env.OAUTH_KV.delete(key);
    return null;
  }

  let changed = false;
  // Sessions issued before the browser-action boundary was introduced have no
  // synchronizer token.  Mint it server-side on first use; it never enters the
  // cookie and a legacy session is not magically treated as freshly reauthed.
  if (!session.csrfToken) {
    session.csrfToken = randomString(32);
    changed = true;
  }

  let refreshCookie = null;
  if (session.expiresAt - now <= SESSION_REFRESH_WINDOW * 1000) {
    const seconds = Math.min(SESSION_IDLE_TTL, Math.floor((absoluteEnd - now) / 1000));
    session.expiresAt = now + seconds * 1000;
    changed = true;
    refreshCookie = sessionCookie(opaque, seconds);
  }
  if (changed) {
    const seconds = Math.max(1, Math.min(SESSION_IDLE_TTL, Math.floor((session.expiresAt - now) / 1000)));
    await env.OAUTH_KV.put(key, JSON.stringify(session), { expirationTtl: seconds });
  }
  return { actor, key, session, csrfToken: session.csrfToken, reauthAt: Number(session.reauthAt) || null, refreshCookie };
}

function attachRefresh(response, cookie) {
  if (!cookie) return response;
  const headers = new Headers(response.headers);
  headers.append("set-cookie", cookie);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function signOut(request, env, origin) {
  const opaque = cookieValue(request, SESSION_COOKIE);
  if (opaque && env.OAUTH_KV) await env.OAUTH_KV.delete(SESSION_PREFIX + await sha256(opaque));
  return redirect(`${origin}/`, [clearCookie(SESSION_COOKIE), clearCookie(PENDING_COOKIE)]);
}

function sameOrigin(request, env) {
  return request.headers.get("origin") === dealroomOrigin(env);
}

function equalStrings(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return mismatch === 0;
}

async function systemWorkBody(request, maxBytes = SYSTEM_WORK_MAX_BODY) {
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && (length < 0 || length > maxBytes)) return { error: json({ error: "payload_too_large" }, 413) };
  const raw = await request.clone().text();
  if (new TextEncoder().encode(raw).byteLength > maxBytes) return { error: json({ error: "payload_too_large" }, 413) };
  try { return { value: JSON.parse(raw) }; }
  catch { return { error: json({ error: "invalid_json" }, 400) }; }
}

// One browser-POST guard for every authenticated write on this host: JSON only,
// same origin, same-site fetch metadata, and a synchronizer token that never
// enters the cookie.  The Model Room composer reuses it verbatim rather than
// growing a second, subtly different door.
async function guardSystemWorkPost(request, env, session, { readBody = false, maxBytes = SYSTEM_WORK_MAX_BODY } = {}) {
  const contentType = request.headers.get("content-type") || "";
  if (!/^application\/json(?:\s*;|$)/i.test(contentType)) return { error: json({ error: "unsupported_media_type" }, 415) };
  if (!sameOrigin(request, env)) return { error: json({ error: "forbidden", reason: "origin_mismatch" }, 403) };
  if (request.headers.get("sec-fetch-site") !== "same-origin") return { error: json({ error: "forbidden", reason: "fetch_metadata_mismatch" }, 403) };
  if (!equalStrings(request.headers.get("x-carr-csrf"), session.csrfToken)) return { error: json({ error: "forbidden", reason: "csrf_mismatch" }, 403) };
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && (length < 0 || length > maxBytes)) return { error: json({ error: "payload_too_large" }, 413) };
  // Typed controllers parse their own request body.  Do not clone/read here:
  // cloning after request.json() is a Worker error and would make the security
  // guard depend on handler order.  The challenge endpoint is the one route
  // that owns its body, so it opts into the exact byte-length check below.
  return readBody ? systemWorkBody(request, maxBytes) : { value: null };
}

// ---------------------------------------------------------------- Model Room
//
// THE PANEL READS ONLY THE WIRE (Joe's ruling 0892c539, single-source posture).
// There is no second API for desk state, no Worker read of a local file, and no
// caller-supplied identity anywhere below: the roster, liveness, cursor lag and
// cycle age all come out of the same turns these two endpoints move, and the
// bridge's heartbeat receipt is what makes that sufficient.

/** GET /api/room/turns — the read-room verb's own query, one door over. */
async function roomTurns(request, env, session, dependencies) {
  if (typeof dependencies.roomReadFn !== "function") return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const rawAfter = url.searchParams.get("after_seq");
  const rawLimit = url.searchParams.get("limit");
  if (rawAfter !== null && !/^\d{1,15}$/.test(rawAfter)) return json({ error: "after_seq_invalid" }, 400);
  if (rawLimit !== null && !/^\d{1,15}$/.test(rawLimit)) return json({ error: "limit_invalid" }, 400);
  // Clamped by the room's own normalizer so the browser cursor and the verb
  // cursor can never mean two different things.
  const paging = normalizeRoomPaging({
    after_seq: rawAfter === null ? 0 : Number(rawAfter),
    limit: rawLimit === null ? undefined : Number(rawLimit),
  });
  let read;
  try {
    read = await dependencies.roomReadFn(env, { after_seq: paging.after, limit: paging.limit });
  } catch (error) {
    return json({ error: "wire_unavailable", detail: String(error?.message || error).slice(0, 200) }, 503);
  }
  if (!read || read.ok !== true) return json({ error: read?.error || "wire_unavailable" }, 503);
  // The CSRF token rides the authenticated read so the composer needs no second
  // bootstrap call.  A cross-origin page cannot read this response at all — the
  // host sets no CORS headers — so it stays a same-origin secret.
  return json({ ...read,
    actor: { slug: session.actor.slug, display: session.actor.display },
    csrf_token: session.csrfToken });
}

/** GET /api/room/latest — the read-room-latest verb through the same wire. */
async function roomLatest(request, env, session, dependencies) {
  if (typeof dependencies.roomLatestFn !== "function") return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const rawBefore = url.searchParams.get("before_seq");
  const rawLimit = url.searchParams.get("limit");
  const rawMode = url.searchParams.get("mode") || "all";
  if (rawBefore !== null && !/^\d{1,15}$/.test(rawBefore)) return json({ error: "before_seq_invalid" }, 400);
  if (rawLimit !== null && !/^\d{1,15}$/.test(rawLimit)) return json({ error: "limit_invalid" }, 400);
  if (!["all", "conversation"].includes(rawMode)) return json({ error: "mode_invalid" }, 400);
  const { limit } = normalizeRoomPaging({ limit: rawLimit === null ? undefined : Number(rawLimit) });
  let read;
  try {
    read = await dependencies.roomLatestFn(env, {
      before_seq: rawBefore === null ? undefined : Number(rawBefore), limit, mode: rawMode,
    });
  } catch (error) {
    return json({ error: "wire_unavailable", detail: String(error?.message || error).slice(0, 200) }, 503);
  }
  if (!read || read.ok !== true) return json({ error: read?.error || "wire_unavailable" }, 503);
  return json({ ...read, actor: { slug: session.actor.slug, display: session.actor.display },
    csrf_token: session.csrfToken });
}

/** GET /api/room/queue — the read-room-queue verb's exact projection. */
async function roomQueue(_request, env, _session, dependencies) {
  if (typeof dependencies.queueReadFn !== "function") return json({ error: "not_found" }, 404);
  let read;
  try { read = await dependencies.queueReadFn(env, {}); }
  catch (error) { return json({ error: "queue_unavailable", detail: String(error?.message || error).slice(0, 200), live: false }, 503); }
  if (!read || read.ok !== true) return json({ error: read?.error || "queue_unavailable", live: false }, 503);
  // Deliberately no actor or CSRF token here: this is a small, exact read
  // contract. The enqueue form obtains its synchronizer token from /turns.
  return json(read);
}

/**
 * POST /api/room/turn — Joe speaking into the room from the panel.
 *
 * ATTRIBUTION IS SERVER-DERIVED AND THE SEAT IS FIXED. The body is the only
 * thing read off the request: seat is always "human", kind always "turn", the
 * sponsor comes from the verified session, and the idempotency/msg id is minted
 * here.  A request that supplies seat, sponsor, kind, room or msg_id is not
 * rejected — those fields are simply never consulted, which is the same refusal
 * add-room-turn makes and the reason the panel has no seat selector.
 */
// The composer promises the reader that a retry cannot double-post. A random
// id per request does NOT deliver that: the failure this guards is a response
// the browser never saw, where the turn landed and the reader presses retry.
//
// So the id is minted server-side and DETERMINISTICALLY, from the session, the
// exact body, and a one-minute bucket — the room's own msg_id uniqueness then
// collapses the retry into a dedup instead of a second turn. Nothing about it
// comes from the request body's own fields, so this is not a caller-supplied
// id wearing a different hat.
//
// THE DELIBERATE TRADE: the same person posting the same exact text twice
// inside the same minute lands once. That is the case worth collapsing — it is
// a double-submit far more often than it is intent — and a minute is short
// enough that a genuine repeat a moment later still goes through.
const ROOM_DEDUP_WINDOW_MS = 60_000;

export async function roomMessageId(sessionKey, body, now) {
  const bucket = Math.floor(now / ROOM_DEDUP_WINDOW_MS);
  const digest = await sha256(`room-turn\n${sessionKey}\n${bucket}\n${body}`);
  return [digest.slice(0, 8), digest.slice(8, 12), digest.slice(12, 16),
    digest.slice(16, 20), digest.slice(20, 32)].join("-");
}

export function roomControlTurn(control) {
  if (!control || typeof control !== "object") return null;
  const action = typeof control.action === "string" ? control.action : "";
  const desk = typeof control.desk === "string" ? control.desk : "";
  if (!ROOM_CONTROL_ACTIONS.has(action) || !ROOM_DESK_NAME.test(desk)) return null;
  // Rebuilt from validated parts, never echoed: nothing a caller wrote reaches
  // the wire verbatim, so the receipt the bridge parses cannot smuggle a field.
  return { kind: "receipt", body: JSON.stringify({ control: { action, desk } }) };
}

async function roomTurnPost(request, env, session, dependencies) {
  if (typeof dependencies.roomWriteFn !== "function") return json({ error: "not_found" }, 404);
  const guard = await guardSystemWorkPost(request, env, session, { readBody: true, maxBytes: ROOM_MAX_BODY });
  if (guard.error) return guard.error;

  let kind = "turn";
  let body = typeof guard.value?.body === "string" ? guard.value.body : "";
  if (guard.value?.control !== undefined) {
    const control = roomControlTurn(guard.value.control);
    if (!control) return json({ error: "control_not_allowed" }, 400);
    ({ kind, body } = control);
  }
  if (!body.trim()) return json({ error: "body_required" }, 400);
  if (body.length > ROOM_BODY_MAX) return json({ error: "body_too_long", limit: ROOM_BODY_MAX, got: body.length }, 413);

  const scope = personalScopeForActor(session.actor);
  if (scope.status !== "personal") {
    return json({ error: "no_sponsoring_partner",
      hint: "the room only takes turns from a credential a partner sponsors" }, 403);
  }
  let posted;
  try {
    posted = await dependencies.roomWriteFn(env, {
      sponsor: scope.sponsor, seat: "human", kind, body,
      msgId: await roomMessageId(session.key, body, dependencies.now()),
      originChannel: "browser-human", originActor: session.actor.slug,
    });
  } catch (error) {
    return json({ error: "wire_unavailable", detail: String(error?.message || error).slice(0, 200) }, 503);
  }
  if (!posted || posted.ok !== true) return json({ error: posted?.error || "wire_unavailable" }, 503);
  return json(posted);
}

async function roomRequest(request, env, session, dependencies) {
  const pathname = new URL(request.url).pathname;
  if (pathname === `${ROOM_PREFIX}/latest`) {
    if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
    return roomLatest(request, env, session, dependencies);
  }
  if (pathname === `${ROOM_PREFIX}/turns`) {
    if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
    return roomTurns(request, env, session, dependencies);
  }
  if (pathname === `${ROOM_PREFIX}/turn`) {
    if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
    return roomTurnPost(request, env, session, dependencies);
  }
  if (pathname === `${ROOM_PREFIX}/queue`) {
    if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
    return roomQueue(request, env, session, dependencies);
  }
  return json({ error: "not_found" }, 404);
}

function approvalTarget(action, args) {
  const hashKey = APPROVAL_ACTIONS.get(action);
  if (!hashKey || !args || typeof args !== "object") return null;
  const humanRef = args.human_ref;
  const baseVersion = args.base_version;
  const proposalHash = args[hashKey];
  const idempotencyKey = args.idempotency_key;
  if (!/^WR-[0-9]{1,12}$/.test(humanRef || "") || !Number.isInteger(baseVersion) || baseVersion < 1 ||
      !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(idempotencyKey || "") ||
      !/^sha256:[0-9a-f]{64}$/.test(proposalHash || "")) return null;
  return { action, human_ref: humanRef, base_version: baseVersion,
    idempotency_key: idempotencyKey, [hashKey]: proposalHash };
}

/** A challenge binds the exact immutable approval target, not a mutable UI label. */
export async function actionDigestFor(action, args) {
  const target = approvalTarget(action, args);
  return target ? sha256(JSON.stringify(target)) : null;
}

/**
 * Reusable authorization seam for the exact typed Program 6 controller.
 * It returns a refusal Response, or null when the action may proceed.
 */
export async function authorizeProgram6Action({ request, env, actor, session, action, args,
  now = Date.now(), redeemChallenge = redeemProgram6BrowserChallenge }) {
  const guard = await guardSystemWorkPost(request, env, session);
  if (guard.error) return guard.error;
  if (!APPROVAL_ACTIONS.has(action)) return null;
  if (!session.reauthAt || now - session.reauthAt > REAUTH_TTL * 1000) {
    return json({ error: "reauth_required", reauth_url: "/auth/reauth?return_to=/" }, 401);
  }
  const token = request.headers.get("x-carr-action-challenge");
  if (!token || token.length > 256) return json({ error: "action_challenge_required" }, 401);
  const key = ACTION_CHALLENGE_PREFIX + token;
  const challenge = await env.OAUTH_KV.get(key, { type: "json" });
  const digest = await actionDigestFor(action, args);
  if (!challenge || challenge.expiresAt <= now || challenge.sessionKey !== session.key ||
      challenge.action !== action || !digest || !equalStrings(challenge.digest, digest)) {
    return json({ error: "invalid_action_challenge" }, 403);
  }
  const redemption = await redeemChallenge({ env, actor,
    tokenDigest: await sha256(token), sessionDigest: await sha256(session.key),
    action, materialDigest: digest, idempotencyKey: args.idempotency_key });
  if (!redemption?.ok) return json({ error: redemption?.error || "invalid_action_challenge" },
    redemption?.error === "authority_connection_unavailable" ? 503 : 403);
  await env.OAUTH_KV.delete(key);
  return null;
}

async function systemWorkSession(session, dependencies) {
  const now = dependencies.now();
  return json({
    actor: { slug: session.actor.slug, display: session.actor.display },
    csrf_token: session.csrfToken,
    reauth_required: !session.reauthAt || now - session.reauthAt > REAUTH_TTL * 1000,
    reauth_url: "/auth/reauth?return_to=/",
    challenge_ttl_seconds: ACTION_CHALLENGE_TTL,
  });
}

async function createActionChallenge(request, env, session, dependencies) {
  const guard = await guardSystemWorkPost(request, env, session, { readBody: true });
  if (guard.error) return guard.error;
  const target = approvalTarget(guard.value?.action, guard.value);
  if (!target || Object.keys(guard.value).sort().join(",") !== Object.keys(target).sort().join(",")) {
    return json({ error: "invalid_action_challenge_target" }, 400);
  }
  const now = dependencies.now();
  if (!session.reauthAt || now - session.reauthAt > REAUTH_TTL * 1000) {
    return json({ error: "reauth_required", reauth_url: "/auth/reauth?return_to=/" }, 401);
  }
  const token = randomString(32);
  const expiresAt = now + ACTION_CHALLENGE_TTL * 1000;
  await env.OAUTH_KV.put(ACTION_CHALLENGE_PREFIX + token, JSON.stringify({
    sessionKey: session.key,
    action: target.action,
    digest: await actionDigestFor(target.action, target),
    expiresAt,
  }), { expirationTtl: ACTION_CHALLENGE_TTL });
  return json({ challenge: token, expires_at: new Date(expiresAt).toISOString() });
}

async function prepareSystemWorkControllerRequest(request, env, session) {
  if (request.method !== "POST") return { request };
  const guard = await guardSystemWorkPost(request, env, session, { readBody: true });
  if (guard.error) return { error: guard.error };
  const headers = new Headers(request.headers);
  headers.delete("content-length");
  return { request: new Request(request.url, { method: "POST", headers,
    body: JSON.stringify(guard.value) }) };
}

async function startReauth(request, env, dependencies) {
  const origin = dealroomOrigin(env);
  if (!origin) return json({ error: "not_found" }, 404);
  const session = await sessionFor(request, env, dependencies);
  if (!session) return redirect(`${origin}/auth/login?return_to=${encodeURIComponent(safeReturnTo(new URL(request.url).searchParams.get("return_to"), origin))}`);
  const response = await startLogin(request, env, { purpose: "reauth", sessionKey: session.key });
  return attachRefresh(response, session.refreshCookie);
}

async function commandCenterResponse(request, env, session, dependencies) {
  if (!workspaceCommandCenterEnabled(env)) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const queryKeys = [...url.searchParams.keys()];
  if (queryKeys.length > 0 && (queryKeys.length !== 1 || queryKeys[0] !== "viewer" || url.searchParams.get("viewer") !== session.actor.slug)) {
    return json({ error: "AUTHORIZATION_REFUSED" }, 403);
  }
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: { allow: "GET, HEAD, OPTIONS", "cache-control": "no-store" } });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response(JSON.stringify({ error: "METHOD_NOT_ALLOWED" }), {
      status: 405, headers: { ...JSON_HEADERS, allow: "GET, HEAD, OPTIONS" },
    });
  }
  if (typeof dependencies.commandCenterReader !== "function") return json({ error: "DEPENDENCY_UNAVAILABLE" }, 503);
  try {
    const payload = await dependencies.commandCenterReader(env, session.actor, env.CORRELATION_ID);
    if (request.method === "HEAD") return new Response(null, { status: 200, headers: JSON_HEADERS });
    return json(payload);
  } catch (error) {
    const code = ["AUTHORIZATION_REFUSED", "TENANT_SCOPE_REFUSED", "FRESHNESS_UNKNOWN", "DEPENDENCY_UNAVAILABLE", "INTERNAL_ERROR"].includes(error?.code) ? error.code : "INTERNAL_ERROR";
    const status = code === "AUTHORIZATION_REFUSED" ? 403 : code === "TENANT_SCOPE_REFUSED" ? 404 : code === "FRESHNESS_UNKNOWN" ? 409 : code === "DEPENDENCY_UNAVAILABLE" ? 503 : 500;
    return json({ error: code }, status);
  }
}

// Default census reader. Built from the SAME client factory index.js uses for
// commandCenterReader — neon() over DATABASE_URL_READER, wrapped so .query()
// returns { rows } — imported from @neondatabase/serverless directly rather
// than from index.js, which must stay free of any dependency on this route.
async function defaultWorkInventoryReader(env, actor, correlationId, params = {}) {
  const sql = neon(env.DATABASE_URL_READER);
  const client = { query: async (text, values = []) => ({ rows: await sql.query(text, values) }) };
  return readWorkInventoryCensus({
    client, actor, correlationId: correlationId || env.CORRELATION_ID,
    ...params,
  });
}

/**
 * GET /api/v1/work-inventory — the V5-UX-C10 complete work inventory census.
 *
 * Mounted exactly like the Command Center read: same feature flag, same typed
 * error-to-status map, same dependency-injected reader so the route carries no
 * database knowledge. The three query parameters (cursor, limit, kinds,
 * statuses) are handed to the reader unparsed — the reader owns bounding and
 * refusal so the paging semantics keep ONE definition.
 */
async function workInventoryResponse(request, env, session, dependencies) {
  if (!workspaceCommandCenterEnabled(env)) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const allowed = new Set(["cursor", "limit", "kinds", "statuses", "system", "source", "age", "text", "live_library", "id"]);
  if ([...url.searchParams.keys()].some((key) => !allowed.has(key))) {
    return json({ error: "AUTHORIZATION_REFUSED" }, 403);
  }
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: { allow: "GET, HEAD, OPTIONS", "cache-control": "no-store" } });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response(JSON.stringify({ error: "METHOD_NOT_ALLOWED" }), {
      status: 405, headers: { ...JSON_HEADERS, allow: "GET, HEAD, OPTIONS" },
    });
  }
  // The census route owns its own default reader, so the surface is reachable
  // without index.js wiring it; an injected reader (the web test's, or a future
  // caller's) still wins. The default is READER-role only: this is a read.
  const reader = typeof dependencies.workInventoryReader === "function"
    ? dependencies.workInventoryReader : defaultWorkInventoryReader;
  try {
    const payload = await reader(env, session.actor, env.CORRELATION_ID, {
      cursor: url.searchParams.get("cursor"),
      limit: url.searchParams.get("limit"),
      kinds: url.searchParams.get("kinds"),
      statuses: url.searchParams.get("statuses"),
      ...Object.fromEntries(["system", "source", "age", "text", "live_library", "id"]
        .filter(key => url.searchParams.has(key)).map(key => [key, url.searchParams.get(key)])),
    });
    if (request.method === "HEAD") return new Response(null, { status: 200, headers: JSON_HEADERS });
    return json(payload);
  } catch (error) {
    const code = ["AUTHORIZATION_REFUSED", "TENANT_SCOPE_REFUSED", "FRESHNESS_UNKNOWN", "DEPENDENCY_UNAVAILABLE", "INTERNAL_ERROR"].includes(error?.code) ? error.code : "INTERNAL_ERROR";
    const status = code === "AUTHORIZATION_REFUSED" ? 403 : code === "TENANT_SCOPE_REFUSED" ? 404 : code === "FRESHNESS_UNKNOWN" ? 409 : code === "DEPENDENCY_UNAVAILABLE" ? 503 : 500;
    return json({ error: code }, status);
  }
}

// A partner asks for one advisory at a time. This route reuses the authenticated
// Deal Room record verb, then sends only bounded deal evidence to TypeSafe.
async function jevDealReadingResponse(request, env, session, dependencies) {
  if (request.method !== "POST") return json({ error: "METHOD_NOT_ALLOWED" }, 405);
  if (!sameOrigin(request, env)) return json({ error: "forbidden", reason: "origin_mismatch" }, 403);
  let args;
  try {
    const body = await request.text();
    if (body.length > 1024) throw new Error("too_large");
    args = JSON.parse(body);
  } catch { return json({ error: "INVALID_REQUEST" }, 400); }
  if (!args || Object.keys(args).length !== 1 ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(args.deal || ""))
    return json({ error: "INVALID_REQUEST" }, 400);
  try {
    const reader = dependencies.jevDealRecordReader || (async (deal) => {
      const sql = neon(env.DATABASE_URL_READER);
      const client = { query: async (text, values = []) => ({ rows: await sql.query(text, values) }) };
      return executeRegisteredTool(client, session.actor, "get-deal-room", { deal });
    });
    const record = await reader(args.deal);
    const read = dependencies.jevDealRead || readDealWithJev;
    return json(await read(record, { askJev: env.TYPESAFE_API_KEY ? request =>
      callTool(env, session.actor, "ask-jev", {
        idempotency_key: crypto.randomUUID(), session_id: `worker-deal-reading-${crypto.randomUUID()}`,
        purpose: "call", ...request,
      }, "full", "app_runtime") : undefined }));
  } catch {
    return json({ error: "DEPENDENCY_UNAVAILABLE" }, 503);
  }
}

// Default Atlas reader. Same client factory as the census route above — neon()
// over DATABASE_URL_READER, wrapped so .query() returns { rows } — so this route
// carries no database knowledge of its own and no write credential can reach it.
async function defaultAtlasGraphReader(env, actor, correlationId, params = {}) {
  const sql = neon(env.DATABASE_URL_READER);
  const client = { query: async (text, values = []) => ({ rows: await sql.query(text, values) }) };
  return readAtlasInventoryGraph({
    client, actor, correlationId: correlationId || env.CORRELATION_ID,
    layer: params.layer, q: params.q, include_retired: params.include_retired,
    limit: params.limit, cursor: params.cursor,
  });
}

/**
 * GET /api/v1/atlas-graph — the V5-UX-C07 Atlas inventory graph and linked index.
 *
 * Mounted exactly like the work-inventory census: same feature flag, same typed
 * error-to-status map, same dependency-injected reader. The five query
 * parameters are handed to the reader unparsed — the reader owns bounding and
 * refusal so the paging semantics keep ONE definition.
 */
async function atlasGraphResponse(request, env, session, dependencies) {
  if (!workspaceCommandCenterEnabled(env)) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const allowed = new Set(["layer", "q", "include_retired", "limit", "cursor"]);
  if ([...url.searchParams.keys()].some((key) => !allowed.has(key))) {
    return json({ error: "AUTHORIZATION_REFUSED" }, 403);
  }
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: { allow: "GET, HEAD, OPTIONS", "cache-control": "no-store" } });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response(JSON.stringify({ error: "METHOD_NOT_ALLOWED" }), {
      status: 405, headers: { ...JSON_HEADERS, allow: "GET, HEAD, OPTIONS" },
    });
  }
  const reader = typeof dependencies.atlasGraphReader === "function"
    ? dependencies.atlasGraphReader : defaultAtlasGraphReader;
  try {
    const payload = await reader(env, session.actor, env.CORRELATION_ID, {
      layer: url.searchParams.get("layer"),
      q: url.searchParams.get("q"),
      include_retired: url.searchParams.get("include_retired"),
      limit: url.searchParams.get("limit"),
      cursor: url.searchParams.get("cursor"),
    });
    if (request.method === "HEAD") return new Response(null, { status: 200, headers: JSON_HEADERS });
    return json(payload);
  } catch (error) {
    const code = ["AUTHORIZATION_REFUSED", "TENANT_SCOPE_REFUSED", "FRESHNESS_UNKNOWN", "DEPENDENCY_UNAVAILABLE", "INTERNAL_ERROR"].includes(error?.code) ? error.code : "INTERNAL_ERROR";
    const status = code === "AUTHORIZATION_REFUSED" ? 403 : code === "TENANT_SCOPE_REFUSED" ? 404 : code === "FRESHNESS_UNKNOWN" ? 409 : code === "DEPENDENCY_UNAVAILABLE" ? 503 : 500;
    return json({ error: code }, status);
  }
}

// Default metering reader. Same client factory as the census routes above --
// neon() over DATABASE_URL_READER, wrapped so .query() returns { rows } -- so
// this route carries no database knowledge of its own and no write credential
// can reach it. The WRITER path of the cost ledger is not mounted here at all.
async function defaultMeteringReader(env, actor, correlationId, params = {}) {
  const sql = neon(env.DATABASE_URL_READER);
  const client = { query: async (text, values = []) => ({ rows: await sql.query(text, values) }) };
  return readMeteringProjection({
    client, actor, correlationId: correlationId || env.CORRELATION_ID,
    tree_ref: params.tree_ref, period: params.period,
  });
}

/**
 * GET /api/v1/metering -- the WR-000111 producer cost projection.
 *
 * Mounted exactly like the Atlas graph: same feature flag, same typed
 * error-to-status map, same dependency-injected reader. The two query
 * parameters are handed to the reader unparsed -- the reader owns bounding and
 * refusal so the semantics keep ONE definition.
 */
async function meteringResponse(request, env, session, dependencies) {
  if (!workspaceCommandCenterEnabled(env)) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const allowed = new Set(["tree_ref", "period"]);
  if ([...url.searchParams.keys()].some((key) => !allowed.has(key))) {
    return json({ error: "AUTHORIZATION_REFUSED" }, 403);
  }
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: { allow: "GET, HEAD, OPTIONS", "cache-control": "no-store" } });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response(JSON.stringify({ error: "METHOD_NOT_ALLOWED" }), {
      status: 405, headers: { ...JSON_HEADERS, allow: "GET, HEAD, OPTIONS" },
    });
  }
  const reader = typeof dependencies.meteringReader === "function"
    ? dependencies.meteringReader : defaultMeteringReader;
  try {
    const payload = await reader(env, session.actor, env.CORRELATION_ID, {
      tree_ref: url.searchParams.get("tree_ref"),
      period: url.searchParams.get("period"),
    });
    if (request.method === "HEAD") return new Response(null, { status: 200, headers: JSON_HEADERS });
    return json(payload);
  } catch (error) {
    const code = ["AUTHORIZATION_REFUSED", "TENANT_SCOPE_REFUSED", "FRESHNESS_UNKNOWN", "DEPENDENCY_UNAVAILABLE", "INTERNAL_ERROR"].includes(error?.code) ? error.code : "INTERNAL_ERROR";
    const status = code === "AUTHORIZATION_REFUSED" ? 403 : code === "TENANT_SCOPE_REFUSED" ? 404 : code === "FRESHNESS_UNKNOWN" ? 409 : code === "DEPENDENCY_UNAVAILABLE" ? 503 : 500;
    return json({ error: code }, status);
  }
}

// Default program-controller reader. Same client factory as the two census
// routes above — neon() over DATABASE_URL_READER, wrapped so .query() returns
// { rows } — so this route carries no database knowledge of its own and no
// write credential can reach it.
async function defaultProgramControllerReader(env, actor, correlationId, params = {}) {
  const sql = neon(env.DATABASE_URL_READER);
  const client = { query: async (text, values = []) => ({ rows: await sql.query(text, values) }) };
  return readProgramControllerCensus({
    client, actor, correlationId: correlationId || env.CORRELATION_ID,
    program_ref: params.program_ref, slice_ref: params.slice_ref, release_ref: params.release_ref,
  });
}

/**
 * GET /api/v1/program-controller — the V5-F02 program-controller census.
 *
 * Mounted exactly like the atlas graph: same feature flag, same typed
 * error-to-status map, same dependency-injected reader.
 *
 * THE ADMITTED PARAMETER SET IS EXACTLY THREE, and `auto_release_requested` is
 * deliberately absent. It is a stated invariant in the census reader, not a
 * store and not a caller input, so asking for it here is an unknown parameter
 * and refuses 403 — a gated party may not ask for its own auto release.
 */
async function programControllerResponse(request, env, session, dependencies) {
  if (!workspaceCommandCenterEnabled(env)) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  const allowed = new Set(["program_ref", "slice_ref", "release_ref"]);
  if ([...url.searchParams.keys()].some((key) => !allowed.has(key))) {
    return json({ error: "AUTHORIZATION_REFUSED" }, 403);
  }
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: { allow: "GET, HEAD, OPTIONS", "cache-control": "no-store" } });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response(JSON.stringify({ error: "METHOD_NOT_ALLOWED" }), {
      status: 405, headers: { ...JSON_HEADERS, allow: "GET, HEAD, OPTIONS" },
    });
  }
  const reader = typeof dependencies.programControllerReader === "function"
    ? dependencies.programControllerReader : defaultProgramControllerReader;
  try {
    const payload = await reader(env, session.actor, env.CORRELATION_ID, {
      program_ref: url.searchParams.get("program_ref"),
      slice_ref: url.searchParams.get("slice_ref"),
      release_ref: url.searchParams.get("release_ref"),
    });
    if (request.method === "HEAD") return new Response(null, { status: 200, headers: JSON_HEADERS });
    return json(payload);
  } catch (error) {
    const code = ["AUTHORIZATION_REFUSED", "TENANT_SCOPE_REFUSED", "FRESHNESS_UNKNOWN", "DEPENDENCY_UNAVAILABLE", "INTERNAL_ERROR"].includes(error?.code) ? error.code : "INTERNAL_ERROR";
    const status = code === "AUTHORIZATION_REFUSED" ? 403 : code === "TENANT_SCOPE_REFUSED" ? 404 : code === "FRESHNESS_UNKNOWN" ? 409 : code === "DEPENDENCY_UNAVAILABLE" ? 503 : 500;
    return json({ error: code }, status);
  }
}

/**
 * GET /api/v1/business/{clients,vendors}[/<uuid>] — the Journey 1 business read.
 *
 * The route holds no query knowledge of its own: the read model parses,
 * bounds and refuses the query so the list, the total and the filter semantics
 * keep exactly ONE definition. The actor comes from the verified session and is
 * never taken from the URL.
 */
async function businessResponse(request, env, session, dependencies) {
  if (!workspaceCommandCenterEnabled(env)) return json({ error: "not_found" }, 404);
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: { allow: "GET, HEAD, OPTIONS", "cache-control": "no-store" } });
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response(JSON.stringify({ error: "METHOD_NOT_ALLOWED" }), {
      status: 405, headers: { ...JSON_HEADERS, allow: "GET, HEAD, OPTIONS" },
    });
  }
  if (typeof dependencies.businessReader !== "function") return json({ error: "DEPENDENCY_UNAVAILABLE" }, 503);
  try {
    const payload = await dependencies.businessReader(env, session.actor, request, env.CORRELATION_ID);
    if (request.method === "HEAD") return new Response(null, { status: 200, headers: JSON_HEADERS });
    return json(payload);
  } catch (error) {
    const code = Object.prototype.hasOwnProperty.call(BUSINESS_ERROR_STATUS, error?.code) ? error.code : "INTERNAL_ERROR";
    // Only a malformed query says which parameter was wrong, and only an
    // unprovisioned read says which class of access is missing. Neither says
    // anything about the record, the tenant, the schema or the statement.
    const body = { error: code };
    if (code === "QUERY_INVALID" && error?.detail) body.detail = error.detail;
    if (code === "DEPENDENCY_NOT_PROVISIONED" && BUSINESS_DEPENDENCY_CLASSES.has(error?.detail?.dependency)) {
      body.dependency = error.detail.dependency;
    }
    return json(body, BUSINESS_ERROR_STATUS[code]);
  }
}

async function bundleAsset(env, request) {
  const url = new URL(request.url);
  const requested = url.pathname === "/" ? "/index.html" : (DEALROOM_ROUTE_ASSETS.get(url.pathname) || url.pathname);
  for (const pathname of [requested, `/dist${requested}`]) {
    const response = await asset(env, request, pathname);
    if (response) return withHeaders(response, { "cache-control": "no-cache" });
  }
  if (request.headers.get("accept")?.includes("text/html") || url.pathname === "/") {
    for (const pathname of ["/index.html", "/dist/index.html", "/public-shell/index.html"]) {
      const response = await asset(env, request, pathname);
      if (response) return withHeaders(response, { "content-type": "text/html; charset=utf-8", "cache-control": "no-cache" });
    }
  }
  return json({ error: "not_found" }, 404);
}

/** Injectable dependencies keep the gate testable without Google or a database. */
export function createDealroomHandler(overrides = {}) {
  const dependencies = {
    exchangeGoogleCodeFn: exchangeGoogleCode,
    verifyGoogleIdTokenFn: verifyGoogleIdToken,
    slugForEmailFn: slugForEmail,
    propsForSlugFn: propsForSlug,
    // The Deal Room session surface builds an actor from grant props and needs
    // no receipt identity, so it presents no server witness and the actor it
    // gets back is deliberately unbranded (identity.js, amendment 8).
    actorFromPropsFn: props => authenticatedIdentity.connectionForGrant(props),
    // The business read ships with its own production adapter so the Clients
    // and Vendors views work wherever this handler is mounted; a test replaces
    // the whole reader rather than reaching past it to a database.
    businessReader: createWorkspaceBusinessReader(),
    now: () => Date.now(),
    ...overrides,
  };

  return {
    async fetch(request, env, ctx) {
      const response = await handleRequest(request, env, ctx, dependencies);
      return withSecurityHeaders(response);
    },
  };
}

async function handleRequest(request, env, ctx, dependencies) {
      const primaryOrigin = dealroomOrigin(env);
      const legacyOrigin = legacyDealroomOrigin(env);
      if (legacyOrigin && requestMatchesDealroomOrigin(request, legacyOrigin)) {
        const legacyUrl = new URL(request.url);
        // The old hostname is a browser bookmark bridge only.  Do not carry
        // query input (including stale return_to values) across origins, and
        // never let API, machine, asset, or mutation requests become HTML
        // redirects by accident.
        if ((request.method === "GET" || request.method === "HEAD") &&
            LEGACY_BROWSER_REDIRECT_PATHS.has(legacyUrl.pathname) && primaryOrigin) {
          return redirect(`${primaryOrigin}/deals`);
        }
        return json({ error: "not_found" }, 404);
      }
      const origin = dealroomOriginForRequest(request, env);
      if (!origin) return json({ error: "not_found" }, 404);
      env = envForDealroomOrigin(env, origin);
      const url = new URL(request.url);
      const publicResponse = await publicShellAsset(env, request, url.pathname);
      if (publicResponse) return publicResponse;
      if (url.pathname === "/auth/login" && request.method === "GET") return startLogin(request, env);
      if (url.pathname === "/auth/callback" && request.method === "GET") return completeLogin(request, env, dependencies);
      if (url.pathname === "/auth/reauth" && request.method === "GET") return startReauth(request, env, dependencies);
      // The first prototype's workspace API was intentionally retired. Keep
      // it a quiet 404 so stale browser bundles cannot reach a second read.
      // The business read is the ONLY addition to that surface, and it is
      // admitted by an exact path parser rather than a prefix.
      if (url.pathname.startsWith("/api/v1/") && url.pathname !== COMMAND_CENTER_PATH &&
          url.pathname !== USAGE_PATH && url.pathname !== `${USAGE_PATH}/session` &&
          url.pathname !== JEV_DEAL_READING_PATH &&
          url.pathname !== WORK_INVENTORY_PATH && url.pathname !== ATLAS_GRAPH_PATH &&
          url.pathname !== PROGRAM_CONTROLLER_PATH && url.pathname !== METERING_PATH &&
          !isBusinessApiPath(url.pathname)) return json({ error: "not_found" }, 404);
      // Home, Clients and Vendors are one workspace surface and share its flag.
      if ((url.pathname === COMMAND_CENTER_PATH || url.pathname === WORK_INVENTORY_PATH ||
           url.pathname === ATLAS_GRAPH_PATH || url.pathname === PROGRAM_CONTROLLER_PATH ||
           url.pathname === METERING_PATH ||
           isBusinessApiPath(url.pathname) ||
           BUSINESS_VIEW_PATHS.has(url.pathname) || url.pathname === BUSINESS_ASSET_PATH ||
           APP_DOCUMENT_PATHS.has(url.pathname)) &&
          !workspaceCommandCenterEnabled(env)) return json({ error: "not_found" }, 404);
      if (url.pathname === "/workspace" || url.pathname === "/workspace.html") return redirect(`${origin}/`);
      // /clients and /vendors are the addresses; the asset file is not a second one.
      if (url.pathname === BUSINESS_ASSET_PATH) return redirect(`${origin}${CLIENTS_ROUTE}`);

      if (url.pathname === "/auth/signout") {
        if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
        const signOutSession = await sessionFor(request, env, dependencies);
        if (!signOutSession) return json({ error: "unauthorized", state: "sign_in_required" }, 401);
        if (!sameOrigin(request, env) || request.headers.get("sec-fetch-site") !== "same-origin" ||
            !equalStrings(request.headers.get("x-carr-csrf"), signOutSession.csrfToken)) {
          return json({ error: "forbidden", reason: "csrf_mismatch" }, 403);
        }
        return attachRefresh(await signOut(request, env, origin), signOutSession.refreshCookie);
      }

      const session = await sessionFor(request, env, dependencies);
      if (!session) {
        if (url.pathname.startsWith(USAGE_PATH) || url.pathname === JEV_DEAL_READING_PATH || url.pathname === COMMAND_CENTER_PATH || url.pathname === WORK_INVENTORY_PATH ||
            url.pathname === ATLAS_GRAPH_PATH || url.pathname === PROGRAM_CONTROLLER_PATH ||
            url.pathname === METERING_PATH ||
            isBusinessApiPath(url.pathname)) {
          return json({ error: "AUTHENTICATION_REQUIRED" }, 401);
        }
        if (url.pathname === "/mcp" || url.pathname === "/pipeline/changes" ||
            url.pathname.startsWith(SYSTEM_WORK_PREFIX) || url.pathname.startsWith(ROOM_PREFIX) ||
            url.pathname.startsWith("/api/tours/")) {
          return json({ error: "unauthorized", state: "sign_in_required" }, 401);
        }
        return redirect(`${origin}/auth/login?return_to=${encodeURIComponent(url.pathname + url.search)}`);
      }

      let response;
      if (url.pathname === USAGE_PATH || url.pathname === `${USAGE_PATH}/session`) {
        response = await usageResponse(request, env, session, dependencies, guardSystemWorkPost);
      } else if (isTourInternalRequest(request) && dependencies.tourHandler?.fetch) {
        response = await dependencies.tourHandler.fetch(request, env, ctx, session.actor, session);
      } else if (url.pathname.startsWith(ROOM_PREFIX)) {
        response = await roomRequest(request, env, session, dependencies);
      } else if (url.pathname.startsWith(SYSTEM_WORK_PREFIX)) {
        if (!program6ActionsEnabled(env)) return json({ error: "program6_actions_disabled" }, 404);
        if (url.pathname === `${SYSTEM_WORK_PREFIX}/session` && request.method === "GET") response = await systemWorkSession(session, dependencies);
        else if (url.pathname === `${SYSTEM_WORK_PREFIX}/challenge` && request.method === "POST") response = await createActionChallenge(request, env, session, dependencies);
        else if (typeof dependencies.program6Handler === "function") {
          // The injected controller receives a server-derived actor and opaque
          // session metadata.  It never receives a caller-selected verb or
          // tenant and must use authorizeProgram6Action for its typed posts.
          const prepared = await prepareSystemWorkControllerRequest(request, env, session);
          response = prepared.error || await dependencies.program6Handler(prepared.request, env, ctx, session.actor, session);
        } else response = json({ error: "not_found" }, 404);
      } else if (url.pathname === "/mcp") {
        // SameSite limits cross-site cookies, but sibling subdomains are still
        // the same site. Origin equality closes that remaining CSRF door for
        // every verb POST authenticated by the browser cookie.
        if (!sameOrigin(request, env)) {
          return json({ error: "forbidden", reason: "origin_mismatch" }, 403);
        }
        response = await dependencies.mcpHandler(request, env, ctx, session.actor);
      }
      else if (url.pathname === "/pipeline/changes") response = await dependencies.pipelineHandler(request, env, ctx, session.actor);
      else if (url.pathname === JEV_DEAL_READING_PATH) {
        response = await jevDealReadingResponse(request, env, session, dependencies);
      } else if (url.pathname === COMMAND_CENTER_PATH) {
        response = await commandCenterResponse(request, env, session, dependencies);
      } else if (url.pathname === WORK_INVENTORY_PATH) {
        response = await workInventoryResponse(request, env, session, dependencies);
      } else if (url.pathname === ATLAS_GRAPH_PATH) {
        response = await atlasGraphResponse(request, env, session, dependencies);
      } else if (url.pathname === PROGRAM_CONTROLLER_PATH) {
        response = await programControllerResponse(request, env, session, dependencies);
      } else if (url.pathname === METERING_PATH) {
        response = await meteringResponse(request, env, session, dependencies);
      } else if (isBusinessApiPath(url.pathname)) {
        response = await businessResponse(request, env, session, dependencies);
      } else if (url.pathname.startsWith("/api/v1/")) {
        response = json({ error: "not_found" }, 404);
      } else if (url.pathname === "/" && workspaceCommandCenterEnabled(env)) {
        const originalPath = url.pathname;
        url.pathname = "/workspace.html";
        response = await bundleAsset(env, new Request(url, request));
        url.pathname = originalPath;
      } else response = await bundleAsset(env, request);
      return attachRefresh(response, session.refreshCookie);
}

export function isDealroomRequest(request, env) {
  const legacyOrigin = legacyDealroomOrigin(env);
  if (requestMatchesDealroomOrigin(request, legacyOrigin)) return true;
  if (!dealroomOriginForRequest(request, env)) return false;
  const pathname = new URL(request.url).pathname;
  if (pathname === SYSTEM_WORK_PREFIX || DEALROOM_EXACT_PATHS.has(pathname) ||
      DEALROOM_PATH_PREFIXES.some((prefix) => pathname.startsWith(prefix))) return true;

  // The staging workers.dev origin also carries the machine/OAuth API. Only an
  // opaque Deal Room session cookie selects the browser adapter for these two
  // shared paths; any Authorization header remains owned by the machine doors
  // or OAuthProvider, even if a browser cookie is present too.
  if (pathname === "/mcp" || pathname === "/pipeline/changes") {
    return !request.headers.get("authorization") && Boolean(cookieValue(request, SESSION_COOKIE));
  }
  return false;
}

export function isLegacyDealroomRequest(request, env) {
  return requestMatchesDealroomOrigin(request, legacyDealroomOrigin(env));
}
