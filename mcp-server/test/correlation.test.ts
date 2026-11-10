// correlation.test.mjs — coverage for Program 3 Gap A: the Worker had no
// correlation id anywhere. Grep for "correlation" across mcp-server/src/*.js
// before this build returned zero hits — no route read one, generated one, or
// echoed one back. The production-maturity baseline's own words: "One
// correlation ID connects UI request, Worker route, record verb, database
// operation, agent session, approval, release, and incident." This file
// proves the Worker-route half of that sentence: a caller's own id is
// respected when it is a real UUID, a missing or garbage one is replaced with
// a freshly generated one, every response — success or thrown error — carries
// it back as x-correlation-id, and it is handed to the wrapped handler via
// env.CORRELATION_ID (the same per-request env-mutation pattern mcp.js's
// dispatch() already uses for env.ctx — see the "wiring" tests at the bottom).
//
// index.js itself cannot be imported under node --test: it pulls in
// "@cloudflare/workers-oauth-provider", which resolves a `cloudflare:` module
// specifier Node's loader refuses outside the Workers runtime (proven live —
// see the source-assertion tests at the bottom, same technique
// tool-read-call.test.mjs uses to prove mcp.js's write-branch wiring without a
// live database). So this file tests correlation.ts directly — a standalone
// module with no Worker-only import — and proves index.js WIRES it by reading
// index.js as text, the same technique already established in this repo.
//
// Run with: node --test mcp-server/test/correlation.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  correlationIdFor,
  isValidCorrelationId,
  logLine,
  withCorrelationHeader,
  wrapWithCorrelation,
} from "../src/correlation.ts";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const A_REAL_UUID = "9f3b2c1a-4d5e-4f60-8a1b-0123456789ab";

// ────────────────────────────────────────────────────────────────────────
// isValidCorrelationId — pure format check
// ────────────────────────────────────────────────────────────────────────

test("isValidCorrelationId: accepts a well-formed UUID", () => {
  assert.equal(isValidCorrelationId(A_REAL_UUID), true);
  assert.equal(isValidCorrelationId(crypto.randomUUID()), true);
});

test("isValidCorrelationId: refuses garbage — not a UUID at all, wrong shape, empty, non-string", () => {
  for (const bad of [
    "not-a-uuid",
    "12345",
    "",
    "  ",
    null,
    undefined,
    42,
    {},
    "9f3b2c1a-4d5e-4f60-8a1b-0123456789abXYZ",
    "'; drop table ops.run; --",
  ]) {
    assert.equal(isValidCorrelationId(bad), false, `expected ${JSON.stringify(bad)} to be invalid`);
  }
});

// ────────────────────────────────────────────────────────────────────────
// correlationIdFor — the accept-or-generate decision, pure, given a Request
// ────────────────────────────────────────────────────────────────────────

test("correlationIdFor: a request carrying a valid x-correlation-id echoes that EXACT value", () => {
  const req = new Request("https://api.doctorcre.com/health", {
    headers: { "x-correlation-id": A_REAL_UUID },
  });
  assert.equal(correlationIdFor(req), A_REAL_UUID.toLowerCase());
});

test("correlationIdFor: a request with no x-correlation-id header gets a freshly generated, valid UUID", () => {
  const req = new Request("https://api.doctorcre.com/health");
  const id = correlationIdFor(req);
  assert.match(id, UUID_RE);
});

test("correlationIdFor: two header-less requests get DIFFERENT generated ids — never a constant fallback", () => {
  const id1 = correlationIdFor(new Request("https://api.doctorcre.com/health"));
  const id2 = correlationIdFor(new Request("https://api.doctorcre.com/health"));
  assert.notEqual(id1, id2);
});

test("correlationIdFor: garbage in x-correlation-id is REPLACED, never passed through", () => {
  const req = new Request("https://api.doctorcre.com/mcp", {
    headers: { "x-correlation-id": "'; drop table ops.run; --" },
  });
  const id = correlationIdFor(req);
  assert.match(id, UUID_RE);
  assert.notEqual(id, "'; drop table ops.run; --");
});

