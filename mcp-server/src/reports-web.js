// Browser adapter for the deliberately small public tour-report share surface.
//
// A share bearer is accepted once, from the fragment bootstrap, and is exchanged
// for an opaque host-only cookie.  This module intentionally has no map, GIS,
// service-worker, CORS, or database implementation: those concerns belong to
// its injected record-layer seams. MapLibre is self-hosted and consumes only
// the separately scoped, entrance-verified public map projection.

export const REPORTS_ORIGIN = "https://reports.doctorcre.com";
export const REPORTS_ASSET_DIRECTORY = "../dealroom/reports";

const SESSION_COOKIE = "__Host-tour_share_session";
const MAX_BODY_BYTES = 16 * 1024;
const SESSION_TTL_MS = 60 * 60 * 1000;
const STATIC_ASSETS = new Map([
  ["/share", "/reports/share.html"],
  ["/share-bootstrap.js", "/reports/share-bootstrap.js"],
  ["/share.js", "/reports/share.js"],
  ["/share.css", "/reports/share.css"],
  ["/vendor/maplibre-gl-6.4.1/maplibre-gl.mjs", "/reports/vendor/maplibre-gl-6.4.1/maplibre-gl.mjs"],
  ["/vendor/maplibre-gl-6.4.1/maplibre-gl-shared.mjs", "/reports/vendor/maplibre-gl-6.4.1/maplibre-gl-shared.mjs"],
  ["/vendor/maplibre-gl-6.4.1/maplibre-gl-worker.mjs", "/reports/vendor/maplibre-gl-6.4.1/maplibre-gl-worker.mjs"],
  ["/vendor/maplibre-gl-6.4.1/maplibre-gl.css", "/reports/vendor/maplibre-gl-6.4.1/maplibre-gl.css"],
]);
const API_METHODS = new Map([
  ["/api/share/exchange", "POST"],
  ["/api/share/report", "GET"],
  ["/api/share/map", "GET"],
  ["/api/share/feedback", "GET"],
  ["/api/share/shortlist", "POST"],
  ["/api/share/comment", "POST"],
]);
const REPORTS_CSP = [
  "default-src 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'self'",
  "child-src 'none'",
  "manifest-src 'none'",
].join("; ");

function json(body, status = 200, additions = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...additions },
  });
}

function reportsRequest(request) {
  try { return new URL(request.url).origin === REPORTS_ORIGIN; }
  catch { return false; }
}

function cookieValue(request, name) {
  const header = request.headers.get("cookie") || "";
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index >= 0 && part.slice(0, index).trim() === name) return part.slice(index + 1).trim();
  }
  return null;
}

function sessionCookie(session) {
  return `${SESSION_COOKIE}=${session}; Path=/; Secure; HttpOnly; SameSite=Lax`;
}

