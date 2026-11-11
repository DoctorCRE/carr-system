// Map client portal HTTP requests to slug-bound session operations.

import { Pool } from "@neondatabase/serverless";
import { portalProposalStorage } from "./portal-proposals.ts";
import type { Db } from "./verb-contract.ts";

type Json = Record<string, unknown>;
type Scope = { slug: string; digest: string; signal: AbortSignal };
type Operation = (
  args: Scope & { password?: string; publication_id?: string; publication_version?: number; request?: unknown },
) => Promise<unknown>;
type Options = {
  now?: () => number;
  safeProjection?: typeof safePortalProjection;
  signal?: (event: { code: string }) => void;
  origin?: string;
  deadlineMs?: number;
  maxConcurrent?: number;
  maxRequests?: number;
  loginLimit?: number;
  actionLimit?: number;
  maxRateBuckets?: number;
  exchange: Operation;
  read: Operation;
  revoke: Operation;
  proposalRead: Operation;
  proposalSave: Operation;
};
export const PORTAL_ORIGIN = "https://share.doctorcre.com";
const TTL_SECONDS = 604800;
const SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
const TOKEN = /^[a-f0-9]{64}$/;
const encoder = new TextEncoder();
const cookieName = (slug: string) => `__Host-portal-${slug}`;
const cookie = (slug: string, token: string, age: number) =>
  `${cookieName(slug)}=${token}; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=${age}`;
const escapeHtml = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char] ?? char,
  );
const record = (value: unknown): value is Json => value !== null && typeof value === "object" && !Array.isArray(value);

/** Select only the canonical client-portal.v1 fields, never internal row fields. */
export function safePortalProjection(portal: unknown, slug: string) {
  if (
    !record(portal) ||
    portal.slug !== slug ||
    !record(portal.roadmap) ||
    !Array.isArray(portal.roadmap.phases) ||
    !Array.isArray(portal.materials) ||
    portal.materials.length > 100
  )
    throw new TypeError("invalid_projection");
  const phases = portal.roadmap.phases.map((phase) => {
    if (!record(phase) || typeof phase.slug !== "string" || typeof phase.label !== "string")
      throw new TypeError("invalid_phase");
    return { slug: phase.slug, label: phase.label };
  });
  if (portal.roadmap.current_phase != null && typeof portal.roadmap.current_phase !== "string")
    throw new TypeError("invalid_phase");
  const materials = portal.materials.map((material) => {
    if (
      !record(material) ||
      typeof material.kind !== "string" ||
      typeof material.title !== "string" ||
      !("content" in material)
    )
      throw new TypeError("invalid_material");
    return { kind: material.kind, title: material.title, content: material.content };
  });
  if (!Array.isArray(portal.publications) || portal.publications.length > 100)
    throw new TypeError("invalid_publications");
  const publications = portal.publications.map((item) => {
    if (!record(item) || typeof item.id !== "string" || typeof item.kind !== "string" || typeof item.title !== "string")
      throw new TypeError("invalid_publication");
    const result: Json = { id: item.id, kind: item.kind, title: item.title };
    if (typeof item.content === "string") result.content = item.content;
    if (item.deliverable !== undefined) {
      const d = item.deliverable;
      if (
        !record(d) ||
        typeof d.id !== "string" ||
        typeof d.filename !== "string" ||
        typeof d.mime !== "string" ||
        typeof d.bytes !== "number"
      )
        throw new TypeError("invalid_deliverable");
      result.deliverable = { id: d.id, filename: d.filename, mime: d.mime, bytes: d.bytes };
    }
    return result;
  });
  return { slug, roadmap: { current_phase: portal.roadmap.current_phase ?? null, phases }, materials, publications };
}