test("correlationIdFor: case-insensitive on input, normalized to lowercase on output", () => {
  const req = new Request("https://api.doctorcre.com/health", {
    headers: { "x-correlation-id": A_REAL_UUID.toUpperCase() },
  });
  assert.equal(correlationIdFor(req), A_REAL_UUID.toLowerCase());
});

// ────────────────────────────────────────────────────────────────────────
// withCorrelationHeader — attaches the header without disturbing the rest
// ────────────────────────────────────────────────────────────────────────

test("withCorrelationHeader: sets x-correlation-id and preserves status, body and other headers", async () => {
  const original = new Response(JSON.stringify({ ok: true }), {
    status: 201,
    headers: { "content-type": "application/json", "x-other": "kept" },
  });
  const out = withCorrelationHeader(original, A_REAL_UUID);
  assert.equal(out.status, 201);
  assert.equal(out.headers.get("x-correlation-id"), A_REAL_UUID);
  assert.equal(out.headers.get("content-type"), "application/json");
  assert.equal(out.headers.get("x-other"), "kept");
  assert.deepEqual(await out.json(), { ok: true });
});

// ────────────────────────────────────────────────────────────────────────
// wrapWithCorrelation — the one middleware. Proves the two behaviors the
// brief's gate (a) asks for: echo-or-generate on every response, and a
// thrown error still carries the id in both the response and a structured
// log line.
// ────────────────────────────────────────────────────────────────────────

test("wrapWithCorrelation: echoes the caller's own valid id back as the response header", async () => {
  const handler = async (_req: Request, _env: { CORRELATION_ID?: string }, _ctx: object) =>
    new Response("ok", { status: 200 });
  const wrapped = wrapWithCorrelation(handler);
  const req = new Request("https://api.doctorcre.com/health", {
    headers: { "x-correlation-id": A_REAL_UUID },
  });
  const res = await wrapped(req, {}, {});
  assert.equal(res.headers.get("x-correlation-id"), A_REAL_UUID.toLowerCase());
  assert.equal(await res.text(), "ok");
});

test("wrapWithCorrelation: a request with no header gets a generated UUID back, and it is a real UUID", async () => {
  const handler = async () => new Response("ok");
  const wrapped = wrapWithCorrelation(handler);
  const res = await wrapped(new Request("https://api.doctorcre.com/health"), {}, {});
  assert.match(res.headers.get("x-correlation-id") ?? "", UUID_RE);
});

test("wrapWithCorrelation: hands the resolved id to the wrapped handler via env.CORRELATION_ID", async () => {
  let seen: string | undefined | null = null;
  const handler = async (_req: Request, env: { CORRELATION_ID?: string }) => {
    seen = env.CORRELATION_ID;
    return new Response("ok");
  };
  const wrapped = wrapWithCorrelation(handler);
  const req = new Request("https://api.doctorcre.com/mcp", {
    headers: { "x-correlation-id": A_REAL_UUID },
  });
  const res = await wrapped(req, {}, {});
  assert.equal(seen, A_REAL_UUID.toLowerCase());
  assert.equal(res.headers.get("x-correlation-id"), A_REAL_UUID.toLowerCase());
});

test("wrapWithCorrelation: a throwing handler still returns a response carrying the correlation id — never an unhandled rejection", async () => {
  const handler = async () => {
    throw new Error("boom");
  };
  const wrapped = wrapWithCorrelation(handler);
  const req = new Request("https://api.doctorcre.com/mcp", {
    headers: { "x-correlation-id": A_REAL_UUID },
  });
  const res = await wrapped(req, {}, {});
  assert.equal(res.status, 500);
  assert.equal(res.headers.get("x-correlation-id"), A_REAL_UUID.toLowerCase());
  const body = await res.json();
  assert.equal(body.correlation_id, A_REAL_UUID.toLowerCase());
  assert.equal(body.error, "internal_error");
});

