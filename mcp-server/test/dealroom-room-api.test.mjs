// The Model Room observatory's two doors onto the partner room, proved without
// Google, a database, or a browser (Joe's ruling 0892c539).
//
// WHAT THESE TESTS ARE ACTUALLY GUARDING, in the order the risk runs:
//
//   THE WIRE IS NOT PUBLIC. Both endpoints sit behind the same cookie session
//   as the rest of this host, and an unauthenticated caller gets a JSON 401
//   rather than a login redirect — an API that redirects a fetch() to Google is
//   an API that looks broken instead of expired.
//
//   ATTRIBUTION IS SERVER-DERIVED, WHICH MEANS THE REQUEST BODY CANNOT TOUCH
//   IT. This is the one property the whole partner room rests on. A caller may
//   send seat, sponsor, kind, room or msg_id; none of them are consulted, and
//   the assertion below is written against the values that reach the database
//   layer rather than against the response, because a response can echo
//   anything.
//
//   THE READ IS THE VERB'S READ. The pagination arguments the browser sends are
//   clamped by partner-room.js's own normalizer, so the panel's cursor and a
//   desk's cursor can never come to mean two different things (rule a8c55a47).
//
//   THE CONTROL PATH IS AN ALLOWLIST. The RECONNECT button can ask for exactly
//   one action on a desk name shaped like a desk name, and the Worker rebuilds
//   the receipt body from those two validated values rather than echoing what
//   the page sent.

import test from "node:test";
import assert from "node:assert/strict";
import { createDealroomHandler, roomControlTurn } from "../src/dealroom-web.js";
import { normalizeRoomPaging, ROOM_BODY_MAX } from "../src/partner-room.js";

const HOST = "dealroom.doctorcre.com";
const ORIGIN = `https://${HOST}`;
const JOE = "joe.bookout.carr.us@gmail.com";

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

function env() {
  return {
    DEALROOM_HOST: HOST,
    GOOGLE_CLIENT_ID: "google-client.test",
    GOOGLE_CLIENT_SECRET: "not-a-real-secret",
    OAUTH_KV: new MemoryKv(),
    ASSETS: { async fetch() { return new Response("missing", { status: 404 }); } },
  };
}

const TURNS = [
  { seq: "70", at: "2026-08-22T14:00:00+00:00", sponsor: "joe", seat: "claude", kind: "turn", body: "on it", msg_id: "m70" },
  { seq: "71", at: "2026-08-22T14:01:00+00:00", sponsor: "dell", seat: "human", kind: "turn", body: "thanks", msg_id: "m71" },
];

function handlerWith(overrides = {}) {
  const calls = { reads: [], latest: [], writes: [] };
  const handler = createDealroomHandler({
    exchangeGoogleCodeFn: async () => ({ id_token: "stub" }),
    verifyGoogleIdTokenFn: async () => ({ email: JOE, email_verified: true, sub: `sub:${JOE}` }),
    now: () => 1_800_000_000_000,
    mcpHandler: async () => new Response("{}"),
    pipelineHandler: async () => new Response("{}"),
    roomReadFn: async (_env, params) => {
      calls.reads.push(params);
      const after = params.after_seq;
      const turns = TURNS.filter((t) => Number(t.seq) > after).slice(0, params.limit);
      return { ok: true, room: "partner-line", turns,
        latest_seq: turns.length ? turns.at(-1).seq : after, more: false };
    },
    roomLatestFn: async (_env, params) => {
      calls.latest.push(params);
      return { ok: true, room: "model-room", mode: params.mode, turns: TURNS,
        latest_seq: "71", before_seq: "70", more: false };
    },
    roomWriteFn: async (_env, params) => {
      calls.writes.push(params);
      return { ok: true, room: "partner-line", seq: 99, at: "2026-08-22T15:00:00+00:00",
        sponsor: params.sponsor, seat: params.seat, kind: params.kind, msg_id: params.msgId };
    },
    queueReadFn: async () => ({ ok: true, room: "partner-line", events: [], projected_at: null, live: false }),
    ...overrides,
  });
  return { handler, calls };
}