function response(body: unknown, status = 200, headers: Record<string, string> = {}, html = false) {
  return new Response(html ? String(body) : JSON.stringify(body), {
    status,
    headers: {
      "content-type": html ? "text/html; charset=utf-8" : "application/json; charset=utf-8",
      "cache-control": "private, no-store",
      "content-security-policy":
        "default-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'",
      "strict-transport-security": "max-age=31536000; includeSubDomains",
      "x-content-type-options": "nosniff",
      "x-frame-options": "DENY",
      "referrer-policy": "no-referrer",
      "cross-origin-resource-policy": "same-origin",
      ...headers,
    },
  });
}
function page(slug: string, projection: unknown, csrf: string, headers: Record<string, string> = {}) {
  const content = projection
    ? `<meta name="portal-csrf" content="${csrf}"><h1>Your portal</h1><pre>${escapeHtml(JSON.stringify(projection, null, 2))}</pre><form method="post" action="/${slug}/logout"><input type="hidden" name="csrf" value="${csrf}"><button>Log out</button></form>`
    : `<meta name="portal-csrf" content="${csrf}"><h1>Open your portal</h1><form method="post" action="/${slug}/login"><input type="hidden" name="csrf" value="${csrf}"><label>Password <input name="password" type="password" required maxlength="1024" autocomplete="current-password"></label><button>Open portal</button></form>`;
  return response(
    `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Client portal</title><body>${content}</body></html>`,
    200,
    headers,
    true,
  );
}
async function digest(token: string) {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(token)));
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
function newToken() {
  return [...crypto.getRandomValues(new Uint8Array(32))].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
function readCookie(request: Request, slug: string, prefix = "__Host-portal-") {
  const matches = (request.headers.get("cookie") || "")
    .split(";")
    .map((x) => x.trim())
    .filter((x) => x.startsWith(`${prefix}${slug}=`));
  if (matches.length !== 1) return null;
  const token = String(matches[0]).slice((prefix + slug).length + 1);
  return TOKEN.test(token) ? token : null;
}

class HttpError extends Error {
  readonly status: number;
  constructor(status: number, code: string) {
    super(code);
    this.status = status;
  }
}

async function bodyText(request: Request, remaining: number, limit: number) {
  const length = request.headers.get("content-length");
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) throw new HttpError(413, "body_too_large");
  const reader = request.body?.getReader();
  if (!reader) return "";
  let size = 0,
    text = "";
  let timer: ReturnType<typeof setTimeout> | undefined;
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false });
  try {
    return await Promise.race([
      (async () => {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          size += chunk.value.byteLength;
          if (size > limit) throw new HttpError(413, "body_too_large");
          text += decoder.decode(chunk.value, { stream: true });
        }
        return text + decoder.decode();
      })(),
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => {
            void reader.cancel().catch(() => {});
            reject(new HttpError(408, "body_timeout"));
          },
          Math.max(1, remaining),
        );
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    void reader.cancel().catch(() => {});
  }
}

/**
 * Dependencies resolve committed contract results, including denied/locked.
 * exchange({slug,password,digest,signal}), read/revoke({slug,digest,signal}).
 * read MUST use a read-write transaction. No Request, cookie or MCP forwarding.
 * signal receives fixed operational codes only. No credentials are logged.
 */