test("wrapWithCorrelation: a throwing handler emits a structured log line carrying the correlation id (attached to error output)", async () => {
  const originalError = console.error;
  const lines: string[] = [];
  console.error = (line) => lines.push(line);
  try {
    const handler = async () => {
      throw new Error("boom for the log");
    };
    const wrapped = wrapWithCorrelation(handler);
    const req = new Request("https://api.doctorcre.com/mcp", {
      headers: { "x-correlation-id": A_REAL_UUID },
    });
    await wrapped(req, {}, {});
  } finally {
    console.error = originalError;
  }
  assert.equal(lines.length, 1);
  const parsed = JSON.parse(lines[0] ?? "");
  assert.equal(parsed.correlation_id, A_REAL_UUID.toLowerCase());
  assert.equal(parsed.level, "error");
  assert.match(parsed.message, /boom for the log/);
});

test("wrapWithCorrelation: never throws itself, even when the inner handler rejects with a non-Error", async () => {
  const handler = async () => {
    throw "just a string";
  };
  const wrapped = wrapWithCorrelation(handler);
  await assert.doesNotReject(wrapped(new Request("https://api.doctorcre.com/health"), {}, {}));
});

// ────────────────────────────────────────────────────────────────────────
// logLine — pure, no network. One JSON line, correlation id present when given.
// ────────────────────────────────────────────────────────────────────────

test("logLine: produces one JSON-parseable line carrying the fields it was given", () => {
  const originalLog = console.log;
  const lines: string[] = [];
  console.log = (line) => lines.push(line);
  try {
    logLine("info", "worker_request", { correlation_id: A_REAL_UUID, path: "/health" });
  } finally {
    console.log = originalLog;
  }
  assert.equal(lines.length, 1);
  const parsed = JSON.parse(lines[0] ?? "");
  assert.equal(parsed.correlation_id, A_REAL_UUID);
  assert.equal(parsed.event, "worker_request");
  assert.equal(parsed.level, "info");
  assert.ok(parsed.ts);
});

test("logLine: routes warn/error to console.error and everything else to console.log", () => {
  const seen = { log: 0, error: 0 };
  const originalLog = console.log,
    originalError = console.error;
  console.log = () => {
    seen.log++;
  };
  console.error = () => {
    seen.error++;
  };
  try {
    logLine("info", "a", {});
    logLine("warn", "b", {});
    logLine("error", "c", {});
  } finally {
    console.log = originalLog;
    console.error = originalError;
  }
  assert.equal(seen.log, 1);
  assert.equal(seen.error, 2);
});

// ────────────────────────────────────────────────────────────────────────
// Wiring proof — index.js cannot be imported under node --test (it pulls in
// @cloudflare/workers-oauth-provider, which resolves a `cloudflare:` module
// specifier the Node loader refuses outside the Workers runtime — proven
// live below, same as index.js's own header comment already says about
// identity.js's agentActorForToken). So the wiring is proven the same way
// tool-read-call.test.mjs proves mcp.js's write-branch wiring: read the
// source, assert the call is actually there, in the right place.
// ────────────────────────────────────────────────────────────────────────

test("index.js genuinely cannot be imported outside the Workers runtime (documents why this file cannot exercise it directly)", async () => {
  await assert.rejects(import(new URL("../src/index.js", import.meta.url).href), (e) =>
    /cloudflare:/.test(String(e instanceof Error ? e.message : e)),
  );
});

test("index.js: the top-level default export's fetch is wrapped with wrapWithCorrelation", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/index.js", import.meta.url), "utf8");
  assert.match(
    src,
    /import\s*\{\s*wrapWithCorrelation\s*\}\s*from\s*["']\.\/correlation\.ts["']/,
    "index.js must import wrapWithCorrelation from ./correlation.ts",
  );
  assert.match(
    src,
    /fetch:\s*wrapWithCorrelation\(/,
    "the default export's fetch must be wrapWithCorrelation(...) — every route (health, release, " +
      "ingest, /mcp under every auth door, /pipeline/changes, the Deal Room web handler, capture) " +
      "flows through this one entry point, so wrapping it here covers all of them without touching " +
      "any individual route body",
  );
});