test("newest room endpoint is authenticated and returns a fresh window", async () => {
  const { handler, calls } = handlerWith();
  const environment = env();
  assert.equal((await handler.fetch(new Request(`${ORIGIN}/api/room/latest`), environment, {})).status, 401);
  const cookie = await signedIn(handler, environment);
  const response = await handler.fetch(new Request(`${ORIGIN}/api/room/latest?limit=80&mode=conversation`,
    { headers: { cookie } }), environment, {});
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(calls.latest[0], { before_seq: undefined, limit: 80, mode: "conversation" });
  assert.equal((await response.json()).latest_seq, "71");
});

async function signedIn(handler, environment) {
  const start = await handler.fetch(new Request(`${ORIGIN}/auth/login?return_to=/room.html`), environment, {});
  const google = new URL(start.headers.get("location"));
  const state = google.searchParams.get("state");
  const pending = (typeof start.headers.getSetCookie === "function"
    ? start.headers.getSetCookie() : [start.headers.get("set-cookie")])
    .find((value) => value.startsWith("__Host-dealroom_oauth=")).split(";", 1)[0];
  const callback = await handler.fetch(
    new Request(`${ORIGIN}/auth/callback?state=${state}&code=stub`, { headers: { cookie: pending } }),
    environment, {});
  const session = (typeof callback.headers.getSetCookie === "function"
    ? callback.headers.getSetCookie() : [callback.headers.get("set-cookie")])
    .find((value) => value.startsWith("__Host-dealroom_session=")).split(";", 1)[0];
  return session;
}

function postHeaders(cookie, csrf, extra = {}) {
  return { cookie, "content-type": "application/json", origin: ORIGIN,
    "sec-fetch-site": "same-origin", "x-carr-csrf": csrf, ...extra };
}

