// WR-000527 — one strict Bearer parser at every machine-authentication door.
//
// Audit F6 (PR #2183) found the doors stripping an OPTIONAL "Bearer " prefix,
// so the bare secret authenticated as well as "Bearer <secret>". Each door now
// asks bearerCredential() first and refuses before it reads its token map.
// The doors that index.js keeps to itself are lifted from the source, as
// ingest-body-limit.test.mjs does, because index.js cannot load under node.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { bearerCredential } from "../src/bearer.ts";
import { createCaptureHandler } from "../src/capture.js";
import { createDealroomHandler } from "../src/dealroom-web.js";
import { sharedOAuthFetch } from "../src/oauth-policy.js";

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src");
const SECRET = "synthetic-machine-secret-0527";

// Every header a strict parser must refuse, beside the legitimate forms.
const MALFORMED = [
  undefined, null, 7, {}, "", " ", SECRET, `Basic ${SECRET}`, "Bearer", "Bearer ", "Bearer  ",
  `Bearer\t${SECRET}`, `Bearer${SECRET}`, `Bearer ${SECRET} extra`, `Bearer ${SECRET}, Bearer ${SECRET}`,
  `Bearer ${SECRET},`, `Token ${SECRET}`, `xBearer ${SECRET}`, ` Bearer ${SECRET}`, `Bearer ${SECRET} `,
  `Bearer ${SECRET}\n`, `Bearer secéret`, `Bearer ${SECRET}; sponsor=joe`,
];
const LEGITIMATE = [`Bearer ${SECRET}`, `bearer ${SECRET}`, `BEARER ${SECRET}`, `BeArEr ${SECRET}`,
  `Bearer  ${SECRET}`];

test("the parser returns the credential for each legitimate scheme spelling", () => {
  for (const header of LEGITIMATE) assert.equal(bearerCredential(header), SECRET, header);
  // RFC 6750 b64token characters, including the padding a base64 secret ends with.
  assert.equal(bearerCredential("Bearer aZ09-._~+/=="), "aZ09-._~+/==");
});

test("the parser refuses every absent scheme, empty, multiple or malformed credential", () => {
  for (const header of MALFORMED) assert.equal(bearerCredential(header), null, JSON.stringify(header));
});