async function sha256Digest(value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value)
    : value instanceof ArrayBuffer ? new Uint8Array(value)
      : ArrayBuffer.isView(value) ? new Uint8Array(value.buffer, value.byteOffset, value.byteLength) : null;
  if (!bytes) throw new TypeError("SHA-256 input must be text or bytes");
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return `sha256:${[...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function newSessionToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function sameOriginPost(request) {
  return request.headers.get("origin") === REPORTS_ORIGIN && request.headers.get("sec-fetch-site") === "same-origin";
}

function isJsonContentType(value) {
  return /^application\/json(?:\s*;|$)/i.test(value || "");
}

const SHARE_BEARER = /^[A-Za-z0-9_-]{43}$/;
const PUBLIC_REF = /^(?:property|projection):public:[A-Za-z0-9_-]{16,128}$/;
const REQUEST_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function isPlainRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
}

async function jsonBody(request) {
  if (!isJsonContentType(request.headers.get("content-type"))) return { error: json({ error: "unsupported_media_type" }, 415) };
  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && (declaredLength < 0 || declaredLength > MAX_BODY_BYTES)) return { error: json({ error: "payload_too_large" }, 413) };
  const raw = await request.clone().text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) return { error: json({ error: "payload_too_large" }, 413) };
  try {
    const value = JSON.parse(raw);
    return isPlainRecord(value) ? { value } : { error: json({ error: "invalid_request" }, 400) };
  } catch {
    return { error: json({ error: "invalid_json" }, 400) };
  }
}

function methodNotAllowed(allow) {
  return json({ error: "method_not_allowed" }, 405, { allow });
}

function dependencyFailure(result) {
  const status = result?.status === 401 || result?.status === 403 || result?.status === 404 ? result.status : 503;
  return json({ error: status === 401 ? "unauthorized" : status === 403 ? "forbidden" : status === 404 ? "not_found" : "share_unavailable" }, status);
}

function publicResponse(result) {
  // Dependencies return the already-redacted projection only in `data`; the
  // session and the original share bearer are never a response shape here.
  return json({ data: result.data });
}

async function staticAsset(env, request, pathname) {
  if (!env?.ASSETS?.fetch) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  url.pathname = pathname;
  url.search = "";
  const response = await env.ASSETS.fetch(new Request(url, { method: "GET" }));
  if (response.status === 404) return json({ error: "not_found" }, 404);
  const headers = new Headers(response.headers);
  headers.set("cache-control", "no-store");
  if (pathname.endsWith(".html")) headers.set("content-type", "text/html; charset=utf-8");
  if (pathname.endsWith(".js")) headers.set("content-type", "application/javascript; charset=utf-8");
  if (pathname.endsWith(".mjs")) headers.set("content-type", "application/javascript; charset=utf-8");
  if (pathname.endsWith(".css")) headers.set("content-type", "text/css; charset=utf-8");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function exchange(request, env, ctx, dependencies) {
  if (!sameOriginPost(request)) return json({ error: "forbidden" }, 403);
  const body = await jsonBody(request);
  if (body.error) return body.error;
  if (Object.keys(body.value).length !== 1 || typeof body.value.token !== "string" || !SHARE_BEARER.test(body.value.token)) {
    return json({ error: "invalid_share" }, 400);
  }
  if (typeof dependencies.exchangeShareTokenFn !== "function") return json({ error: "not_found" }, 404);
  const session = newSessionToken();
  const tokenDigest = await sha256Digest(body.value.token);
  const sessionDigest = await sha256Digest(session);
  const now = Number(dependencies.now());
  const sessionExpiresAt = new Date((Number.isFinite(now) ? now : Date.now()) + SESSION_TTL_MS).toISOString();
  const auditDigest = await sha256Digest(`tour-share-exchange\n${tokenDigest}\n${sessionDigest}\n${sessionExpiresAt}`);
  let result;
  try {
    result = await dependencies.exchangeShareTokenFn({ env, ...(ctx ? { ctx } : {}), tokenDigest, sessionDigest, sessionExpiresAt, auditDigest });
  } catch { return json({ error: "share_unavailable" }, 503); }
  if (!result?.ok) return dependencyFailure(result);
  // Deliberately acknowledge only the exchange.  Never return or log the
  // bearer, session, grant, or the dependency's internal result.
  return json({ ok: true }, 200, { "set-cookie": sessionCookie(session) });
}

async function read(request, env, ctx, dependencies, dependencyName) {
  const session = cookieValue(request, SESSION_COOKIE);
  if (!session) return json({ error: "unauthorized" }, 401);
  if (typeof dependencies[dependencyName] !== "function") return json({ error: "not_found" }, 404);
  let result;
  try { result = await dependencies[dependencyName]({ env, ...(ctx ? { ctx } : {}), sessionDigest: await sha256Digest(session) }); }
  catch { return json({ error: "share_unavailable" }, 503); }
  if (!result?.ok || !isPlainRecord(result.data)) return dependencyFailure(result);
  return publicResponse(result);
}

async function writeFeedback(request, env, ctx, dependencies, pathname) {
  if (!sameOriginPost(request)) return json({ error: "forbidden" }, 403);
  const session = cookieValue(request, SESSION_COOKIE);
  if (!session) return json({ error: "unauthorized" }, 401);
  const parsed = await jsonBody(request);
  if (parsed.error) return parsed.error;
  const value = parsed.value;
  const kind = pathname === "/api/share/shortlist" ? "shortlist" : "comment";
  const expected = kind === "shortlist" ? ["projection_ref", "property_ref", "shortlisted", "idempotency_key"] : ["projection_ref", "property_ref", "comment", "idempotency_key"];
  if (Object.keys(value).sort().join(",") !== expected.sort().join(",") ||
      !PUBLIC_REF.test(value.projection_ref) || !value.projection_ref.startsWith("projection:public:") ||
      !PUBLIC_REF.test(value.property_ref) || !value.property_ref.startsWith("property:public:") ||
      !REQUEST_ID.test(value.idempotency_key) ||
      (kind === "shortlist" ? typeof value.shortlisted !== "boolean" :
        typeof value.comment !== "string" || !value.comment.trim() || value.comment.length > 1000 || /[\u0000-\u001f\u007f]/.test(value.comment)))
    return json({ error: "invalid_request" }, 400);
  const dependencyName = kind === "shortlist" ? "shortlistFn" : "commentFn";
  if (typeof dependencies[dependencyName] !== "function") return json({ error: "not_found" }, 404);
  let result;
  try {
    result = await dependencies[dependencyName]({ env, ...(ctx ? { ctx } : {}), sessionDigest: await sha256Digest(session), ...value });
  } catch { return json({ error: "share_unavailable" }, 503); }
  if (!result?.ok || !isPlainRecord(result.data)) return dependencyFailure(result);
  return publicResponse(result);
}

function withSecurityHeaders(response) {
  const headers = new Headers(response.headers);
  // An injected asset implementation must not accidentally widen this public
  // surface. No CORS is emitted, including on errors and method refusals.
  for (const name of [...headers.keys()]) if (name.startsWith("access-control-")) headers.delete(name);
  headers.set("cache-control", "no-store");
  headers.set("content-security-policy", REPORTS_CSP);
  headers.set("strict-transport-security", "max-age=31536000; includeSubDomains");
  headers.set("x-content-type-options", "nosniff");
  headers.set("x-frame-options", "DENY");
  headers.set("referrer-policy", "no-referrer");
  headers.set("permissions-policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()");
  headers.set("cross-origin-opener-policy", "same-origin");
  headers.set("cross-origin-resource-policy", "same-origin");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function handleRequest(request, env, ctx, dependencies) {
  if (!reportsRequest(request)) return json({ error: "not_found" }, 404);
  const pathname = new URL(request.url).pathname;
  const asset = STATIC_ASSETS.get(pathname);
  if (asset) return request.method === "GET" ? staticAsset(env, request, asset) : methodNotAllowed("GET");
  const method = API_METHODS.get(pathname);
  if (!method) return json({ error: "not_found" }, 404);
  if (request.method !== method) return methodNotAllowed(method);
  if (pathname === "/api/share/exchange") return exchange(request, env, ctx, dependencies);
  if (pathname === "/api/share/shortlist" || pathname === "/api/share/comment") return writeFeedback(request, env, ctx, dependencies, pathname);
  return read(request, env, ctx, dependencies, pathname === "/api/share/map" ? "readMapFn" : pathname === "/api/share/feedback" ? "readFeedbackFn" : "readShareFn");
}

/** Injectable seams keep this browser gate independent from Worker, DB, and MCP wiring. */
export function createReportsWebHandler(overrides = {}) {
  const dependencies = { now: () => Date.now(), ...overrides };
  return { fetch: async (request, env, ctx) => withSecurityHeaders(await handleRequest(request, env, ctx, dependencies)) };
}

export function isReportsRequest(request) {
  if (!reportsRequest(request)) return false;
  const pathname = new URL(request.url).pathname;
  return STATIC_ASSETS.has(pathname) || API_METHODS.has(pathname);
}

export function isReportsHostRequest(request) {
  return reportsRequest(request);
}
