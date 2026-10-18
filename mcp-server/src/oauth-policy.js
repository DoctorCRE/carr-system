// Shared authorization policy around the pinned provider. No OAuth cryptography
// is duplicated here; the provider remains responsible for codes and tokens.
export function validateAuthorizationRequest(request, auth) {
  const params = new URL(request.url).searchParams;
  for (const name of ["response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method", "resource"])
    if (params.getAll(name).length > 1) throw new Error(`Duplicate ${name}`);
  if (auth.responseType !== "code" || !auth.clientId || !auth.redirectUri)
    throw new Error("A registered client, redirect URI and code response are required");
  // SHA-256 is exactly 32 bytes, encoded without padding (43 characters).
  if (auth.codeChallengeMethod !== "S256" || !/^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/.test(auth.codeChallenge || ""))
    throw new Error("A well-formed S256 code_challenge is required");
  auth.resource = exactResource(auth.resource ?? `${new URL(request.url).origin}/mcp`, request);
}

export const SHARED_OAUTH_PATHS = ["/mcp", "/doc/mcp", "/pipeline/changes"];
export const isSharedOAuthPath = path => SHARED_OAUTH_PATHS.some(route => path === route || path.startsWith(route + "/"));
const MCP_ORIGINS = ["https://chatgpt.com", "https://chat.openai.com", "https://claude.ai",
  "https://api.doctorcre.com", "https://app.doctorcre.com", "https://dealroom.doctorcre.com"];

// Absence is permitted for native/server-to-server transports. Supplied
// origins must be canonical exact origins; never reflect an arbitrary caller.
export function mcpOriginRefusal(request, env = {}) {
  const origin = request.headers.get("origin");
  if (origin === null) return null;
  let extra = [];
  try {
    if (env.CARR_MCP_ALLOWED_ORIGINS !== undefined) extra = JSON.parse(env.CARR_MCP_ALLOWED_ORIGINS);
    const canonical = value => typeof value === "string" && new URL(value).origin === value && new URL(value).protocol === "https:";
    if (!Array.isArray(extra) || !extra.every(canonical) || !canonical(origin) || ![...MCP_ORIGINS, ...extra].includes(origin)) throw new Error();
  } catch { return Response.json({ error: "origin_not_allowed" }, { status: 403, headers: { "cache-control": "no-store" } }); }
  return null;
}

export function requireApprovedClient(clientId, env) {
  if (env.CARR_OAUTH_APPROVED_CLIENT_IDS === undefined) return;
  let ids;
  try { ids = JSON.parse(env.CARR_OAUTH_APPROVED_CLIENT_IDS); } catch { throw new Error("Client approval policy is invalid"); }
  if (!Array.isArray(ids) || ids.some(id => typeof id !== "string" || !id) || !ids.includes(clientId))
    throw new Error("This client is not approved by the server");
}

function exactResource(value, request) {
  const origin = new URL(request.url).origin;
  if (typeof value !== "string" || !SHARED_OAUTH_PATHS.some(path => value === origin + path))
    throw new Error("One exact supported resource is required");
  return value;
}

function legacyResource(value, request) {
  const origin = new URL(request.url).origin;
  return value == null || value === origin || value === `${origin}/`;
}

function oauthError(request, status, error) {
  const url = new URL(request.url);
  return Response.json({ error }, { status, headers: {
    "cache-control": "no-store",
    ...(status === 401 ? { "www-authenticate": `Bearer resource_metadata="${url.origin}/.well-known/oauth-protected-resource${url.pathname}", error="invalid_token"` } : {}),
  } });
}