test("no source door strips an optional Bearer prefix any more", () => {
  const offenders = [];
  for (const file of readdirSync(SRC)) {
    if (!/\.(?:js|ts|mjs)$/.test(file)) continue;
    const text = readFileSync(path.join(SRC, file), "utf8");
    if (/replace\(\s*\/\^Bearer/i.test(text) || /match\(\s*\/\^Bearer/i.test(text)) offenders.push(file);
  }
  assert.deepEqual(offenders, []);
});

// ---------- identity.js doors ----------

// A token map whose lookup is observable: JSON.parse stringifies its argument,
// so a door that reads the map calls toString. A malformed header must not.
function countingMap(json) {
  const map = { reads: 0, toString() { this.reads += 1; return json; } };
  return map;
}

process.env.AGENT_TOKENS = JSON.stringify({ codex: SECRET });
process.env.LOCAL_TOKENS = JSON.stringify({ "joe-local": SECRET });
process.env.CODEX_CONTINUITY_TOKENS = JSON.stringify({ joe: SECRET });
process.env.UNATTENDED_TOKENS = JSON.stringify({ joe: { "nightly-mac": SECRET } });
process.env.HERMES_TOKENS = JSON.stringify({ "hermes-pilot": SECRET });
process.env.HERMES_COS_TOKENS = JSON.stringify({ "hermes-pilot": SECRET });
process.env.REVIEW_TOKENS = JSON.stringify({ "codex-reviewer": SECRET });
const identity = await import("../src/identity.js?wr000527-server-credentials");

const DOORS = [
  ["agent", h => identity.agentActorForToken(h, process.env.AGENT_TOKENS), "codex"],
  ["local", h => identity.agentActorForToken(h, process.env.LOCAL_TOKENS, "local-token"), "joe-local"],
  ["codex continuity", h => identity.continuityActorForTokenMaps(h, process.env.CODEX_CONTINUITY_TOKENS), "codex"],
  ["unattended", h => identity.unattendedActorForToken(h, process.env.UNATTENDED_TOKENS), "joe-local"],
  ["hermes", h => identity.hermesActorForTokenMaps(h, process.env.HERMES_TOKENS), "hermes-pilot"],
  ["hermes cos", h => identity.hermesCosActorForToken(h, process.env.HERMES_COS_TOKENS), "hermes-pilot"],
];

for (const [label, door, slug] of DOORS) {
  test(`the ${label} door admits each legitimate Bearer spelling`, () => {
    for (const header of LEGITIMATE) assert.equal(door(header)?.slug, slug, header);
  });
  test(`the ${label} door refuses the bare secret and every malformed header`, () => {
    for (const header of MALFORMED) assert.equal(door(header), null, JSON.stringify(header));
  });
}

test("a malformed header is refused before any identity door reads its token map", () => {
  const lookups = [
    map => identity.agentActorForToken(SECRET, map),
    map => identity.continuityActorForTokenMaps(SECRET, map),
    map => identity.unattendedActorForToken(SECRET, map),
    map => identity.hermesActorForToken(SECRET, map),
    map => identity.hermesCosActorForToken(SECRET, map),
  ];
  for (const lookup of lookups) {
    const map = countingMap(JSON.stringify({ codex: SECRET }));
    assert.equal(lookup(map), null);
    assert.equal(map.reads, 0, "the door parsed its token map for a malformed header");
  }
  // The positive control: a well-formed header does reach the map.
  const map = countingMap(JSON.stringify({ codex: SECRET }));
  identity.agentActorForToken(`Bearer ${SECRET}`, map);
  assert.equal(map.reads, 1);
});

test("the review council door dispatches only a well-formed Bearer", async () => {
  const request = header => ({ method: "GET", url: "https://x/mcp",
    headers: { get: name => (name === "authorization" ? header : null) } });
  for (const header of MALFORMED.filter(h => typeof h === "string"))
    assert.equal(await identity.serveReviewRequest(request(header), {}, {}), null, header);
  for (const header of LEGITIMATE) {
    const response = await identity.serveReviewRequest(request(header), {}, {});
    assert.notEqual(response, null, header);
    assert.equal(response.status, 405, "a GET reaches mcp.js's own method refusal");
  }
});

// ---------- index.js doors, lifted from source ----------

const INDEX = readFileSync(path.join(SRC, "index.js"), "utf8");
function lift(signature) {
  const start = INDEX.indexOf(signature);
  assert.ok(start >= 0, `index.js must still define ${signature}`);
  const end = INDEX.indexOf("\n}\n", start);
  return INDEX.slice(start, end + 3);
}
const headerRequest = header => ({ headers: { get: name =>
  (name === "authorization" ? header ?? null : name === "content-length" ? "2" : null) },
  json: async () => ({}) });

test("the smoke-probe door admits a well-formed Bearer and refuses every malformed header", () => {
  const source = lift("function probeActorFor(request, env) {");
  const probeActorFor = new Function("bearerCredential", `${source}\nreturn probeActorFor;`)(bearerCredential);
  const env = { PROBE_TOKENS: JSON.stringify({ "smoke-probe": SECRET }) };
  for (const header of LEGITIMATE) assert.equal(probeActorFor(headerRequest(header), env)?.slug, "smoke-probe");
  for (const header of MALFORMED.filter(h => typeof h === "string" || h == null))
    assert.equal(probeActorFor(headerRequest(header), env), null, JSON.stringify(header));
});

test("the ingest door admits a well-formed Bearer and refuses the bare secret before the body", async () => {
  const source = lift("async function ingest(request, env) {");
  const json = (body, status = 200) => ({ body, status });
  let databaseCalls = 0;
  const neon = () => { databaseCalls += 1; return async () => [{ id: "x", was_duplicate: false }]; };
  const ingest = new Function("json", "neon", "INGEST_TRANSPORT", "bearerCredential",
    `${source}\nreturn ingest;`)(json, neon, { max_body_bytes: 1024 }, bearerCredential);
  const env = { INGEST_TOKENS: JSON.stringify({ "fixture-source": SECRET }) };
  for (const header of MALFORMED.filter(h => typeof h === "string" || h == null)) {
    const response = await ingest(headerRequest(header), env);
    assert.equal(response.status, 401, JSON.stringify(header));
  }
  assert.equal(databaseCalls, 0);
  const admitted = await ingest(headerRequest(`bearer ${SECRET}`), env);
  assert.notEqual(admitted.status, 401);
});

// ---------- capture, e2e session and shared OAuth doors ----------

test("the capture claim door refuses the bare device secret before any database access", async () => {
  const handler = createCaptureHandler({ randomStringFn: () => "opaque",
    withWriter: async () => assert.fail("a refused claim reached the database") });
  const env = { CAPTURE_TOKENS: JSON.stringify({ "mac-studio": SECRET }) };
  for (const header of [SECRET, `Basic ${SECRET}`, `Bearer ${SECRET} extra`, `Bearer ${SECRET}, Bearer ${SECRET}`]) {
    const response = await handler.fetch(new Request("https://worker.test/capture/claim", {
      method: "POST", headers: { authorization: header, "content-type": "application/json" }, body: "{}" }), env);
    assert.equal(response.status, 401, header);
  }
  // A well-formed bearer passes authentication and fails on the empty claim body instead.
  const passed = await handler.fetch(new Request("https://worker.test/capture/claim", {
    method: "POST", headers: { authorization: `bearer ${SECRET}`, "content-type": "application/json" },
    body: "{}" }), env);
  assert.notEqual(passed.status, 401);
});

test("the staging e2e-session door refuses a comma-joined duplicate credential", async () => {
  const secret = "synthetic-e2e-session-secret-for-tests-only";
  const server = createDealroomHandler({ now: () => 1_800_000_000_000, prepareE2eSessionFn: async () => {} });
  // The staging hosts e2e-session.test.mjs uses; the door answers 404 anywhere else.
  const appHost = "doctorcre-app-staging.joe-bookout-carr-us.workers.dev";
  const env = { CARR_ENV: "staging", APP_HOST: "carr-mcp-staging.joe-bookout-carr-us.workers.dev",
    DOCTORCRE_APP_HOST: appHost, E2E_SESSION_SECRET: secret,
    OAUTH_KV: { put: async () => {}, get: async () => null, delete: async () => {} } };
  const post = authorization => server.fetch(new Request(`https://${appHost}/auth/e2e-session`,
    { method: "POST", headers: { authorization } }), env);
  for (const header of [secret, `Bearer ${secret}, Bearer ${secret}`, `Basic ${secret}`])
    assert.equal((await post(header)).status, 401, header);
  assert.notEqual((await post(`bearer ${secret}`)).status, 401);
});

test("the shared OAuth policy refuses a malformed bearer before unwrapping it", async () => {
  let unwrapped = 0;
  const env = { OAUTH_PROVIDER: { unwrapToken: async () => { unwrapped += 1; return null; } } };
  const provider = { fetch: async () => new Response("provider") };
  for (const header of [`Bearer a b`, `Bearer a, Bearer b`, `Bearer `]) {
    const response = await sharedOAuthFetch(provider,
      new Request("https://carr.test/mcp", { method: "POST", headers: { authorization: header } }), env, {});
    assert.equal(response.status, 401, header);
  }
  assert.equal(unwrapped, 0);
});
