// Defect 049f269e: a signed-in browser could not render a Tour PDF (503), and
// no Tour write had ever come from a web session.
//
// Deal Room hands the Tour leaf a host-scoped env built with Object.create(env),
// so the Worker's secrets and bindings live on that object's PROTOTYPE. The
// runtime's tool seam used to build the dispatcher env as `{ ...env, ctx }`,
// which copies own properties only: callTool received no DATABASE_URL_WRITER,
// the writer pool failed before its first query, the leaf answered 503, and no
// render job row was ever inserted. The direct Tour reads read env in place,
// which is why the packet read kept working and hid the fault.
//
// This drives the REAL chain with a real web-session actor: the Deal Room
// cookie login, sessionFor, envForDealroomOrigin, the Tour leaf's CSRF gate,
// the production runtime adapter, invoke, and the real callTool. Only the
// packet read and the PDF render are stubbed, and the database socket is an
// offline fake that records where callTool tried to connect. Reaching the
// writer host proves the DSN crossed the seam; the old code never got there.

import test from "node:test";
import assert from "node:assert/strict";
import { neonConfig } from "@neondatabase/serverless";
import { createDealroomHandler } from "../src/dealroom-web.js";
import { createTourInternalWebHandler } from "../src/tour-internal-web.js";
import { createTourRuntimeAdapters, toolEnvironment } from "../src/tour-runtime.js";

const HOST = "dealroom.doctorcre.com";
const ORIGIN = `https://${HOST}`;
const JOE = "joe.bookout.carr.us@gmail.com";
const WRITER_HOST = "writer-sentinel.invalid";
const PROJECTION_ID = "10000000-0000-4000-8000-000000000001";
const IDEMPOTENCY_KEY = "40000000-0000-4000-8000-000000000001";
const digest = character => `sha256:${character.repeat(64)}`;

const sockets = [];
neonConfig.webSocketConstructor = class OfflineSocket {
  constructor(url) { sockets.push(String(url)); throw new Error("offline test socket"); }
};

class MemoryKv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) {
    const value = this.values.get(key);
    if (value === undefined) return null;
    return options?.type === "json" ? JSON.parse(value) : value;
  }
  async delete(key) { this.values.delete(key); }
}

function environment() {
  return {
    DEALROOM_HOST: HOST,
    GOOGLE_CLIENT_ID: "google-client.test",
    GOOGLE_CLIENT_SECRET: "not-a-real-secret",
    OAUTH_KV: new MemoryKv(),
    ASSETS: { async fetch() { return new Response("missing", { status: 404 }); } },
    DATABASE_URL_WRITER: `postgres://writer_user@${WRITER_HOST}/carr`,
    DATABASE_URL_READER: "postgres://reader_user@reader-sentinel.invalid/carr",
  };
}

function cookies(response) {
  return typeof response.headers.getSetCookie === "function"
    ? response.headers.getSetCookie() : [response.headers.get("set-cookie")];
}

async function signedIn(handler, env) {
  const start = await handler.fetch(new Request(`${ORIGIN}/auth/login?return_to=/tours`), env, {});
  const state = new URL(start.headers.get("location")).searchParams.get("state");
  const pending = cookies(start).find(value => value.startsWith("__Host-dealroom_oauth=")).split(";", 1)[0];
  const callback = await handler.fetch(
    new Request(`${ORIGIN}/auth/callback?state=${state}&code=stub`, { headers: { cookie: pending } }), env, {});
  const cookie = cookies(callback).find(value => value.startsWith("__Host-dealroom_session=")).split(";", 1)[0];
  const stored = [...env.OAUTH_KV.values.values()].map(value => { try { return JSON.parse(value); } catch { return null; } })
    .find(value => value && typeof value.csrfToken === "string");
  return { cookie, csrf: stored.csrfToken };
}

test("tool env inherits prototype-held Worker bindings instead of dropping them", () => {
  const scoped = Object.create({ DATABASE_URL_WRITER: "writer", carr_documents: { binding: true } });
  scoped.APP_HOST = "app.example.test";
  const ctx = { waitUntil() {} };
  const env = toolEnvironment(scoped, ctx);
  assert.equal(env.DATABASE_URL_WRITER, "writer");
  assert.deepEqual(env.carr_documents, { binding: true });
  assert.equal(env.APP_HOST, "app.example.test");
  assert.equal(env.ctx, ctx);
  assert.equal(Object.hasOwn(scoped, "ctx"), false, "the request's env object is not mutated");
});