test("both room endpoints require a session, and answer a fetch with JSON rather than a redirect", async () => {
  const { handler } = handlerWith();
  const environment = env();
  for (const [path, init] of [["/api/room/turns", {}],
    ["/api/room/turn", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" }]]) {
    const response = await handler.fetch(new Request(`${ORIGIN}${path}`, init), environment, {});
    assert.equal(response.status, 401, path);
    assert.deepEqual(await response.json(), { error: "unauthorized", state: "sign_in_required" });
  }
});

test("expired Tour API sessions return JSON 401 instead of an OAuth redirect", async () => {
  const { handler } = handlerWith();
  const response = await handler.fetch(new Request(`${ORIGIN}/api/tours/library`), env(), {});
  assert.equal(response.status, 401);
  assert.deepEqual(await response.json(), { error: "unauthorized", state: "sign_in_required" });
});

test("the panel's own page is served behind the same sign-in as the rest of the host", async () => {
  const { handler } = handlerWith();
  const anonymous = await handler.fetch(new Request(`${ORIGIN}/room.html`), env(), {});
  assert.equal(anonymous.status, 302);
  assert.match(anonymous.headers.get("location"), /\/auth\/login\?return_to=%2Froom\.html/);
});

test("reading the wire pages by after_seq and carries the viewer's own identity", async () => {
  const { handler, calls } = handlerWith();
  const environment = env();
  const cookie = await signedIn(handler, environment);

  const first = await handler.fetch(new Request(`${ORIGIN}/api/room/turns`, { headers: { cookie } }), environment, {});
  assert.equal(first.status, 200);
  const body = await first.json();
  assert.deepEqual(body.turns.map((t) => t.seq), ["70", "71"]);
  assert.equal(body.actor.slug, "joe");
  assert.ok(body.csrf_token, "the composer needs a synchronizer token and gets it here");
  assert.deepEqual(calls.reads[0], { after_seq: 0, limit: 50 });

  const next = await handler.fetch(
    new Request(`${ORIGIN}/api/room/turns?after_seq=70&limit=1`, { headers: { cookie } }), environment, {});
  const page = await next.json();
  assert.deepEqual(page.turns.map((t) => t.seq), ["71"]);
  assert.deepEqual(calls.reads[1], { after_seq: 70, limit: 1 });
});

test("Queue API is authenticated, no-store, and preserves its exact projection contract", async () => {
  const { handler } = handlerWith({ queueReadFn: async () => ({ ok: true, room: "partner-line", events: [{
    v: 1, board: "carr-build", event_id: 9, event: "started", task_id: "t_one",
    card: { title: "<unsafe>", target: "sol", effective_model: "gpt-5.6-sol", status: "running", priority: "P2", cap: "read", updated_at: "2026-08-24T18:00:00Z", source_seq: null },
    summary: "<unsafe summary>", projected_at: "2026-08-24T18:00:00Z",
  }], projected_at: "2026-08-24T18:00:00Z", live: true }) });
  const environment = env();
  const anonymous = await handler.fetch(new Request(`${ORIGIN}/api/room/queue`), environment, {});
  assert.equal(anonymous.status, 401);
  const cookie = await signedIn(handler, environment);
  const response = await handler.fetch(new Request(`${ORIGIN}/api/room/queue`, { headers: { cookie } }), environment, {});
  assert.equal(response.status, 200); assert.equal(response.headers.get("cache-control"), "no-store");
  const body = await response.json();
  assert.deepEqual(Object.keys(body), ["ok", "room", "events", "projected_at", "live"]);
  assert.equal(body.events[0].card.title, "<unsafe>");
});

test("paging arguments are clamped by the room's own normalizer, never by a second copy of the rule", async () => {
  const { handler, calls } = handlerWith();
  const environment = env();
  const cookie = await signedIn(handler, environment);

  await handler.fetch(new Request(`${ORIGIN}/api/room/turns?limit=9999`, { headers: { cookie } }), environment, {});
  const clamped = normalizeRoomPaging({ after_seq: 0, limit: 9999 });
  assert.equal(clamped.limit, 200, "the room's own cap is 200 and this endpoint inherits it");
  assert.deepEqual(calls.reads.at(-1), { after_seq: clamped.after, limit: clamped.limit });

  for (const query of ["after_seq=-4", "after_seq=abc", "limit=0.5"]) {
    const bad = await handler.fetch(new Request(`${ORIGIN}/api/room/turns?${query}`, { headers: { cookie } }), environment, {});
    assert.equal(bad.status, 400, query);
  }
});

test("posting a turn ignores every caller-supplied attribution and mints its own identity", async () => {
  const { handler, calls } = handlerWith();
  const environment = env();
  const cookie = await signedIn(handler, environment);
  const csrf = (await (await handler.fetch(
    new Request(`${ORIGIN}/api/room/turns`, { headers: { cookie } }), environment, {})).json()).csrf_token;

  const response = await handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(cookie, csrf),
    body: JSON.stringify({ body: "hello the room", seat: "claude", sponsor: "dell",
      kind: "system", room: "some-other-room", msg_id: "11111111-1111-1111-1111-111111111111" }),
  }), environment, {});

  assert.equal(response.status, 200);
  assert.equal(calls.writes.length, 1);
  const written = calls.writes[0];
  assert.equal(written.seat, "human", "the panel has no seat selector and the server enforces that");
  assert.equal(written.sponsor, "joe", "sponsor comes from the verified session, never the body");
  assert.equal(written.originChannel, "browser-human", "only the authenticated browser endpoint may stamp human origin");
  assert.equal(written.originActor, "joe", "the browser identity is server-derived from the session");
  assert.equal(written.kind, "turn");
  assert.equal(written.body, "hello the room");
  assert.notEqual(written.msgId, "11111111-1111-1111-1111-111111111111");
  assert.match(written.msgId, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  assert.equal(written.room, undefined, "the endpoint never forwards a caller's room");
});

