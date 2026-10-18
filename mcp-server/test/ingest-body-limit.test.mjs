// ingest-body-limit.test.mjs — the live /ingest 413 body-limit check.
//
// This coverage used to ride along inside the canary-ingest sink's regression
// selftest; retiring the canary sink (#1879) removed it, and nothing else
// exercised the live Worker's size guard. The guard is the `content-length`
// comparison in ingest() against max_body_bytes from the shared transport
// contract (src/ingest-transport.v1.json).
//
// index.js cannot be imported under node --test (it pulls in
// "@cloudflare/workers-oauth-provider", which needs the Workers runtime), so
// this follows the repo's established technique (see correlation.test.mjs):
// read index.js as text, lift the real ingest() function out of it, and run
// that exact source against stubbed `json`, `neon` and transport bindings.
//
// Run with: node --test mcp-server/test/ingest-body-limit.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");
const INDEX = readFileSync(path.join(SRC, "index.js"), "utf8");
const TRANSPORT = JSON.parse(readFileSync(path.join(SRC, "ingest-transport.v1.json"), "utf8"));

function liftIngest() {
  const start = INDEX.indexOf("async function ingest(request, env) {");
  assert.ok(start >= 0, "index.js must still define ingest(request, env)");
  const end = INDEX.indexOf("\n}\n", start);
  assert.ok(end > start, "could not find the end of ingest()");
  return INDEX.slice(start, end + 3);
}

// Build a runnable ingest() whose database is a spy, so a test can prove the
// oversize path never reaches it.
function makeIngest() {
  const db = { connects: 0, inserts: 0 };
  const json = (body, status = 200) => ({ body, status });
  const neon = () => {
    db.connects += 1;
    return async () => {
      db.inserts += 1;
      return [{ id: "11111111-1111-4111-8111-111111111111", was_duplicate: false }];
    };
  };
  const factory = new Function("json", "neon", "INGEST_TRANSPORT", `${liftIngest()}\nreturn ingest;`);
  return { ingest: factory(json, neon, TRANSPORT), db };
}

const TOKEN = "test-ingest-token";
const ENV = { INGEST_TOKENS: JSON.stringify({ notes: TOKEN }), DATABASE_URL_WRITER: "stub" };

function post({ token = TOKEN, contentLength, body = "{}" } = {}) {
  const headers = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  if (contentLength !== undefined) headers["content-length"] = String(contentLength);
  return new Request("https://worker.invalid/ingest", { method: "POST", headers, body });
}

test("transport contract: max_body_bytes is a positive integer (1 MiB)", () => {
  assert.equal(TRANSPORT.schema, "carr-ingest-transport/v1");
  assert.ok(Number.isInteger(TRANSPORT.max_body_bytes) && TRANSPORT.max_body_bytes > 0);
  assert.equal(TRANSPORT.max_body_bytes, 1048576);
});

test("index.js reads the limit from the shared contract, not a private literal", () => {
  assert.match(INDEX, /import INGEST_TRANSPORT from "\.\/ingest-transport\.v1\.json"/);
  assert.match(INDEX, /len > INGEST_TRANSPORT\.max_body_bytes/);
});

test("an authenticated body one byte over the limit is refused 413 payload_too_large", async () => {
  const { ingest, db } = makeIngest();
  const res = await ingest(post({ contentLength: TRANSPORT.max_body_bytes + 1 }), ENV);
  assert.equal(res.status, 413);
  assert.deepEqual(res.body, { error: "payload_too_large" });
  assert.equal(db.connects, 0, "an oversize request must never reach the database");
});

test("a body exactly at the limit is not refused for size", async () => {
  const { ingest, db } = makeIngest();
  const res = await ingest(post({ contentLength: TRANSPORT.max_body_bytes }), ENV);
  assert.equal(res.status, 200);
  assert.equal(res.body.ok, true);
  assert.equal(db.inserts, 1);
});

test("a small body is accepted and recorded", async () => {
  const { ingest, db } = makeIngest();
  const res = await ingest(post({ contentLength: 2 }), ENV);
  assert.equal(res.status, 200);
  assert.equal(db.inserts, 1);
});

test("authentication is checked before size: an unauthenticated oversize body is 401, not 413", async () => {
  const { ingest, db } = makeIngest();
  const res = await ingest(post({ token: "wrong", contentLength: TRANSPORT.max_body_bytes * 10 }), ENV);
  assert.equal(res.status, 401);
  assert.deepEqual(res.body, { error: "unauthorized" });
  assert.equal(db.connects, 0);
});

test("a missing token is 401 even with an oversize body", async () => {
  const { ingest } = makeIngest();
  const res = await ingest(post({ token: "", contentLength: TRANSPORT.max_body_bytes + 1 }), ENV);
  assert.equal(res.status, 401);
});