export function createPortalWebHandler(options: Options) {
  const config = {
    now: Date.now,
    safeProjection: safePortalProjection,
    signal: (event: { code: string }) => console.error("portal_web", event.code),
    deadlineMs: 3000,
    maxConcurrent: 8,
    maxRequests: 128,
    loginLimit: 10,
    actionLimit: 120,
    maxRateBuckets: 1024,
    origin: PORTAL_ORIGIN,
    ...options,
  };
  for (const key of [
    "deadlineMs",
    "maxConcurrent",
    "maxRequests",
    "loginLimit",
    "actionLimit",
    "maxRateBuckets",
  ] as const)
    if (!Number.isSafeInteger(config[key]) || config[key] < 1) throw new TypeError(`invalid_${key}`);
  let active = 0,
    requests = 0;
  const state = () => ({ failures: 0, openUntil: 0, probing: false });
  const breakers = {
    exchange: state(),
    read: state(),
    revoke: state(),
    projection: state(),
    proposalRead: state(),
    proposalSave: state(),
  };
  const rates = new Map<string, { count: number; until: number }>();
  const emit = (code: string) => {
    try {
      config.signal({ code });
    } catch {
      /* Observability cannot grant access. */
    }
  };
  const unavailable = (code: string) => {
    emit(code);
    return new HttpError(503, "portal_unavailable");
  };

  async function call<T>(
    name: keyof typeof breakers,
    remaining: number,
    operation: (signal: AbortSignal) => Promise<T>,
  ) {
    const breaker = breakers[name];
    const now = config.now();
    if (active >= config.maxConcurrent) throw unavailable("dependency_capacity");
    if (breaker.openUntil && (now < breaker.openUntil || breaker.probing)) throw unavailable("circuit_open");
    if (breaker.openUntil) breaker.probing = true;
    active++;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const work = Promise.resolve().then(() => operation(controller.signal));
    work.then(
      () => active--,
      () => active--,
    );
    try {
      const result = await Promise.race([
        work,
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () => {
              controller.abort();
              reject(new Error("deadline"));
            },
            Math.max(1, remaining),
          );
        }),
      ]);
      breaker.failures = 0;
      breaker.openUntil = 0;
      return result;
    } catch (error) {
      if (
        error instanceof Error &&
        "status" in error &&
        typeof error.status === "number" &&
        [400, 401, 409].includes(error.status)
      )
        throw new HttpError(
          error.status,
          error.message === "portal_access_denied"
            ? "unauthenticated"
            : error.message === "version_conflict"
              ? "stale_version"
              : error.message === "property_not_published" || error.message === "publication_unavailable"
                ? "retired"
                : error.message,
        );
      breaker.failures++;
      if (breaker.failures >= 3 || breaker.probing) breaker.openUntil = config.now() + 30000;
      throw unavailable("dependency_failure");
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      breaker.probing = false;
    }
  }

  function rate(slug: string, login: boolean, session: string | null) {
    const now = config.now();
    for (const [key, bucket] of rates) if (bucket.until <= now) rates.delete(key);
    const key = `${slug}:${login ? "login" : (session ?? "anonymous")}`;
    if (!rates.has(key)) {
      if (rates.size >= config.maxRateBuckets) throw unavailable("rate_capacity");
      rates.set(key, { count: 0, until: now + 60000 });
    }
    if (++(rates.get(key) as { count: number }).count > (login ? config.loginLimit : config.actionLimit))
      throw new HttpError(429, "rate_limited");
  }

  async function handle(request: Request) {
    const deadline = performance.now() + config.deadlineMs;
    const remaining = () => {
      const budget = deadline - performance.now();
      if (budget <= 0) throw unavailable("request_deadline");
      return budget;
    };
    const url = new URL(request.url);
    if (url.origin !== config.origin || url.username || url.password) throw new HttpError(404, "not_found");
    if (
      request.method !== "GET" &&
      (request.headers.get("origin") !== config.origin ||
        ["cross-site", "same-site"].includes(request.headers.get("sec-fetch-site") ?? ""))
    )
      throw new HttpError(403, "origin");
    if (url.search || /[%\\]/.test(url.pathname)) throw new HttpError(400, "invalid_path");
    const match =
      /^\/([^/]+)(?:\/(login|status|logout|packages|files|selections|proposals)?(?:\/([0-9a-f-]{36}))?)?$/.exec(
        url.pathname,
      );
    if (!match || !SLUG.test(String(match[1]))) throw new HttpError(404, "not_found");
    const slug = String(match[1]),
      action = match[2] ?? "",
      publication = match[3];
    if (
      (action === "proposals") !== Boolean(publication) ||
      (publication && !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(publication))
    )
      throw new HttpError(404, "not_found");
    const token = readCookie(request, slug);
    rate(slug, action === "login", token);
    const csrf = token
      ? await digest(`portal-csrf:${token}`)
      : (readCookie(request, slug, "__Host-portal-csrf-") ?? newToken());
    if (["packages", "files", "selections"].includes(action)) throw new HttpError(501, "portal_feature_not_available");
    const method =
      action === "login" || action === "logout"
        ? "POST"
        : action === "proposals"
          ? request.method === "POST"
            ? "POST"
            : "GET"
          : "GET";
    if (request.method !== method) return response({ error: "method_not_allowed" }, 405, { allow: method });
    if (url.pathname === `/${slug}`) return response({}, 308, { location: `/${slug}/` });
    let payload: Json = {};
    if (request.method === "POST") {
      const text = await bodyText(request, remaining(), action === "proposals" ? 262144 : 8192);
      if (action === "proposals") {
        if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") ?? ""))
          throw new HttpError(415, "unsupported_media_type");
        try {
          const value: unknown = JSON.parse(text);
          if (!record(value)) throw Error();
          payload = value;
        } catch {
          throw new HttpError(400, "invalid_proposal");
        }
      } else {
        if (!/^application\/x-www-form-urlencoded(?:\s*;|$)/i.test(request.headers.get("content-type") ?? ""))
          throw new HttpError(415, "unsupported_media_type");
        const params = new URLSearchParams(text);
        if (new Set(params.keys()).size !== [...params.keys()].length) throw new HttpError(400, "invalid_form");
        payload = Object.fromEntries(params);
      }
      const provided = action === "proposals" ? request.headers.get("x-portal-csrf") : payload.csrf;
      const expected = action === "login" ? readCookie(request, slug, "__Host-portal-csrf-") : token ? csrf : null;
      if (!expected || provided !== expected) throw new HttpError(403, "csrf");
    }
    if (action === "login") {
      let password = payload.password;
      if (
        Object.keys(payload).sort().join(",") !== "csrf,password" ||
        typeof password !== "string" ||
        !password ||
        [...password].length > 1024 ||
        password.includes("\0")
      )
        throw new HttpError(400, "invalid_password");
      const token = newToken(),
        sessionDigest = await digest(token);
      let result: Json;
      try {
        result = await call("exchange", remaining(), async (signal) => {
          const value = await config.exchange({ slug, password: String(password), digest: sessionDigest, signal });
          if (record(value) && value.status === "denied") return value;
          if (record(value) && value.status === "locked" && Number.isFinite(Date.parse(String(value.retry_at))))
            return value;
          if (record(value) && value.status === "success" && Date.parse(String(value.expires_at)) > config.now())
            return value;
          throw new Error("invalid_exchange_result");
        });
      } finally {
        password = undefined;
      }
      if (result.status === "locked")
        return response(
          {
            error: "portal_locked",
            message: `Too many password attempts. Try again at ${result.retry_at}.`,
            retry_at: result.retry_at,
          },
          429,
          {
            "retry-after": String(Math.max(1, Math.ceil((Date.parse(String(result.retry_at)) - config.now()) / 1000))),
          },
        );
      if (result.status === "denied") return response({ error: "portal_access_denied" }, 401);
      const age = Math.min(TTL_SECONDS, Math.floor((Date.parse(String(result.expires_at)) - config.now()) / 1000));
      if (age < 1) throw unavailable("invalid_expiry");
      return response({}, 303, { location: `/${slug}/`, "set-cookie": cookie(slug, token, age) });
    }
    const clear = { "set-cookie": cookie(slug, "", 0) };
    if (action === "logout") {
      if (Object.keys(payload).join(",") !== "csrf") throw new HttpError(400, "invalid_logout");
      if (token)
        await call("revoke", remaining(), async (signal) => {
          const result = await config.revoke({ slug, digest: await digest(token), signal });
          if (!record(result) || !["success", "denied"].includes(String(result.status)))
            throw new Error("invalid_revoke_result");
        });
      return response({}, 303, { location: `/${slug}/`, ...clear });
    }
    if (!token)
      return action === "status" || action === "proposals"
        ? response({ error: "unauthenticated" }, 401, clear)
        : page(slug, null, csrf, {
            "set-cookie": `__Host-portal-csrf-${slug}=${csrf}; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=900`,
          });
    const result = await call("read", remaining(), async (signal) => {
      const value = await config.read({ slug, digest: await digest(token), signal });
      if (record(value) && value.status === "denied") return value;
      if (
        !record(value) ||
        value.status !== "success" ||
        value.schema_version !== "client-portal.v1" ||
        !record(value.portal) ||
        value.portal.slug !== slug
      )
        throw new Error("invalid_read_result");
      return value;
    });
    if (result.status === "denied") {
      if (action === "status" || action === "proposals") return response({ error: "unauthenticated" }, 401, clear);
      const anonymous = newToken();
      return page(slug, null, anonymous, {
        "set-cookie": `__Host-portal-csrf-${slug}=${anonymous}; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=900`,
      });
    }
    if (action === "status") return response({ authenticated: true, csrf });
    if (action === "proposals") {
      if (
        request.method === "POST" &&
        (Object.keys(payload).sort().join(",") !== "publication_version,request" ||
          !Number.isSafeInteger(payload.publication_version) ||
          Number(payload.publication_version) < 1 ||
          !record(payload.request) ||
          Object.keys(payload.request).sort().join(",") !== "idempotency_key,notes,selected,version")
      )
        throw new HttpError(400, "invalid_proposal");
      const receipt = await call(
        request.method === "POST" ? "proposalSave" : "proposalRead",
        remaining(),
        async (signal) =>
          config[request.method === "POST" ? "proposalSave" : "proposalRead"]({
            slug,
            digest: await digest(token),
            signal,
            publication_id: String(publication),
            publication_version: Number(payload.publication_version),
            request: payload.request,
          }),
      );
      return response(receipt);
    }
    const projection = await call("projection", remaining(), async () => {
      const safe = await config.safeProjection(result.portal, slug);
      const serialized = JSON.stringify(safePortalProjection(safe, slug));
      if (encoder.encode(serialized).byteLength > 1048576) throw new Error("projection_too_large");
      return JSON.parse(serialized);
    });
    return page(slug, projection, csrf);
  }

  return {
    fetch: async (request: Request) => {
      if (requests >= config.maxRequests) {
        emit("request_capacity");
        return response({ error: "portal_unavailable" }, 503);
      }
      requests++;
      try {
        return await handle(request);
      } catch (error) {
        if (error instanceof HttpError) return response({ error: error.message }, error.status);
        emit("request_failure");
        return response({ error: "portal_unavailable" }, 503);
      } finally {
        requests--;
      }
    },
  };
}