test("a retry of a post whose response was never seen cannot land a second turn", async () => {
  // THE FAILURE THIS GUARDS is not a double click — it is a response the
  // browser never received. The turn landed; the composer showed a failure
  // toast; the reader pressed retry. A random id per request would post it
  // twice and there would be no way to tell the two apart afterwards.
  const { handler, calls } = handlerWith();
  const environment = env();
  const cookie = await signedIn(handler, environment);
  const csrf = (await (await handler.fetch(
    new Request(`${ORIGIN}/api/room/turns`, { headers: { cookie } }), environment, {})).json()).csrf_token;

  const send = () => handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(cookie, csrf),
    body: JSON.stringify({ body: "the release gate wants four approval values" }),
  }), environment, {});

  assert.equal((await send()).status, 200);
  assert.equal((await send()).status, 200);
  assert.equal(calls.writes.length, 2, "both requests reach the wire — the room, not the Worker, is what dedups");
  assert.equal(calls.writes[0].msgId, calls.writes[1].msgId,
    "and they carry the same message id, so the room's uniqueness collapses the retry");

  // A DIFFERENT body from the same session is a different turn, and the same
  // body from a different session is too — the id is not a hash of the text.
  await handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(cookie, csrf),
    body: JSON.stringify({ body: "the release gate wants four approval values." }),
  }), environment, {});
  assert.notEqual(calls.writes[2].msgId, calls.writes[0].msgId);

  const otherEnv = env();
  const otherCookie = await signedIn(handler, otherEnv);
  const otherCsrf = (await (await handler.fetch(
    new Request(`${ORIGIN}/api/room/turns`, { headers: { cookie: otherCookie } }), otherEnv, {})).json()).csrf_token;
  await handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(otherCookie, otherCsrf),
    body: JSON.stringify({ body: "the release gate wants four approval values" }),
  }), otherEnv, {});
  assert.notEqual(calls.writes[3].msgId, calls.writes[0].msgId,
    "two sessions saying the same thing are two turns");

  // The window is a minute, not forever: a genuine repeat later still lands.
  const later = handlerWith({ now: () => 1_800_000_000_000 + 120_000 });
  const laterEnv = env();
  const laterCookie = await signedIn(later.handler, laterEnv);
  const laterCsrf = (await (await later.handler.fetch(
    new Request(`${ORIGIN}/api/room/turns`, { headers: { cookie: laterCookie } }), laterEnv, {})).json()).csrf_token;
  await later.handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(laterCookie, laterCsrf),
    body: JSON.stringify({ body: "the release gate wants four approval values" }),
  }), laterEnv, {});
  assert.match(later.calls.writes[0].msgId, /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
});

test("the composer post carries the whole browser-write guard, not a lighter version of it", async () => {
  const { handler, calls } = handlerWith();
  const environment = env();
  const cookie = await signedIn(handler, environment);
  const csrf = (await (await handler.fetch(
    new Request(`${ORIGIN}/api/room/turns`, { headers: { cookie } }), environment, {})).json()).csrf_token;
  const body = JSON.stringify({ body: "hi" });

  const cases = [
    [415, { cookie, "content-type": "text/plain", origin: ORIGIN, "sec-fetch-site": "same-origin", "x-carr-csrf": csrf }],
    [403, { cookie, "content-type": "application/json", origin: "https://evil.example", "sec-fetch-site": "same-origin", "x-carr-csrf": csrf }],
    [403, { cookie, "content-type": "application/json", origin: ORIGIN, "sec-fetch-site": "cross-site", "x-carr-csrf": csrf }],
    [403, { cookie, "content-type": "application/json", origin: ORIGIN, "sec-fetch-site": "same-origin", "x-carr-csrf": "wrong-token-value-here" }],
  ];
  for (const [status, headers] of cases) {
    const response = await handler.fetch(new Request(`${ORIGIN}/api/room/turn`, { method: "POST", headers, body }), environment, {});
    assert.equal(response.status, status, JSON.stringify(headers));
  }
  assert.equal(calls.writes.length, 0, "not one refused request reached the wire");

  const wrongMethod = await handler.fetch(new Request(`${ORIGIN}/api/room/turns`, {
    method: "POST", headers: postHeaders(cookie, csrf), body }), environment, {});
  assert.equal(wrongMethod.status, 405);
});

