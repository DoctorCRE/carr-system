import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createContext, runInContext } from "node:vm";
import { fileURLToPath } from "node:url";
import { createReportsWebHandler, REPORTS_ORIGIN } from "../src/reports-web.js";
import { createTourInternalWebHandler } from "../src/tour-internal-web.js";
import { tourSharingBrowserAccess, tourSharingTools } from "../src/tour-sharing.js";

// V0 slice 5: a client shortlists and comments inside a shared Tour, and the
// broker reads it back tied to the exact projection. Synthetic values only.
// The database here is a small in-memory stand-in for the SQL functions in
// migrations/0749 (grant, expiry, membership, idempotency). It proves the
// browser, the reports adapter and the verbs agree with each other; it does
// not prove the SQL, which needs the live measurement in the PR body.

const SHARE_JS = fileURLToPath(new URL("../../dealroom/reports/share.js", import.meta.url));
const SHARE_HTML = fileURLToPath(new URL("../../dealroom/reports/share.html", import.meta.url));
const APP_JS = fileURLToPath(new URL("../../dealroom/tours/app.js", import.meta.url));
const TOURS_HTML = fileURLToPath(new URL("../../dealroom/tours/index.html", import.meta.url));

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const PROJECTION_ID = "10000000-0000-4000-8000-000000000001";
const PROJECTION_REF = `projection:public:${"p".repeat(32)}`;
const OTHER_PROJECTION_REF = `projection:public:${"q".repeat(32)}`;
const PROP_A = `property:public:${"a".repeat(32)}`;
const PROP_B = `property:public:${"b".repeat(32)}`;
const PROP_OTHER_TOUR = `property:public:${"z".repeat(32)}`;
const TOKEN = "T".repeat(43);
const digestOf = async value => {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
  return "sha256:" + [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
};

function store({ scopes = ["view_packet", "shortlist", "comment"] } = {}) {
  const db = {
    now: Date.parse("2026-10-01T12:00:00Z"),
    grant: { projection_ref: PROJECTION_REF, scopes, expiresAt: Date.parse("2026-10-08T12:00:00Z"), status: "active", members: new Set([PROP_A, PROP_B]) },
    tokenDigest: null, sessions: new Map(), writes: new Map(), shortlist: new Map(), comments: [], calls: [],
  };
  const live = digest => {
    const session = db.sessions.get(digest);
    return session && db.grant.status === "active" && db.now < db.grant.expiresAt && db.now < session.expiresAt ? db.grant : null;
  };
  const client = { async query(sql, params) {
    db.calls.push({ sql, params });
    const seam = /ops\.(\w+)\(/.exec(sql)?.[1];
    if (seam === "exchange_tour_share_session") return { rows: [{ exchange: { ok: true, permission_scopes: db.grant.scopes, expires_at: new Date(db.grant.expiresAt).toISOString() } }] };
    const [sd] = params;
    const grant = live(sd);
    if (seam === "read_tour_share_feedback")
      return { rows: [{ feedback: grant ? { projection_ref: grant.projection_ref, permission_scopes: grant.scopes.filter(s => s !== "view_packet"), items: [...grant.members].map(property_ref => ({ property_ref })) } : null }] };
    if (seam === "write_tour_share_shortlist" || seam === "write_tour_share_comment") {
      const kind = seam.endsWith("shortlist") ? "shortlist" : "comment";
      const [, projectionRef, propertyRef, value, key] = params;
      if (!grant || !grant.scopes.includes(kind) || projectionRef !== grant.projection_ref || !grant.members.has(propertyRef)) return { rows: [{ feedback: null }] };
      const fingerprint = JSON.stringify([kind, projectionRef, propertyRef, value]);
      if (db.writes.has(key)) return { rows: [{ feedback: db.writes.get(key) === fingerprint ? { saved: true } : null }] };
      db.writes.set(key, fingerprint);
      if (kind === "shortlist") db.shortlist.set(propertyRef, value);
      else db.comments.push({ propertyRef, comment: value });
      return { rows: [{ feedback: { saved: true } }] };
    }
    if (seam === "read_tour_feedback") {
      const [tenant, projectionId, actorId] = params;
      if (tenant !== "carr-internal" || projectionId !== PROJECTION_ID || !actorId) return { rows: [{ feedback: null }] };
      return { rows: [{ feedback: { projection_id: PROJECTION_ID, share_grant_id: "secret-grant", items: [...db.grant.members].map((property_ref, i) => ({
        property_ref, route_label: String.fromCharCode(65 + i), shortlisted: db.shortlist.has(property_ref) ? db.shortlist.get(property_ref) : null, broker_notes: "internal note", token_digest: "leak",
        comments: db.comments.filter(c => c.propertyRef === property_ref).map((c, n) => ({ comment_ref: `comment:public:${String(n).padStart(32, "c")}`, comment: c.comment, created_at: "2026-10-01T12:00:00Z" })),
      })) } }] };
    }
    throw new Error(`unexpected sql ${sql}`);
  } };
  return { db, client };
}

function wire(env = store()) {
  const browser = tourSharingBrowserAccess({ ToolError });
  const adapter = fn => async args => {
    try { const r = await fn(env.client, args); return r.ok ? { ok: true, data: r.feedback ?? r.packet ?? r.map } : { ok: false, status: 404 }; }
    catch (error) { if (error instanceof ToolError && error.payload?.error === "tour_share_access_refused") return { ok: false, status: 404 }; throw error; }
  };
  const sessionArg = ({ sessionDigest, env: _env, ctx: _ctx, ...rest }) => ({ session_digest: sessionDigest, ...rest });
  const surface = createReportsWebHandler({
    now: () => env.db.now,
    exchangeShareTokenFn: async ({ tokenDigest, sessionDigest, sessionExpiresAt }) => {
      if (tokenDigest !== env.db.tokenDigest || env.db.grant.status !== "active" || env.db.now >= env.db.grant.expiresAt) return { ok: false, status: 403 };
      env.db.sessions.set(sessionDigest, { expiresAt: Math.min(Date.parse(sessionExpiresAt), env.db.grant.expiresAt) });
      return { ok: true };
    },
    readShareFn: async ({ sessionDigest }) => env.db.sessions.has(sessionDigest) && env.db.grant.status === "active"
      ? { ok: true, data: { stops: [PROP_A, PROP_B].map((property_ref, i) => ({ property_ref, route_sequence: i + 1, route_label: String.fromCharCode(65 + i), name: `Fixture property ${i + 1}`, address: `${i + 1} Test Way` })) } }
      : { ok: false, status: 404 },
    readMapFn: async () => ({ ok: false, status: 404 }),
    readFeedbackFn: adapter((c, a) => browser.readFeedback(c, sessionArg(a))),
    shortlistFn: adapter((c, a) => browser.shortlist(c, sessionArg(a))),
    commentFn: adapter((c, a) => browser.comment(c, sessionArg(a))),
  });
  return { surface, env };
}

// --- a just-enough DOM for share.js ---------------------------------------
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.parent = null; this.listeners = {}; this.attrs = {}; this.hidden = false; this.disabled = false; this.value = ""; this.dataset = {}; this.className = ""; this.textContent = ""; }
  append(...nodes) { for (const n of nodes) { n.parent = this; this.children.push(n); } }
  replaceChildren() { this.children = []; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  async click() { for (const fn of this.listeners.click || []) await fn({}); }
  async key(key) { for (const fn of this.listeners.keydown || []) await fn({ key, preventDefault() {} }); }
  all(test, out = []) { for (const c of this.children) { if (test(c)) out.push(c); c.all(test, out); } return out; }
  querySelectorAll(selector) { const cls = selector.slice(1); return this.all(c => c.className.split(" ").includes(cls)); }
}
function pageFor(surface, env) {
  const ids = {};
  const doc = {
    querySelector(selector) { return ids[selector] ||= new El(selector); },
    createElement(tag) { return new El(tag); },
  };
  const jar = { cookie: "" };
  const trace = [];
  // Advance browser deadlines explicitly. Real crypto and request handling
  // must not race a compressed wall-clock timeout on a loaded CI runner.
  let clockMs = 0, nextTimer = 0;
  const timers = new Map();
  const clock = {
    advance(ms) {
      clockMs += ms;
      for (const [id, timer] of timers) {
        if (timer.at > clockMs) continue;
        timers.delete(id);
        timer.fn();
      }
    },
  };
  const fetchBridge = async (path, options = {}) => {
    const headers = { origin: REPORTS_ORIGIN, "sec-fetch-site": "same-origin", ...(options.headers || {}), ...(jar.cookie ? { cookie: jar.cookie } : {}) };
    trace.push({ path, method: options.method || "GET", body: options.body });
    const call = () => surface.fetch(new Request(`${REPORTS_ORIGIN}${path}`, { method: options.method || "GET", headers, body: options.body }), {});
    const hook = env.intercept?.[path];
    const response = hook ? await hook(call, options) : await call();
    const set = response.headers.get("set-cookie");
    if (set) jar.cookie = set.split(";")[0];
    return response;
  };
  const context = createContext({ document: doc, fetch: fetchBridge, crypto, Promise, JSON, Number, String, Array, RegExp, Error, TypeError, Math, Date, URL, AbortController,
    setTimeout: (fn, ms) => { const id = ++nextTimer; timers.set(id, { at: clockMs + ms, fn }); return id; },
    clearTimeout: id => timers.delete(id) });
  context.globalThis = context;
  context.__CARR_TOUR_TAKE_SHARE_TOKEN__ = () => TOKEN;
  return { doc, ids, jar, trace, context, clock };
}
async function openShare(w, { waitFeedback = true } = {}) {
  runInContext(await readFile(SHARE_JS, "utf8"), w.context);
  await w.doc.querySelector("#open-tour").click();
  const list = w.doc.querySelector("#report-list");
  const settled = () => list.attrs["aria-busy"] === "false" && (!waitFeedback || (list.dataset.feedbackState && list.dataset.feedbackState !== "loading"));
  for (let i = 0; i < 400 && !settled(); i += 1) await new Promise(resolve => setTimeout(resolve, 5));
  assert.ok(settled(), "share page settled");
  return list;
}
const until = async (check, what) => { for (let i = 0; i < 400 && !check(); i += 1) await new Promise(resolve => setTimeout(resolve, 5)); assert.ok(check(), what); };
const controls = row => ({ pick: row.all(c => c.className === "shortlist-toggle")[0], field: row.all(c => c.className === "comment-field")[0], send: row.all(c => c.className === "comment-send")[0], note: row.all(c => c.className.includes("feedback-note"))[0] });

async function setup(options) {
  const env = store(options);
  env.db.tokenDigest = await digestOf(TOKEN);
  const { surface } = wire(env);
  const w = pageFor(surface, env);
  return { env, w, surface };
}

test("client shortlists and comments; broker reads the same items tied to the exact projection", async () => {
  const { env, w } = await setup();
  const list = await openShare(w);
  const rows = list.children;
  assert.equal(rows.length, 2);
  const a = controls(rows[0]), b = controls(rows[1]);
  assert.ok(a.pick && a.field && a.send, "both scopes render controls");
  await a.pick.click();
  assert.equal(a.pick.attrs["aria-pressed"], "true");
  a.field.value = "  Good frontage  ";
  await a.send.click();
  assert.equal(a.field.value, "", "saved comment clears the field");
  await b.pick.click(); await b.pick.click();
  assert.equal(b.pick.attrs["aria-pressed"], "false");

  const bodies = w.trace.filter(t => t.method === "POST" && t.path !== "/api/share/exchange").map(t => JSON.parse(t.body));
  assert.ok(bodies.every(x => x.projection_ref === PROJECTION_REF));
  assert.doesNotMatch(JSON.stringify(bodies), /session|token|grant|projection_id|tenant|actor/i);
  assert.doesNotMatch(JSON.stringify(w.trace.filter(t => t.path !== "/api/share/exchange")), new RegExp(TOKEN), "bearer is sent once, in the exchange body only");

  // Broker side: authenticated internal surface, verb reads the same store.
  const tools = tourSharingTools({ ToolError, withEnvelope: async (_c, _a, _v, _x, fn) => fn(), writeEvent: async () => {} });
  const brokerActor = { id: "broker", organization_tenant_id: "tenant-one" };
  const internal = createTourInternalWebHandler({
    readFeedbackFn: async ({ input }) => { { const r = await tools["read-tour-feedback"].handler(env.client, brokerActor, { projection_id: input.projection_id, cursor: null, limit: 100 }); return { ok: true, data: { feedback: r.feedback } }; } },
  });
  const session = { key: "opaque", csrfToken: "csrf" };
  const origin = "https://app.doctorcre.com";
  const get = () => internal.fetch(new Request(`${origin}/api/tours/feedback?projection_id=${PROJECTION_ID}`), { APP_HOST: "app.doctorcre.com" }, {}, brokerActor, session);
  assert.equal((await internal.fetch(new Request(`${origin}/api/tours/feedback?projection_id=${PROJECTION_ID}`), { APP_HOST: "app.doctorcre.com" }, {}, undefined, undefined)).status, 401);
  const response = await get();
  assert.equal(response.status, 200);
  const { feedback } = (await response.json()).data;
  assert.equal(feedback.projection_id, PROJECTION_ID);
  const byRef = Object.fromEntries(feedback.items.map(i => [i.property_ref, i]));
  assert.equal(byRef[PROP_A].shortlisted, true);
  assert.equal(byRef[PROP_B].shortlisted, false, "explicit no stays distinct from no answer");
  assert.deepEqual(byRef[PROP_A].comments.map(c => c.comment), ["Good frontage"]);
  assert.doesNotMatch(JSON.stringify(feedback), /broker_notes|internal note|token_digest|secret-grant|share_grant_id/);
});

test("controls appear only for scopes granted and only when the grant lists the property", async () => {
  const shortlistOnly = await setup({ scopes: ["view_packet", "shortlist"] });
  const rows = (await openShare(shortlistOnly.w)).children;
  const c = controls(rows[0]);
  assert.ok(c.pick); assert.equal(c.field, undefined);

  const readOnly = await setup({ scopes: ["view_packet"] });
  const rows2 = (await openShare(readOnly.w)).children;
  assert.equal(rows2.length, 2);
  assert.equal(controls(rows2[0]).pick, undefined, "packet-only grant has no feedback controls");
  assert.equal(readOnly.w.trace.filter(t => t.method === "POST" && t.path !== "/api/share/exchange").length, 0);
});

test("a stale page cannot write another Tour's property or projection", async () => {
  const { env, w, surface } = await setup();
  await openShare(w);
  const post = (path, body) => surface.fetch(new Request(`${REPORTS_ORIGIN}${path}`, { method: "POST", headers: { origin: REPORTS_ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json", cookie: w.jar.cookie }, body: JSON.stringify(body) }), {});
  const key = "30000000-0000-4000-8000-000000000001";
  assert.equal((await post("/api/share/shortlist", { projection_ref: PROJECTION_REF, property_ref: PROP_OTHER_TOUR, shortlisted: true, idempotency_key: key })).status, 404);
  assert.equal((await post("/api/share/comment", { projection_ref: OTHER_PROJECTION_REF, property_ref: PROP_A, comment: "x", idempotency_key: key })).status, 404);
  assert.equal(env.db.shortlist.size, 0);
  assert.equal(env.db.comments.length, 0);
});

test("a dropped response retries with the same idempotency key and writes once; a new action gets a new key", async () => {
  const { env, w } = await setup();
  const list = await openShare(w);
  const a = controls(list.children[0]);
  a.field.value = "Parking looks tight";
  // The request commits on the server, then the browser sees a 503.
  const original = env.client.query;
  let failOnce = true;
  env.client.query = async (sql, params) => { const r = await original.call(env.client, sql, params); if (failOnce && sql.includes("write_tour_share_comment")) { failOnce = false; throw new Error("connection lost after commit"); } return r; };
  await a.send.click();
  assert.match(a.note.textContent, /Try again/);
  assert.equal(a.field.value, "Parking looks tight", "unsent comment is kept for retry");
  await a.send.click();
  assert.equal(a.field.value, "");
  const commentPosts = w.trace.filter(t => t.path === "/api/share/comment").map(t => JSON.parse(t.body));
  assert.equal(commentPosts.length, 2);
  assert.equal(commentPosts[0].idempotency_key, commentPosts[1].idempotency_key, "retry replays the same key");
  assert.equal(env.db.comments.length, 1, "server wrote once");
  a.field.value = "Parking looks tight";
  await a.send.click();
  const third = w.trace.filter(t => t.path === "/api/share/comment").map(t => JSON.parse(t.body)).at(-1);
  assert.notEqual(third.idempotency_key, commentPosts[0].idempotency_key, "a new action after success uses a fresh key");
  assert.equal(env.db.comments.length, 2);
});

test("double activation while a write is in flight sends one request", async () => {
  const { w } = await setup();
  const list = await openShare(w);
  const a = controls(list.children[0]);
  await Promise.all([a.pick.click(), a.pick.click()]);
  assert.equal(w.trace.filter(t => t.path === "/api/share/shortlist").length, 1);
});

test("expired and revoked grants refuse writes and remove the controls", async () => {
  for (const change of [env => { env.db.now = env.db.grant.expiresAt + 1000; }, env => { env.db.grant.status = "revoked"; }]) {
    const { env, w } = await setup();
    const list = await openShare(w);
    const a = controls(list.children[0]);
    change(env);
    await a.pick.click();
    assert.equal(env.db.shortlist.size, 0);
    assert.match(a.note.textContent, /no longer active/);
    assert.equal(list.children[0].all(c => c.className === "feedback").length, 0, "controls removed");
  }
});

test("a new browser cannot reuse a session after the grant is revoked, and the bearer cannot be reused after expiry", async () => {
  const { env, w, surface } = await setup();
  await openShare(w);
  env.db.grant.status = "revoked";
  const read = await surface.fetch(new Request(`${REPORTS_ORIGIN}/api/share/feedback`, { headers: { cookie: w.jar.cookie } }), {});
  assert.equal(read.status, 404);
  const exchange = await surface.fetch(new Request(`${REPORTS_ORIGIN}/api/share/exchange`, { method: "POST", headers: { origin: REPORTS_ORIGIN, "sec-fetch-site": "same-origin", "content-type": "application/json" }, body: JSON.stringify({ token: TOKEN }) }), {});
  assert.equal(exchange.status, 403);
});

test("scanner-safe bootstrap: nothing is exchanged or written until the person selects Open tour", async () => {
  const { w } = await setup();
  runInContext(await readFile(SHARE_JS, "utf8"), w.context);
  assert.equal(w.trace.length, 0, "loading the page makes no request, so a link scanner cannot start a session");
  assert.equal(w.doc.querySelector("#open-tour").disabled, false);
});

test("share.js has no pdf or reaction route and never stores the session or token", async () => {
  const [script, html] = await Promise.all([readFile(SHARE_JS, "utf8"), readFile(SHARE_HTML, "utf8")]);
  assert.match(script, /\/api\/share\/\$\{kind\}/);
  assert.doesNotMatch(script + html, /\/api\/share\/(?:pdf|reaction)|localStorage|sessionStorage|document\.cookie/);
  assert.doesNotMatch(html, /requires? a future governed permission-scope amendment/);
});

test("broker Tours page can issue the two feedback scopes and shows feedback for the approved projection", async () => {
  const [app, html] = await Promise.all([readFile(APP_JS, "utf8"), readFile(TOURS_HTML, "utf8")]);
  assert.match(html, /name="scope" value="shortlist"/);
  assert.match(html, /name="scope" value="comment"/);
  assert.match(html, /id="feedback-list"/);
  assert.match(app, /\/api\/tours\/feedback\?projection_id=/);
  assert.match(app, /textContent/);
  assert.doesNotMatch(app, /innerHTML/);
});

test("broker door: feedback scopes need packet access and unknown scopes stay refused", async () => {
  const env = { APP_HOST: "app.doctorcre.com" };
  const session = { key: "opaque", csrfToken: "csrf" };
  const actor = { id: "broker" };
  const seen = [];
  const surface = createTourInternalWebHandler({ issueShareGrantFn: async ({ input }) => { seen.push(input.permission_scopes); return { ok: true, data: { share_grant_id: "66666666-6666-4666-8666-666666666666" } }; } });
  const body = scopes => ({ projection_id: PROJECTION_ID, token_digest: `sha256:${"a".repeat(64)}`, permission_scopes: scopes, expires_at: "2026-10-08T12:00:00.000Z", receipt_digest: `sha256:${"b".repeat(64)}`, idempotency_key: "40000000-0000-4000-8000-000000000001" });
  const post = scopes => surface.fetch(new Request("https://app.doctorcre.com/api/tours/share/issue", { method: "POST", headers: { origin: "https://app.doctorcre.com", "sec-fetch-site": "same-origin", "content-type": "application/json", "x-carr-csrf": "csrf" }, body: JSON.stringify(body(scopes)) }), env, {}, actor, session);
  for (const ok of [["view_packet", "shortlist", "comment"], ["view_packet", "view_map", "shortlist", "comment"], ["view_packet", "comment"], ["view_packet"], ["view_map"]])
    assert.equal((await post(ok)).status, 200, ok.join());
  for (const bad of [["shortlist"], ["comment"], ["view_map", "shortlist"], ["view_packet", "edit_notes"], ["view_packet", "shortlist", "shortlist"], []])
    assert.equal((await post(bad)).status, 400, bad.join());
  assert.equal(seen.length, 5);
});

test("broker feedback route is GET only, authenticated, one projection id, and coarse on failure", async () => {
  const env = { APP_HOST: "app.doctorcre.com" };
  const session = { key: "opaque", csrfToken: "csrf" };
  const surface = createTourInternalWebHandler({ readFeedbackFn: async () => { throw new Error("connect ECONNRESET db.internal.example:5432"); } });
  const url = suffix => new Request(`https://app.doctorcre.com/api/tours/feedback${suffix}`);
  assert.equal((await surface.fetch(url(`?projection_id=${PROJECTION_ID}`), env, {}, undefined, undefined)).status, 401);
  assert.equal((await surface.fetch(url(""), env, {}, { id: "b" }, session)).status, 400);
  assert.equal((await surface.fetch(url(`?projection_id=${PROJECTION_ID}&tenant_id=x`), env, {}, { id: "b" }, session)).status, 400);
  assert.equal((await surface.fetch(new Request("https://app.doctorcre.com/api/tours/feedback", { method: "POST" }), env, {}, { id: "b" }, session)).status, 405);
  const failed = await surface.fetch(url(`?projection_id=${PROJECTION_ID}`), env, {}, { id: "b" }, session);
  assert.equal(failed.status, 503);
  assert.doesNotMatch(await failed.text(), /ECONNRESET|db\.internal/);
});

// ---------------------------------------------------------------------------
// Review round 1 (PR #1444): client behaviors
// ---------------------------------------------------------------------------
const posts = (w, path) => w.trace.filter(t => t.path === path).map(t => JSON.parse(t.body));
const deferred = () => { let release; const gate = new Promise(resolve => { release = resolve; }); return { gate, release }; };

test("a slow save acknowledgement keeps a newer draft, and a second send is refused while one is in flight", async () => {
  const { env, w } = await setup();
  const list = await openShare(w);
  const a = controls(list.children[0]);
  const hold = deferred();
  env.intercept = { "/api/share/comment": async call => { const r = await call(); await hold.gate; return r; } };
  a.field.value = "First thought";
  const first = a.send.click();
  await until(() => posts(w, "/api/share/comment").length === 1, "first comment sent");
  a.field.value = "Second thought";
  await a.send.click();
  assert.equal(posts(w, "/api/share/comment").length, 1, "no overlapping send for the same control");
  assert.match(a.note.textContent, /Still saving/);
  hold.release(); await first;
  assert.equal(a.field.value, "Second thought", "newer draft survives the first acknowledgement");
  assert.match(a.note.textContent, /newer text is still in the box/);
  assert.deepEqual(env.db.comments.map(c => c.comment), ["First thought"]);
  env.intercept = {};
  await a.send.click();
  assert.deepEqual(env.db.comments.map(c => c.comment), ["First thought", "Second thought"]);
  assert.equal(a.field.value, "");
});

test("an idempotency key names one action: X uncertain, Y saved, X again gets a fresh key; an immediate retry keeps its key", async () => {
  const { env, w } = await setup();
  const list = await openShare(w);
  const a = controls(list.children[0]);
  let lose = true;
  env.intercept = { "/api/share/comment": async call => { const r = await call(); if (lose) { lose = false; return new Response("{}", { status: 503 }); } return r; } };
  a.field.value = "X"; await a.send.click();
  assert.match(a.note.textContent, /Not confirmed/);
  a.field.value = "Y"; await a.send.click();
  a.field.value = "X"; await a.send.click();
  const keys = posts(w, "/api/share/comment").map(p => p.idempotency_key);
  assert.equal(new Set(keys).size, 3, "three distinct actions, three keys");
  assert.deepEqual(env.db.comments.map(c => c.comment), ["X", "Y", "X"], "the later X is stored, not silently dropped");

  lose = true; a.field.value = "Z"; await a.send.click(); await a.send.click();
  const retry = posts(w, "/api/share/comment").slice(-2).map(p => p.idempotency_key);
  assert.equal(retry[0], retry[1], "explicit retry of the same text replays the same key");
  assert.equal(env.db.comments.filter(c => c.comment === "Z").length, 1);
});

test("an HTTP 200 without a saved acknowledgement is an unknown outcome: nothing changes and the key is kept", async () => {
  for (const bad of [() => new Response("{}", { status: 200 }), () => new Response("not json", { status: 200 }), () => new Response(JSON.stringify({ error: "x" }), { status: 200 }), () => new Response(JSON.stringify({ data: { saved: false } }), { status: 200 })]) {
    const { env, w } = await setup();
    const list = await openShare(w);
    const a = controls(list.children[0]);
    let broken = true;
    env.intercept = { "/api/share/shortlist": async call => { const r = await call(); return broken ? bad() : r; }, "/api/share/comment": async call => { const r = await call(); return broken ? bad() : r; } };
    a.field.value = "Keep me";
    await a.pick.click(); await a.send.click();
    assert.equal(a.pick.attrs["aria-pressed"], "false", "no success shown");
    assert.equal(a.field.value, "Keep me", "comment kept");
    assert.match(a.note.textContent, /Not confirmed/);
    broken = false;
    await a.pick.click(); await a.send.click();
    for (const path of ["/api/share/shortlist", "/api/share/comment"]) {
      const sent = posts(w, path).map(p => p.idempotency_key);
      assert.equal(sent[0], sent[1], `${path} retry reuses the original key`);
    }
    assert.equal(a.pick.attrs["aria-pressed"], "true");
    assert.equal(env.db.comments.length, 1);
  }
});

test("comments are one line: newlines and control characters are refused in the page with a clear message, Enter sends", async () => {
  const { env, w } = await setup();
  const list = await openShare(w);
  const a = controls(list.children[0]);
  for (const bad of ["line one\nline two", "tab\there", "bell\u0007"]) {
    a.field.value = bad; await a.send.click();
    assert.match(a.note.textContent, /one line/);
    assert.equal(a.field.value, bad, "draft kept");
  }
  assert.equal(posts(w, "/api/share/comment").length, 0, "nothing reaches the API");
  a.field.value = "Enter sends this"; await a.field.key("Enter");
  assert.deepEqual(env.db.comments.map(c => c.comment), ["Enter sends this"]);
  assert.equal(a.field.tag, "input");
  assert.equal(a.field.type, "text");
});

test("the feedback read never gates the packet: pending, timeout and 503 leave the report usable and say so", async () => {
  for (const [name, intercept, expectMessage] of [
    ["pending forever", { "/api/share/feedback": () => new Promise(() => {}) }, true],
    ["503", { "/api/share/feedback": async () => new Response("{}", { status: 503 }) }, true],
    ["malformed 200", { "/api/share/feedback": async () => new Response(JSON.stringify({ data: { permission_scopes: ["comment"] } }), { status: 200 }) }, true],
    ["no feedback scope on this link", {}, false],
  ]) {
    const { env, w } = await setup({ scopes: name.startsWith("no feedback") ? ["view_packet"] : undefined });
    env.intercept = intercept;
    let feedbackSignal;
    if (name === "pending forever") env.intercept["/api/share/feedback"] = (_call, options) => {
      feedbackSignal = options.signal;
      return new Promise(() => {});
    };
    const list = await openShare(w, { waitFeedback: name !== "pending forever" });
    assert.equal(list.children.length, 2, `${name}: packet rendered`);
    if (name === "pending forever") {
      assert.equal(list.dataset.feedbackState, "loading");
      assert.equal(feedbackSignal.aborted, false);
      w.clock.advance(7999);
      assert.equal(list.dataset.feedbackState, "loading", "deadline has not elapsed");
      w.clock.advance(1);
      await until(() => list.dataset.feedbackState === "unavailable", "feedback deadline reported");
      assert.equal(feedbackSignal.aborted, true);
      assert.equal(list.children.length, 2, "packet remains usable after timeout");
    }
    assert.equal(posts(w, "/api/share/exchange").length, 1);
    assert.ok(w.trace.some(t => t.path === "/api/share/report") && w.trace.some(t => t.path === "/api/share/map"), `${name}: report and map were requested`);
    const message = w.doc.querySelector("#feedback-status").textContent;
    if (expectMessage) {
      assert.equal(list.dataset.feedbackState, "unavailable", name);
      assert.match(message, /unavailable/);
      assert.equal(w.doc.querySelector("#retry-feedback").hidden, false);
      assert.equal(controls(list.children[0]).pick, undefined);
    } else {
      assert.equal(list.dataset.feedbackState, "none");
      assert.equal(message, "", "a link with no feedback scope is silent");
      assert.equal(w.doc.querySelector("#retry-feedback").hidden, true);
    }
  }
});

test("a delayed feedback retry waits for its response without a shortened fixture deadline", async () => {
  const { env, w } = await setup();
  env.intercept = { "/api/share/feedback": async () => new Response("{}", { status: 503 }) };
  const list = await openShare(w);
  assert.equal(list.dataset.feedbackState, "unavailable");
  const hold = deferred();
  env.intercept = { "/api/share/feedback": async call => { await hold.gate; return call(); } };
  await w.doc.querySelector("#retry-feedback").click();
  // A delayed response is still before the browser's deadline. Scheduler load
  // must not turn it into a timeout in the fixture.
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(list.dataset.feedbackState, "loading");
  hold.release();
  await until(() => list.dataset.feedbackState === "ready", "delayed feedback ready after retry");
  assert.ok(controls(list.children[0]).pick);
  w.clock.advance(8000);
  assert.equal(list.dataset.feedbackState, "ready", "completed feedback clears its deadline");
});

test("a failed feedback read can be retried and then works", async () => {
  const { env, w } = await setup();
  let down = true;
  let release, retrySignal;
  const responseReady = new Promise(resolve => { release = resolve; });
  env.intercept = { "/api/share/feedback": async (call, options) => {
    if (down) return new Response("{}", { status: 503 });
    retrySignal = options.signal;
    await responseReady;
    return call();
  } };
  const list = await openShare(w);
  assert.equal(list.dataset.feedbackState, "unavailable");
  down = false;
  await w.doc.querySelector("#retry-feedback").click();
  w.clock.advance(7999);
  assert.equal(list.dataset.feedbackState, "loading", "healthy retry can remain pending before the deadline");
  assert.equal(retrySignal.aborted, false);
  release();
  await until(() => list.dataset.feedbackState === "ready", "feedback ready after retry");
  w.clock.advance(1);
  assert.equal(retrySignal.aborted, false, "successful read cancels its deadline");
  assert.ok(controls(list.children[0]).pick);
  assert.equal(w.doc.querySelector("#retry-feedback").hidden, true);
});

test("a pending feedback request aborts exactly when the browser deadline advances", async () => {
  const { env, w } = await setup();
  let signal;
  env.intercept = { "/api/share/feedback": async (_call, options) => {
    signal = options.signal;
    return new Promise(() => {});
  } };
  const list = await openShare(w, { waitFeedback: false });
  await until(() => Boolean(signal), "feedback request started");
  w.clock.advance(7999);
  assert.equal(signal.aborted, false, "request remains live before its deadline");
  assert.equal(list.children.length, 2, "packet remains available during feedback read");
  w.clock.advance(1);
  assert.equal(signal.aborted, true, "deadline aborts without waiting for wall-clock time");
  await until(() => list.dataset.feedbackState === "unavailable", "timeout shown");
  assert.equal(list.children.length, 2, "timeout preserves the packet");
});

test("rotation: the old link stops working for writes and a new link gets fresh, working controls", async () => {
  const { env, w, surface } = await setup();
  const list = await openShare(w);
  const a = controls(list.children[0]);
  await a.pick.click();
  assert.equal(env.db.shortlist.get(PROP_A), true);
  // Rotate: a new bearer is issued, the old grant and its sessions stop.
  const NEW_TOKEN = "N".repeat(43);
  env.db.tokenDigest = await digestOf(NEW_TOKEN);
  env.db.sessions.clear();
  await a.pick.click();
  assert.match(a.note.textContent, /no longer active/);
  assert.equal(env.db.shortlist.get(PROP_A), true, "old page changed nothing");
  const fresh = pageFor(surface, env);
  fresh.context.__CARR_TOUR_TAKE_SHARE_TOKEN__ = () => NEW_TOKEN;
  const list2 = await openShare(fresh);
  const b = controls(list2.children[1]);
  await b.pick.click();
  assert.equal(env.db.shortlist.get(PROP_B), true);
});

test("share.html names its live regions and retry control", async () => {
  const html = await readFile(SHARE_HTML, "utf8");
  assert.match(html, /id="feedback-status"[^>]*role="status"/);
  assert.match(html, /id="retry-feedback"[^>]*hidden/);
});

// ---------------------------------------------------------------------------
// Review round 1 (PR #1444): broker Tours page, run for real against a fake DOM
// ---------------------------------------------------------------------------
const TOUR_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", TOUR_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const PROJ_A = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1", PROJ_B = "b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2";
const tourFeedback = (projectionId, label, comments = [], shortlisted = true) => ({ projection_id: projectionId, items: [{ property_ref: PROP_A, route_label: label, shortlisted, comments }] });

async function toursPage(routes) {
  const ids = {};
  const doc = {
    querySelector(selector) { return ids[selector] ||= new El(selector); },
    querySelectorAll() { return []; },
    createElement(tag) { return new El(tag); },
  };
  const ok = data => new Response(JSON.stringify({ data, csrf_token: "csrf" }), { status: 200 });
  const fetchStub = async path => {
    const url = new URL(path, "https://app.doctorcre.com");
    const handler = routes[url.pathname];
    if (!handler) return ok({});
    return handler(url);
  };
  const context = createContext({ document: doc, fetch: fetchStub, crypto, Promise, JSON, Number, String, Array, RegExp, Error, TypeError, Math, Date, URL, TextEncoder, btoa, navigator: {}, Uint8Array, encodeURIComponent, setTimeout, clearTimeout });
  context.globalThis = context;
  runInContext(await readFile(APP_JS, "utf8"), context);
  await until(() => doc.querySelector("#tour-list").children.length === 2, "tour library rendered");
  const pick = index => doc.querySelector("#tour-list").children[index].children[0].click();
  return { doc, ok, pick, panel: () => doc.querySelector("#feedback-list") };
}
const panelText = el => [el.textContent, ...el.all(() => true).map(c => c.textContent)].join(" | ");
const routesFor = ({ feedback, detailGate = {} }) => ({
  "/api/tours/library": () => new Response(JSON.stringify({ data: { tours: [{ id: TOUR_A, name: "Tour A", status: "draft" }, { id: TOUR_B, name: "Tour B", status: "draft" }] }, csrf_token: "csrf" }), { status: 200 }),
  "/api/tours/detail": async url => {
    const tourId = url.searchParams.get("tour_id");
    if (detailGate[tourId]) await detailGate[tourId];
    return new Response(JSON.stringify({ data: { id: tourId, name: tourId === TOUR_A ? "Tour A" : "Tour B", projection_id: tourId === TOUR_A ? PROJ_A : PROJ_B, stops: [] } }), { status: 200 });
  },
  "/api/tours/feedback": url => feedback(url.searchParams.get("projection_id")),
});

test("broker: a slow feedback read for Tour A never shows under Tour B, in either completion order", async () => {
  const gateA = deferred();
  const page = await toursPage(routesFor({ feedback: async projectionId => {
    if (projectionId === PROJ_A) await gateA.gate;
    return new Response(JSON.stringify({ data: { feedback: tourFeedback(projectionId, projectionId === PROJ_A ? "A-ONLY" : "B-ONLY") } }), { status: 200 });
  } }));
  await page.pick(0);          // Tour A selected; its feedback read is held
  await until(() => page.doc.querySelector("#tour-name").textContent === "Tour A", "A heading");
  await page.pick(1);          // Tour B selected and fully loaded
  await until(() => /B-ONLY/.test(panelText(page.panel())), "B feedback shown");
  gateA.release();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(page.doc.querySelector("#tour-name").textContent, "Tour B");
  assert.doesNotMatch(panelText(page.panel()), /A-ONLY/);
  assert.match(panelText(page.panel()), /B-ONLY/);
});

test("broker: a late failure for Tour A cannot replace Tour B's panel, and selection clears the old panel at once", async () => {
  const gateA = deferred(), detailB = deferred();
  const page = await toursPage(routesFor({ detailGate: { [TOUR_B]: detailB.gate }, feedback: async projectionId => {
    if (projectionId === PROJ_A) { await gateA.gate; return new Response("{}", { status: 503 }); }
    return new Response(JSON.stringify({ data: { feedback: tourFeedback(projectionId, "B-ONLY") } }), { status: 200 });
  } }));
  await page.pick(0);
  await until(() => page.doc.querySelector("#tour-name").textContent === "Tour A", "A heading");
  const loadingB = page.pick(1);   // B's detail is held: the panel must not still describe A
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(panelText(page.panel()), /Loading client feedback/);
  detailB.release(); await loadingB;
  await until(() => /B-ONLY/.test(panelText(page.panel())), "B feedback shown");
  gateA.release();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.match(panelText(page.panel()), /B-ONLY/);
  assert.doesNotMatch(panelText(page.panel()), /could not be loaded/);
});

test("broker: a response for a different projection than requested is refused", async () => {
  const page = await toursPage(routesFor({ feedback: async () => new Response(JSON.stringify({ data: { feedback: tourFeedback(PROJ_B, "WRONG-TOUR") } }), { status: 200 }) }));
  await page.pick(0);
  await until(() => /could not be loaded/.test(panelText(page.panel())), "refusal shown");
  assert.doesNotMatch(panelText(page.panel()), /WRONG-TOUR/);
});

test("broker: comments stack vertically in their own container, separate from the answer, and render as text", async () => {
  const long = "word ".repeat(120);
  const comments = ["<img src=x onerror=alert(1)>", long, long, long].map((comment, n) => ({ comment_ref: `comment:public:${String(n).padStart(32, "c")}`, comment, created_at: `2026-10-01T12:0${n}:00Z` }));
  const page = await toursPage(routesFor({ feedback: async projectionId => new Response(JSON.stringify({ data: { feedback: tourFeedback(projectionId, "A", comments) } }), { status: 200 }) }));
  await page.pick(0);
  await until(() => page.panel().all(c => c.className === "feedback-comment").length === 4, "four comments");
  const item = page.panel().children[0];
  assert.equal(item.className, "feedback-item");
  assert.ok(item.all(c => c.className === "feedback-answer")[0].textContent === "Shortlisted");
  const container = item.all(c => c.className === "feedback-comments")[0];
  assert.equal(container.children.length, 4, "all comments live in one vertical container");
  assert.ok(container.children.every(c => c.className === "feedback-comment"));
  assert.ok(page.panel().all(c => c.tag === "img").length === 0, "comment markup is text, never elements");
  assert.match(panelText(page.panel()), /<img src=x onerror=alert\(1\)>/);
  const css = await readFile(fileURLToPath(new URL("../../dealroom/tours/app.css", import.meta.url)), "utf8");
  assert.match(css, /\.feedback-comments\s*\{[^}]*flex-direction:\s*column/);
  assert.match(css, /\.feedback-comment\s*\{[^}]*overflow-wrap:\s*anywhere/);
  assert.doesNotMatch(css, /\.feedback-item[^{]*\{[^}]*display:\s*flex/, "the item is a block so the answer and comments never become columns");
});

test("broker: every scope checkbox has its own accessible name inside one labelled group", async () => {
  const html = await readFile(TOURS_HTML, "utf8");
  const group = /<fieldset class="scope-fieldset"><legend>Scopes<\/legend>([\s\S]*?)<\/fieldset>/.exec(html);
  assert.ok(group, "scopes are grouped with a fieldset and legend");
  const boxes = [...group[1].matchAll(/<input id="([^"]+)" type="checkbox" name="scope" value="([^"]+)"/g)];
  assert.deepEqual(boxes.map(m => m[2]), ["view_packet", "view_map", "shortlist", "comment"]);
  for (const [, inputId, value] of boxes) {
    const label = new RegExp(`<label for="${inputId}"><input id="${inputId}"[^>]*> ([A-Za-z ]+)</label>`).exec(group[1]);
    assert.ok(label && label[1].trim().length > 0, `${value} has a visible label`);
  }
  assert.doesNotMatch(html, /tabindex="-1"[^>]*name="scope"|name="scope"[^>]*tabindex="-1"/, "native checkboxes stay in the keyboard order");
  assert.equal(new Set(boxes.map(m => m[1])).size, 4, "ids are unique");
});