/** The writer commits even denied/locked results; errors roll back and never retry. */
export function portalSqlOperations(withWriter: <T>(fn: (db: Db) => Promise<T>) => Promise<T>) {
  const run = (fn: (db: Db) => Promise<unknown>, signal: AbortSignal) =>
    withWriter(async (db) => {
      if (signal.aborted) throw Error("deadline");
      await db.query(
        "set local statement_timeout = '2500ms'; set local lock_timeout = '1000ms'; set local idle_in_transaction_session_timeout = '3000ms'",
      );
      const result = await fn(db);
      if (signal.aborted) throw Error("deadline");
      return result;
    });
  const session =
    (name: "exchange" | "read" | "revoke"): Operation =>
    (args) =>
      run(async (db) => {
        const sql =
          name === "exchange"
            ? "select ops.exchange_client_portal_session($1::text,$2::text,$3::text) result"
            : name === "read"
              ? "select ops.read_client_portal_session($1::text,$2::text) result"
              : "select ops.revoke_client_portal_session($1::text,$2::text) result";
        return (
          await db.query(sql, name === "exchange" ? [args.slug, args.password, args.digest] : [args.slug, args.digest])
        ).rows[0]?.result;
      }, args.signal);
  const proposal =
    (save: boolean): Operation =>
    (args) =>
      run((db) => {
        const storage = portalProposalStorage(db);
        const scope = { slug: args.slug, digest: args.digest, publication_id: String(args.publication_id) };
        return save ? storage.save(scope, Number(args.publication_version), args.request) : storage.read(scope);
      }, args.signal);
  return {
    exchange: session("exchange"),
    read: session("read"),
    revoke: session("revoke"),
    proposalRead: proposal(false),
    proposalSave: proposal(true),
  };
}

/** Construct once per mounted host so admission and breaker state survive requests. */
export function createPortalHost(connectionString: string, origin = PORTAL_ORIGIN) {
  return createPortalWebHandler({
    ...portalSqlOperations(async (fn) => {
      const pool = new Pool({ connectionString, connectionTimeoutMillis: 1500, max: 1 });
      try {
        const client = await pool.connect();
        try {
          await client.query("begin");
          const value = await fn(client);
          await client.query("commit");
          return value;
        } catch (error) {
          await client.query("rollback").catch(() => {});
          throw error;
        } finally {
          client.release();
        }
      } finally {
        await pool.end();
      }
    }),
    origin,
  });
}
