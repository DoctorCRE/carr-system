import { scrubError, scrubRoute } from './runtime-errors.js';

// CARR MCP server — correlation id middleware (Program 3, Gap A, 2026-08-14).
//
// THE GAP THIS CLOSES. The production-maturity baseline's observability
// contract, verbatim: "One correlation ID connects UI request, Worker route,
// record verb, database operation, agent session, approval, release, and
// incident." Before this file, the Worker had none — grep for "correlation"
// across mcp-server/src/*.js returned zero hits. No route read an incoming
// id, generated one when absent, echoed one back, or made one available to
// anything downstream.
//
// WHAT THIS FILE IS: a small, standalone module — no `cloudflare:` import, no
// database driver — so it can be loaded and tested under plain `node --test`
// (index.js itself cannot: it pulls in @cloudflare/workers-oauth-provider,
// which resolves a `cloudflare:` specifier the Node loader refuses outside
// the Workers runtime). Four pieces:
//
//   isValidCorrelationId(value)   — pure format check, a standard UUID shape.
//   correlationIdFor(request)     — read x-correlation-id if present and
//                                    valid, else mint a fresh one. Garbage is
//                                    REPLACED, never passed through — a
//                                    caller cannot put an arbitrary string
//                                    into a column typed uuid, and cannot use
//                                    this header as a free-text injection
//                                    point into structured logs either.
//   withCorrelationHeader(res,id) — attach x-correlation-id to a Response
//                                    without disturbing its status or body.
//   wrapWithCorrelation(handler)  — the one middleware. Wraps a Worker
//                                    fetch(request, env, ctx) handler:
//                                      1. resolves the correlation id,
//                                      2. sets env.CORRELATION_ID so it is
//                                         available to a record-verb write or
//                                         a database operation downstream —
//                                         the SAME per-request env-mutation
//                                         pattern mcp.js's dispatch() already
//                                         uses for env.ctx (see that file's
//                                         `env.ctx = ctx;` line), not a new
//                                         convention,
//                                      3. calls the wrapped handler,
//                                      4. on a thrown error, logs one
//                                         structured line carrying the id and
//                                         returns a 500 whose JSON body also
//                                         carries it — "attached to error
//                                         output" cannot mean only a header,
//                                      5. echoes the id back as
//                                         x-correlation-id on every response,
//                                         success or error, always.
//
// WHERE THIS IS WIRED: index.js's top-level default export, once —
// `fetch: wrapWithCorrelation(routeRequest)` — so every route (the OAuth
// doors, /health, /release, /ingest, /mcp under every one of the four auth
// doors, /pipeline/changes, the Deal Room web handler, /capture) is covered
// without any individual route body being touched. See index.js's own
// comment at that line for why one wrap point is correct here.
//
// WHAT THIS FILE DELIBERATELY DOES NOT DO: it does not write to
// ops.run, ops.deployment, ops.incident, or ops.work_request. Checked by
// reading tools.js, mcp.js, dealroom.js, dealroom-web.js and capture.js in
// full: this Worker writes to none of those tables today (zero hits for
// "ops.run", "ops.deployment", "ops.incident", "ops.work_request" anywhere
// under mcp-server/src/), so there is no existing write to thread a
// correlation id into. env.CORRELATION_ID is the hook the first such write
// will read from — deliberately not exercised here, because inventing a
// write path this build was not asked to build would be scope creep, not
// closing the gap.

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Pure format check. Deliberately permissive on version/variant bits: the
 * database column is plain `uuid`, and ids can arrive from other minters in
 * the system (tools/ops-record.py's correlation_of(), Postgres's own
 * gen_random_uuid() default) that this Worker has no reason to second-guess
 * as long as the shape is a real UUID. */
export function isValidCorrelationId(value) {
  return typeof value === "string" && UUID_RE.test(value.trim());
}

/** Read x-correlation-id off a request; accept it verbatim (lowercased) if
 * it is a real UUID, otherwise mint a fresh one with crypto.randomUUID() —
 * available globally in the Workers runtime and in Node 18+, no import
 * needed. A garbage or missing header both resolve to the generated branch;
 * the caller never learns which case it was, which is the point: this
 * function's contract is "you always get a usable id back," not "you get an
 * error telling you your id was bad." */
export function correlationIdFor(request) {
  const header = request.headers.get("x-correlation-id");
  if (header && isValidCorrelationId(header)) return header.trim().toLowerCase();
  return crypto.randomUUID();
}

/** One JSON line per event. level is 'info' | 'warn' | 'error'; warn and
 * error go to console.error (so a log pipeline filtering stderr for
 * problems sees them), everything else to console.log — matching the
 * severity split the Worker's two pre-existing console calls already used
 * informally (console.warn in google-oidc.js and index.js's OAuth onError).
 * Returns the line for tests; production code does not need the value. */
export function logLine(level, event, fields = {}) {
  const line = JSON.stringify({ ts: new Date().toISOString(), level, event, ...fields });
  if (level === "warn" || level === "error") console.error(line);
  else console.log(line);
  return line;
}

/** Attach x-correlation-id to a Response's headers without touching its
 * status, statusText or body. Response is immutable once constructed, so
 * this builds a new one with a cloned Headers object carrying the addition —
 * every other header the wrapped handler set (content-type, and anything
 * else) survives untouched. */
export function withCorrelationHeader(response, correlationId) {
  const headers = new Headers(response.headers);
  headers.set("x-correlation-id", correlationId);
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function safePath(request) {
  try {
    return new URL(request.url).pathname;
  } catch {
    return null;
  }
}

/** The one middleware. Wrap a Worker fetch(request, env, ctx) handler once,
 * at the single top-level entry point, and every route it dispatches to
 * inherits: a resolved correlation id, env.CORRELATION_ID for anything
 * downstream to read, a guaranteed x-correlation-id response header, and an
 * uncaught throw that becomes a structured log line plus a 500 carrying the
 * id in its body — never an unhandled rejection reaching the Workers
 * runtime's own generic error page, which would have carried no id at all. */
export function wrapWithCorrelation(handler) {
  return async function correlatedFetch(request, env, ctx) {
    const correlationId = correlationIdFor(request);
    env.CORRELATION_ID = correlationId;

    let response;
    try {
      response = await handler(request, env, ctx);
    } catch (e) {
      logLine("error", "worker_uncaught_error", {
        correlation_id: correlationId,
        path: scrubRoute(safePath(request), { server: true }),
        message: scrubError({ type: e?.name, message: e?.message }).message,
      });
      response = new Response(
        JSON.stringify({ error: "internal_error", correlation_id: correlationId }),
        { status: 500, headers: { "content-type": "application/json" } },
      );
    }
    return withCorrelationHeader(response, correlationId);
  };
}