// Interpose policy, not token cryptography. The provider validates client
// authentication, code/verifier and refresh-token hashes before it writes.
export async function sharedOAuthFetch(provider, request, env, ctx) {
  const path = new URL(request.url).pathname;
  const corsRoute = isSharedOAuthPath(path) || path === "/token";
  if (corsRoute) {
    const refused = mcpOriginRefusal(request, env);
    if (refused) return refused;
  }
  const response = await policyFetch(provider, request, env, ctx);
  const origin = request.headers.get("origin");
  if (!corsRoute || !origin) return response;
  const headers = new Headers(response.headers);
  headers.set("access-control-allow-origin", origin);
  headers.append("vary", "Origin");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function policyFetch(provider, request, env, ctx) {
  const url = new URL(request.url);
  if (isSharedOAuthPath(url.pathname)) {
    const refused = mcpOriginRefusal(request, env);
    if (refused) return refused;
    const bearer = request.headers.get("authorization")?.match(/^Bearer (.+)$/i)?.[1];
    if (bearer) {
      // Null means invalid/expired; operational errors reach the existing
      // server-failure boundary and its failure recorder.
      const token = await env.OAUTH_PROVIDER.unwrapToken(bearer);
      if (!token) return oauthError(request, 401, "invalid_token");
      const audience = token.audience;
      const permitted = legacyResource(audience, request)
        ? url.pathname === "/mcp"
        : audience === url.origin + url.pathname;
      if (!permitted) return oauthError(request, 401, "invalid_token");
    }
  }
  if (url.pathname === "/token" && request.method === "POST") {
    let body;
    try {
      const type = (request.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
      if (type === "application/json") {
        const data = await request.clone().json();
        if (Object.values(data).some(v => typeof v !== "string")) throw new Error();
        body = new URLSearchParams(data);
      } else if (type === "application/x-www-form-urlencoded") {
        body = new URLSearchParams(await request.clone().text());
      } else throw new Error();
      for (const key of [...body.keys()]) if (body.getAll(key).length !== 1) throw new Error();
    } catch { return oauthError(request, 400, "invalid_request"); }
    const kind = body.get("grant_type");
    // RFC 7009 shares the provider's token endpoint. Authentication and
    // revocation (including unknown-token handling) remain provider-owned.
    if (kind === null && body.has("token")) return provider.fetch(request, env, ctx);
    if (!["authorization_code", "refresh_token"].includes(kind))
      return oauthError(request, 400, "unsupported_grant_type");
    if (kind === "authorization_code" && !/^[A-Za-z0-9._~-]{43,128}$/.test(body.get("code_verifier") || ""))
      return oauthError(request, 400, "invalid_request");
    const opaque = body.get(kind === "authorization_code" ? "code" : "refresh_token") || "";
    const parts = opaque.split(":");
    if (parts.length !== 3) return oauthError(request, 400, "invalid_grant");
    const key = `grant:${parts[0]}:${parts[1]}`;
    const grant = await env.OAUTH_KV.get(key, { type: "json" });
    if (!grant) return oauthError(request, 400, "invalid_grant");
    let resource;
    try {
      resource = legacyResource(grant.resource, request) ? `${url.origin}/mcp` : exactResource(grant.resource, request);
      const requested = body.get("resource");
      if (requested !== null && requested !== resource &&
          !(legacyResource(grant.resource, request) && legacyResource(requested, request))) throw new Error();
    } catch { return oauthError(request, 400, "invalid_target"); }
    body.set("resource", resource);
    // Request-local projection of a legacy grant. Only the provider's
    // authenticated successful exchange persists this migration, with its
    // original TTL. Invalid input never rotates or rewrites a grant.
    const kv = env.OAUTH_KV;
    const projectedKV = {
      get: async (name, options) => {
        const value = await kv.get(name, options);
        if (name !== key || !value) return value;
        const json = options === "json" || options?.type === "json";
        const current = json ? value : JSON.parse(value);
        return json ? { ...current, resource } : JSON.stringify({ ...current, resource });
      },
      put: (...args) => kv.put(...args), delete: (...args) => kv.delete(...args), list: (...args) => kv.list(...args),
    };
    const headers = new Headers(request.headers);
    headers.set("content-type", "application/x-www-form-urlencoded");
    headers.delete("content-length");
    request = new Request(request, { headers, body: body.toString() });
    env = { ...env, OAUTH_KV: projectedKV };
  }
  return provider.fetch(request, env, ctx);
}