test("the room's 20,000-character body cap is enforced server-side, on characters", async () => {
  const { handler, calls } = handlerWith();
  const environment = env();
  const cookie = await signedIn(handler, environment);
  const csrf = (await (await handler.fetch(
    new Request(`${ORIGIN}/api/room/turns`, { headers: { cookie } }), environment, {})).json()).csrf_token;

  const atCap = await handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(cookie, csrf), body: JSON.stringify({ body: "x".repeat(ROOM_BODY_MAX) }),
  }), environment, {});
  assert.equal(atCap.status, 200, "exactly at the cap is allowed, as the verb allows it");

  const over = await handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(cookie, csrf), body: JSON.stringify({ body: "x".repeat(ROOM_BODY_MAX + 1) }),
  }), environment, {});
  assert.equal(over.status, 413);
  assert.deepEqual(await over.json(), { error: "body_too_long", limit: ROOM_BODY_MAX, got: ROOM_BODY_MAX + 1 });

  const empty = await handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(cookie, csrf), body: JSON.stringify({ body: "   " }),
  }), environment, {});
  assert.equal(empty.status, 400);
  assert.equal(calls.writes.length, 1, "only the at-cap post landed");
});

test("the RECONNECT control is an allowlist, and its receipt body is rebuilt rather than echoed", async () => {
  assert.deepEqual(roomControlTurn({ action: "login", desk: "joe-desk" }),
    { kind: "receipt", body: '{"control":{"action":"login","desk":"joe-desk"}}' });
  // extra keys are dropped, never carried onto the wire
  assert.deepEqual(roomControlTurn({ action: "login", desk: "joe-desk", cmd: "rm -rf /", seat: "claude" }),
    { kind: "receipt", body: '{"control":{"action":"login","desk":"joe-desk"}}' });
  for (const control of [null, {}, { action: "logout", desk: "joe-desk" }, { action: "login" },
    { action: "login", desk: "../etc/passwd" }, { action: "login", desk: "Joe Desk" },
    { action: "login", desk: "x".repeat(60) }, "login"]) {
    assert.equal(roomControlTurn(control), null, JSON.stringify(control));
  }

  const { handler, calls } = handlerWith();
  const environment = env();
  const cookie = await signedIn(handler, environment);
  const csrf = (await (await handler.fetch(
    new Request(`${ORIGIN}/api/room/turns`, { headers: { cookie } }), environment, {})).json()).csrf_token;

  const ok = await handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(cookie, csrf),
    body: JSON.stringify({ control: { action: "login", desk: "joe-desk" }, seat: "hermes" }),
  }), environment, {});
  assert.equal(ok.status, 200);
  assert.deepEqual(
    { seat: calls.writes[0].seat, kind: calls.writes[0].kind, sponsor: calls.writes[0].sponsor, body: calls.writes[0].body },
    { seat: "human", kind: "receipt", sponsor: "joe", body: '{"control":{"action":"login","desk":"joe-desk"}}' });

  const refused = await handler.fetch(new Request(`${ORIGIN}/api/room/turn`, {
    method: "POST", headers: postHeaders(cookie, csrf),
    body: JSON.stringify({ control: { action: "shell", desk: "joe-desk" } }),
  }), environment, {});
  assert.equal(refused.status, 400);
  assert.deepEqual(await refused.json(), { error: "control_not_allowed" });
  assert.equal(calls.writes.length, 1);
});

test("a wire that cannot be reached fails as a wire outage, not as a broken page", async () => {
  const { handler } = handlerWith({
    roomReadFn: async () => { throw new Error("connection refused"); },
  });
  const environment = env();
  const cookie = await signedIn(handler, environment);
  const response = await handler.fetch(new Request(`${ORIGIN}/api/room/turns`, { headers: { cookie } }), environment, {});
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "wire_unavailable");
});