test("a signed-in browser's Tour PDF render reaches the writer database through the real handler chain", async () => {
  const env = environment();
  const reads = [];
  const reports = [];
  let prepared = 0;
  const tourHandler = createTourInternalWebHandler(createTourRuntimeAdapters({
    internalReadFn: async (context, sql, params) => {
      reads.push({ actor: { slug: context.actor?.slug, human: context.actor?.human }, sql, params });
      return { packet: { properties: [{}] }, projection_digest: digest("1") };
    },
    prepareTourPdfArtifactFn: async () => {
      prepared += 1;
      return {
        packetDigest: digest("2"), templateDigest: digest("3"), rendererDigest: digest("4"),
        qcRulesetVersion: "1.0.0", qcRulesetDigest: digest("5"), markersDigest: digest("6"),
        rendered: { templateVersion: "1.0.0", rendererVersion: "1.0.3", propertyCount: 1,
          fontDigests: [digest("7")], artifactDigest: digest("8") },
      };
    },
    storeAndVerifyTourPdfFn: async () => { throw new Error("storage is not reached in this test"); },
    reportFailureFn: record => reports.push(record),
  }));
  const handler = createDealroomHandler({
    exchangeGoogleCodeFn: async () => ({ id_token: "stub" }),
    verifyGoogleIdTokenFn: async () => ({ email: JOE, email_verified: true, sub: `sub:${JOE}` }),
    now: () => 1_800_000_000_000,
    mcpHandler: async () => new Response("{}"),
    pipelineHandler: async () => new Response("{}"),
    tourHandler,
  });
  const { cookie, csrf } = await signedIn(handler, env);

  sockets.length = 0;
  const response = await handler.fetch(new Request(`${ORIGIN}/api/tours/pdf/render`, {
    method: "POST",
    headers: { cookie, "content-type": "application/json", origin: ORIGIN,
      "sec-fetch-site": "same-origin", "x-carr-csrf": csrf },
    body: JSON.stringify({ projection_id: PROJECTION_ID, idempotency_key: IDEMPOTENCY_KEY }),
  }), env, { waitUntil() {} });

  // The web-session actor is the verified partner from the cookie session.
  assert.deepEqual(reads.map(read => read.actor), [{ slug: "joe", human: true }]);
  assert.equal(reads[0].params[1], PROJECTION_ID);
  assert.equal(prepared, 1);
  // THE REGRESSION: callTool opened the writer pool against the Worker's
  // writer DSN. With the spread, the DSN was gone and no socket was attempted.
  assert.deepEqual(sockets, [`wss://${WRITER_HOST}/v2`]);
  // The offline socket still fails, so the browser sees the coarse 503 ...
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "tour_unavailable" });
  // ... and the operator now gets a sanitised record of why.
  assert.deepEqual(reports, [{ event: "tour_internal_failure", route: "/api/tours/pdf/render",
    status: 503, error_class: "Error", code: null }]);
  const reported = JSON.stringify(reports);
  for (const secret of [WRITER_HOST, "writer_user", "carr", csrf, "joe", "offline test socket"])
    assert.equal(reported.includes(secret), false, `failure record must not carry ${secret}`);
});

test("a ToolError refusal is reported by class and code only", async () => {
  const reports = [];
  class ToolErrorLike extends Error {
    constructor(payload) { super(`refused: ${JSON.stringify(payload)}`); this.name = "ToolError"; this.payload = payload; }
  }
  const handler = createTourInternalWebHandler({
    renderPdfFn: async () => { throw new ToolErrorLike({ error: "tour_actor_context_required", hint: "private detail" }); },
    reportFailureFn: record => reports.push(record),
  });
  const csrf = "csrf-value";
  const response = await handler.fetch(new Request("https://app.doctorcre.com/api/tours/pdf/render", {
    method: "POST",
    headers: { "content-type": "application/json", origin: "https://app.doctorcre.com",
      "sec-fetch-site": "same-origin", "x-carr-csrf": csrf },
    body: JSON.stringify({ projection_id: PROJECTION_ID, idempotency_key: IDEMPOTENCY_KEY }),
  }), { APP_HOST: "app.doctorcre.com" }, {}, { slug: "joe" }, { csrfToken: csrf });
  assert.equal(response.status, 503);
  assert.deepEqual(reports, [{ event: "tour_internal_failure", route: "/api/tours/pdf/render",
    status: 503, error_class: "ToolError", code: "tour_actor_context_required" }]);
  assert.doesNotMatch(JSON.stringify(reports), /private detail|refused/);
});
