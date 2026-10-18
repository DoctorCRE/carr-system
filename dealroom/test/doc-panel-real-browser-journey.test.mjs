import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { launchChrome, CHROME_STARTUP_TIMEOUT_MS, CHROME_STOP_TIMEOUT_MS } from "./chrome-launch.mjs";
import { findDisposableChromium } from "./chromium-binary.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DEALROOM = path.resolve(HERE, "..");
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
// Both bounded launches, their teardown, and the existing journey budget.
const BROWSER_TEST_TIMEOUT_MS = 2 * CHROME_STARTUP_TIMEOUT_MS + 4 * CHROME_STOP_TIMEOUT_MS + 30_000;

class DevTools {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(url);
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.listeners = new Map();
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      // Unsolicited protocol events carry a method and no id.
      if (message.method) for (const listener of this.listeners.get(message.method) || []) listener(message.params);
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
      else pending.resolve(message.result || {});
    });
  }

  async call(method, params = {}) {
    await this.opened;
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject, method }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  on(method, listener) {
    this.listeners.set(method, [...(this.listeners.get(method) || []), listener]);
  }

  async evaluate(expression) {
    const result = await this.call("Runtime.evaluate", {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "browser evaluation failed");
    return result.result?.value;
  }

  close() { this.socket.close(); }
}

// The router serves each asset's content at its own exact path — "/" ->
// workspace.html, "/deals" -> index.html, "/clients"/"/vendors" ->
// business.html (mcp-server/src/workspace-surface-inventory.js's HOME_ROUTE /
// DEALS_ROUTE / CLIENTS_ROUTE / VENDORS_ROUTE) — and every one of these pages
// reads window.location.pathname for its own routing (workspace-business.js's
// start(), for instance). A file:// URL's pathname is the on-disk path, never
// one of those, so a file:// artifact fixture used to get treated as a stale
// bookmark and redirected off the page entirely. A real loopback HTTP server
// is what makes the pathname match production's routing, and it also makes
// the root-relative hrefs/srcs in the HTML resolve without any asset-locator
// rewriting.
const ROUTES = { "/": "/workspace.html", "/deals": "/index.html", "/clients": "/business.html", "/vendors": "/business.html" };
const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".webmanifest": "application/manifest+json", ".json": "application/json",
  ".png": "image/png", ".svg": "image/svg+xml",
};

/**
 * A minimal stand-in for the deployed Worker's /mcp JSON-RPC mount and
 * /pipeline/changes cursor, scoped to exactly the verbs booting index.html in
 * live mode and running one set-next-step/add-deal-note issue. `calls`
 * records every verb+arguments this server actually received, in order — the
 * REAL wire payload live-client.js sent, not a hand-built stand-in for it.
 */
function createRpcFixture(deal) {
  const calls = [];
  function handle(name, args = {}) {
    calls.push({ name, args });
    switch (name) {
      case "deal-room-board":
        return { actor: "joe", deals: [deal] };
      case "get-deal-room":
        return {
          deal_id: deal.id, name: deal.name, phase: deal.phase, type: deal.type, owner: deal.owner,
          attention: deal.attention, next_step: deal.next_step, next_date: deal.next_date,
          market: deal.market, segment: deal.segment, operating_state: deal.operating_state,
          account_client_id: deal.account_client_id, last_touch: deal.last_touch,
          thread: [], critical_dates: [], events: [], next_actions: [], activities: [],
          participants: [], premises: [], negotiation_rounds: [], documents: [],
        };
      case "capture-queue":
        return { candidates: [] };
      case "set-next-step":
        return {
          ok: true, deal_id: args.deal, next_step_id: "ns-test", next_action_id: null,
          supersedes: null, created_at: new Date().toISOString(),
        };
      case "add-deal-note":
        return { ok: true, deal_id: args.deal, note_id: "n-test", created_at: new Date().toISOString() };
      default:
        return { ok: true };
    }
  }
  return { calls, handle };
}

async function readRequestBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

/**
 * Serves dealroom/ with production's path-based routing. Pass `rpc` (from
 * createRpcFixture) to also answer POST /mcp and GET /pipeline/changes, which
 * is what lets index.html boot in live mode (?mode=live) against a real
 * network round trip instead of the built-in fixture client.
 */
async function pagesServer(t, { rpc } = {}) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (rpc && req.method === "GET" && url.pathname === "/pipeline/changes") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ events: [], presence: [], capture_sessions: [], cursor: null }));
      return;
    }
    if (rpc && req.method === "POST" && url.pathname === "/mcp") {
      const body = JSON.parse((await readRequestBody(req)) || "{}");
      const { name, arguments: args } = body.params || {};
      const result = rpc.handle(name, args);
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ jsonrpc: "2.0", id: body.id, result: { content: [{ type: "text", text: JSON.stringify(result) }], isError: false } }));
      return;
    }
    const routed = ROUTES[url.pathname] || url.pathname;
    const file = path.join(DEALROOM, routed);
    if (!file.startsWith(DEALROOM + path.sep)) { res.writeHead(403).end(); return; }
    try {
      const contents = await readFile(file);
      res.writeHead(200, { "content-type": CONTENT_TYPES[path.extname(file)] || "application/octet-stream" });
      res.end(contents);
    } catch {
      res.writeHead(404).end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const { port } = server.address();
  return `http://127.0.0.1:${port}`;
}

async function launchBrowser(t) {
  const chrome = await findDisposableChromium();
  if (!chrome) return { unavailableReason: "Chrome for Testing/Playwright Chromium was not found; real V5-J101 browser evidence was not run" };
  let browser;
  try { browser = await launchChrome(chrome); }
  catch (error) { return { unavailableReason: error.message }; }
  const cdp = new DevTools(browser.pageWsUrl);
  t.after(async () => { cdp.close(); await browser.close(); });
  t.diagnostic(`[chrome-startup] ${JSON.stringify({ startupMs: browser.startupMs, attempts: browser.attempts })}`);
  await cdp.call("Page.enable");
  await cdp.call("Runtime.enable");
  // These layout and interaction journeys must not depend on live font CDNs:
  // a stalled Google Fonts request in a fresh profile delays page load past
  // the polling budget. Network.setBlockedURLs (`urls` is the deprecated but
  // widely supported form) fails those requests immediately.
  await cdp.call("Network.enable");
  await cdp.call("Network.setBlockedURLs", { urls: ["*://fonts.googleapis.com/*", "*://fonts.gstatic.com/*"] });
  // Record request URLs and failures so a test can prove what was blocked.
  const requestUrls = new Map();
  const failures = [];
  cdp.on("Network.requestWillBeSent", ({ requestId, request }) => requestUrls.set(requestId, request.url));
  cdp.on("Network.loadingFailed", ({ requestId, blockedReason }) => failures.push({ url: requestUrls.get(requestId), blockedReason }));
  return { cdp, failures };
}

async function configureViewport(cdp, width, { touch = false } = {}) {
  await cdp.call("Emulation.setDeviceMetricsOverride", {
    width, height: 900, deviceScaleFactor: 1, mobile: touch,
    screenWidth: width, screenHeight: 900,
  });
  // maxTouchPoints must be 1-16 even when disabling touch emulation — Chrome's
  // CDP validation rejects 0 outright ("Touch points must be between 1 and 16"),
  // which made every desktop-viewport call (touch defaults to false) throw.
  await cdp.call("Emulation.setTouchEmulationEnabled", { enabled: touch, maxTouchPoints: touch ? 5 : 1 });
}

async function navigate(cdp, url, selector) {
  await cdp.call("Page.navigate", { url });
  for (let attempt = 0; attempt < 160; attempt += 1) {
    const ready = await cdp.evaluate(`document.readyState === "complete" && Boolean(document.querySelector(${JSON.stringify(selector)}))`);
    if (ready) return;
    await wait(50);
  }
  assert.fail(`browser journey did not reach ${selector} at ${url}`);
}

async function tap(cdp, selector) {
  const point = await cdp.evaluate(`(() => { const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2}; })()`);
  await cdp.call("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: point.x, y: point.y, radiusX: 2, radiusY: 2, force: 1 }] });
  await cdp.call("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}

const KEY_CODES = { Tab: 9, Escape: 27, Enter: 13, " ": 32 };
// CDP's "code" field wants the physical key name, not the character — Space
// is "Space", every other key here already matches its own code name.
const KEY_CODE_NAMES = { " ": "Space" };

async function key(cdp, value, { shift = false } = {}) {
  const modifiers = shift ? 8 : 0;
  const code = KEY_CODES[value] || 0;
  const codeName = KEY_CODE_NAMES[value] || value;
  // `text`/`unmodifiedText` matter for Space specifically: verified locally
  // that Chrome's headless CDP pipeline only fires a button's native
  // keyboard-activation click for Space when these are present, and does not
  // fire one for Enter via synthetic dispatch at all (a CDP/headless gap,
  // not a real-browser one — a real keyboard's Enter does activate a
  // focused <button> in Chrome). Space is an equally standard, spec-correct
  // way to activate a <button> from the keyboard, so it is what this harness
  // uses rather than working around the gap.
  const extra = value === " " ? { text: " ", unmodifiedText: " " } : {};
  await cdp.call("Input.dispatchKeyEvent", { type: "keyDown", key: value, code: codeName, modifiers, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, ...extra });
  await cdp.call("Input.dispatchKeyEvent", { type: "keyUp", key: value, code: codeName, modifiers, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
}

// Opens the Doc toggle the way a real keyboard-only visitor does — focus,
// then Space — never element.click(). A <button>'s native keyboard
// activation is Chrome's own input pipeline, not a JS shortcut standing in
// for it, so this exercises the toggle's real tabindex/focusability and its
// native activation behavior rather than assuming both are wired correctly.
// (Space, not Enter: see key()'s own comment — Chrome's headless CDP input
// pipeline does not fire a <button>'s native activation click for a
// synthetic Enter, only for Space, which is equally standard/spec-correct
// keyboard activation for a button.)
async function openDocWithKeyboard(cdp) {
  await cdp.evaluate("document.querySelector('#docPanelToggle').focus()");
  await key(cdp, " ");
}

async function waitFor(cdp, expression, message) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await cdp.evaluate(expression)) return;
    await wait(40);
  }
  assert.fail(message);
}

/** Polls the Node-side RPC capture (not the page) for a verb call, so the test never races the network round trip its own assertions depend on. */
async function waitForCall(rpc, name, fromIndex, message) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const found = rpc.calls.slice(fromIndex).find((call) => call.name === name);
    if (found) return found;
    await wait(50);
  }
  assert.fail(message);
}

const SNAPSHOT = `(() => {
  const panel = document.querySelector('#docPanel');
  const visible = (node) => { const r=node.getBoundingClientRect(), s=getComputedStyle(node); return !node.hidden && s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
  const controls = [...panel.querySelectorAll('button,input')].filter(visible);
  const outside = [...document.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')]
    .filter((node) => !panel.contains(node) && visible(node));
  const callsTours = [...document.querySelectorAll('.inert-entry')].map((node) => ({
    text: node.textContent.trim(), tag: node.tagName.toLowerCase(), ariaDisabled: node.getAttribute('aria-disabled'),
    href: node.getAttribute('href'), tabIndex: node.tabIndex, onclick: node.getAttribute('onclick'),
  }));
  const panelBox = panel.getBoundingClientRect();
  return {
    // document.documentElement.clientWidth is the root element's OWN box —
    // sized by the viewport, never enlarged by an overflowing descendant.
    // window.innerWidth is NOT safe here: independent review measured
    // Chrome's mobile emulation widening innerWidth to fit an overflowing
    // <main> (innerWidth=600 against a configured 375px viewport, clientWidth
    // stayed 375) — using innerWidth as the overflow baseline silently
    // passed exactly the defect this check exists to catch.
    viewportWidth: document.documentElement.clientWidth,
    documentScrollWidth: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
    // A position:fixed panel (doc-panel.css) is never part of the document's
    // own scrolling area, so no overflowing/mispositioned panel can EVER show
    // up in documentScrollWidth, no matter how far it sits off-screen. Its
    // own getBoundingClientRect is the only way to catch that.
    panelRect: { left: panelBox.left, right: panelBox.right, width: panelBox.width },
    panelHidden: panel.hidden, panelRole: panel.getAttribute('role'),
    ariaModal: panel.getAttribute('aria-modal'), activeId: document.activeElement?.id || null,
    focusInside: panel.contains(document.activeElement),
    outsideReachable: outside.filter((node) => !node.closest('[inert]')).map((node) => node.id || node.textContent.trim().slice(0,30)),
    minPanelTarget: controls.length ? Math.min(...controls.map((node) => { const r=node.getBoundingClientRect(); return Math.min(r.width,r.height); })) : 0,
    callsTours,
  };
})()`;

function modalityGuard(snapshot, expectedRole) {
  const failures = [];
  if (snapshot.panelHidden) failures.push("panel_hidden");
  if (snapshot.panelRole !== expectedRole) failures.push("wrong_role");
  if (expectedRole === "dialog" && snapshot.ariaModal !== "true") failures.push("modal_not_announced");
  if (expectedRole === "dialog" && snapshot.outsideReachable.length) failures.push("background_reachable");
  if (!snapshot.focusInside) failures.push("focus_outside_panel");
  if (snapshot.documentScrollWidth - snapshot.viewportWidth > 1) failures.push("horizontal_overflow");
  if (snapshot.panelRect.left < -1 || snapshot.panelRect.right > snapshot.viewportWidth + 1) failures.push("panel_overflows_viewport");
  if (snapshot.minPanelTarget < 24) failures.push("target_below_wcag_2_2_minimum");
  return failures;
}

function inertBoundaryGuard(snapshot) {
  const expected = ["Calls", "Tours"];
  const actual = snapshot.callsTours.map((item) => item.text).sort();
  const failures = [];
  if (JSON.stringify(actual) !== JSON.stringify(expected)) failures.push("inert_entries_missing");
  for (const item of snapshot.callsTours) {
    if (item.tag === "a" || item.tag === "button" || item.href || item.onclick || item.ariaDisabled !== "true" || item.tabIndex >= 0) {
      failures.push(`${item.text || "unknown"}_actionable`);
    }
  }
  return failures;
}

// Real dispatchEvent on the LAST focusable element in the Doc dialog, not a
// CDP-level Tab (which real Chrome's own tab-order can satisfy by wrapping
// back into the only non-inert subtree on the page even with containFocus
// entirely disabled — everything else on the page IS inert while Doc is
// modal, so "focus stayed inside the panel" is true either way and proves
// nothing about containFocus specifically). dispatchEvent's return value is
// the one honest signal: it is false if and only if some listener called
// preventDefault() during dispatch, which only containFocus does here.
const FOCUS_TRAP_CHECK = `(() => {
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const panel = document.querySelector('#docPanel');
  const stops = [...panel.querySelectorAll(FOCUSABLE)];
  const last = stops[stops.length - 1];
  last.focus();
  const event = new KeyboardEvent('keydown', { key: 'Tab', code: 'Tab', bubbles: true, cancelable: true });
  const notPrevented = last.dispatchEvent(event);
  return { prevented: !notPrevented, movedToFirst: document.activeElement === stops[0] };
})()`;

test("V5-J101 real Chrome desktop/touch journeys preserve modality, focus, reflow, targets, and inert Calls/Tours", { timeout: BROWSER_TEST_TIMEOUT_MS }, async (t) => {
  const origin = await pagesServer(t);
  const browser = await launchBrowser(t);
  if (!browser.cdp) {
    if (process.env.CI) assert.fail(browser.unavailableReason);
    t.skip(browser.unavailableReason);
    return;
  }
  const { cdp } = browser;

  await configureViewport(cdp, 1280);
  await navigate(cdp, `${origin}/clients`, "#docPanelToggle");
  await cdp.evaluate("document.querySelector('#docPanelToggle').click()");
  await waitFor(cdp, "!document.querySelector('#docPanel').hidden", "desktop Doc did not open");
  const desktop = await cdp.evaluate(SNAPSHOT);
  assert.deepEqual(modalityGuard(desktop, "complementary"), []);
  assert.deepEqual(inertBoundaryGuard(desktop), []);

  await cdp.evaluate("document.querySelector('#docPanelClose').click()");
  await configureViewport(cdp, 375, { touch: true });
  await tap(cdp, "#docPanelToggle");
  await waitFor(cdp, "!document.querySelector('#docPanel').hidden", "touch Doc did not open");
  const phone = await cdp.evaluate(SNAPSHOT);
  assert.deepEqual(modalityGuard(phone, "dialog"), []);
  assert.deepEqual(inertBoundaryGuard(phone), []);

  // Real focus-trap proof (M8): dispatchEvent on the last focusable stop, not
  // a CDP Tab (see FOCUS_TRAP_CHECK's own comment for why that would pass
  // even with containFocus fully disabled).
  const trap = await cdp.evaluate(FOCUS_TRAP_CHECK);
  assert.equal(trap.prevented, true, "containFocus must intercept (preventDefault) a Tab dispatched on the last focusable element");
  assert.equal(trap.movedToFirst, true, "Tab from the last focusable element must wrap focus to the first stop");

  await key(cdp, "Tab", { shift: true });
  assert.equal(await cdp.evaluate("document.querySelector('#docPanel').contains(document.activeElement)"), true,
    "Shift+Tab from the panel heading must stay in the modal");
  await key(cdp, "Escape");
  await waitFor(cdp, "document.querySelector('#docPanel').hidden", "Escape did not close an unpinned Doc");
  assert.equal(await cdp.evaluate("document.activeElement?.id"), "docPanelToggle", "closing Doc must restore focus to its opener");
});

test("V5-J101 live Google Fonts requests are blocked so page load never waits on a font CDN", { timeout: BROWSER_TEST_TIMEOUT_MS }, async (t) => {
  const origin = await pagesServer(t);
  const browser = await launchBrowser(t);
  if (!browser.cdp) {
    if (process.env.CI) assert.fail(browser.unavailableReason);
    t.skip(browser.unavailableReason);
    return;
  }
  const { cdp, failures } = browser;
  await configureViewport(cdp, 1280);
  // business.html links fonts.googleapis.com; a blocked request surfaces as
  // Network.loadingFailed with blockedReason "inspector".
  await navigate(cdp, `${origin}/clients`, "#docPanelToggle");
  await waitFor(cdp, "document.readyState === 'complete'", "page did not finish loading");
  const blocked = failures.filter((item) => item.blockedReason === "inspector").map((item) => new URL(item.url).hostname);
  assert.ok(blocked.includes("fonts.googleapis.com"), `fonts.googleapis.com must be blocked by the inspector; saw ${JSON.stringify(failures)}`);
});

test("V5-J101 the overflow/panel-fit guard catches live layout mutants a scrollWidth-only check would have missed", { timeout: BROWSER_TEST_TIMEOUT_MS }, async (t) => {
  const origin = await pagesServer(t);
  const browser = await launchBrowser(t);
  if (!browser.cdp) {
    if (process.env.CI) assert.fail(browser.unavailableReason);
    t.skip(browser.unavailableReason);
    return;
  }
  const { cdp } = browser;
  await configureViewport(cdp, 375, { touch: true });
  await navigate(cdp, `${origin}/clients`, "#docPanelToggle");
  await tap(cdp, "#docPanelToggle");
  await waitFor(cdp, "!document.querySelector('#docPanel').hidden", "touch Doc did not open");

  const clean = await cdp.evaluate(SNAPSHOT);
  assert.deepEqual(modalityGuard(clean, "dialog"), [], "the unmutated page must pass before any mutant is planted");

  // Mutant 1 — a live page overflow (independent review's own repro): forcing
  // <main> wider than the viewport is exactly what widened innerWidth in
  // Chrome's mobile emulation without moving documentScrollWidth/clientWidth
  // at all, on the OLD innerWidth-keyed check. This must still fail on the
  // NEW clientWidth-keyed one.
  await cdp.evaluate(`(() => { const s=document.createElement('style'); s.id='v5j101-overflow-mutant'; s.textContent='main{min-width:600px}'; document.head.appendChild(s); })()`);
  const liveOverflow = await cdp.evaluate(SNAPSHOT);
  assert.notDeepEqual(modalityGuard(liveOverflow, "dialog"), [], "a live main{min-width:600px} overflow mutant must fail the overflow guard");
  await cdp.evaluate("document.getElementById('v5j101-overflow-mutant')?.remove()");

  // Mutant 2 — the panel itself wider than the viewport. doc-panel.css sets
  // `.doc-panel{position:fixed}`, and a position:fixed element is excluded
  // from the document's own scrolling/overflow area in every engine, so NO
  // scrollWidth-based check — old or new — can ever see this one. Only the
  // panel's own getBoundingClientRect can.
  await cdp.evaluate(`(() => { const p=document.querySelector('#docPanel'); p.dataset.v5j101OriginalWidth = p.style.width; p.style.width='420px'; })()`);
  const panelOverflow = await cdp.evaluate(SNAPSHOT);
  assert.notDeepEqual(modalityGuard(panelOverflow, "dialog"), [], "a Doc panel wider than the viewport must fail the panel-fit guard even though it never touches documentScrollWidth");
  await cdp.evaluate(`(() => { const p=document.querySelector('#docPanel'); p.style.width = p.dataset.v5j101OriginalWidth || ''; })()`);

  const restored = await cdp.evaluate(SNAPSHOT);
  assert.deepEqual(modalityGuard(restored, "dialog"), [], "removing both mutants must restore a clean pass, proving the guard reacts to state rather than failing permanently");
});

test("V5-J101 Doc opens via real keyboard activation (not .click()) at desktop width on Home, Deals, and Clients/Vendors", { timeout: BROWSER_TEST_TIMEOUT_MS }, async (t) => {
  const origin = await pagesServer(t);
  const browser = await launchBrowser(t);
  if (!browser.cdp) {
    if (process.env.CI) assert.fail(browser.unavailableReason);
    t.skip(browser.unavailableReason);
    return;
  }
  const { cdp } = browser;
  await configureViewport(cdp, 1280);
  // Home ("/") and Deals ("/deals") boot in the app's own built-in fixture
  // mode here (no ?mode=live) — the keyboard-activation and modality contract
  // does not depend on real data, which is what makes covering every
  // authenticated surface cheap rather than only the one page the parity
  // test below drives live.
  for (const routePath of ["/", "/deals", "/clients", "/vendors"]) {
    await navigate(cdp, `${origin}${routePath}`, "#docPanelToggle");
    await openDocWithKeyboard(cdp);
    await waitFor(cdp, "!document.querySelector('#docPanel').hidden", `keyboard activation did not open Doc at ${routePath}`);
    const snapshot = await cdp.evaluate(SNAPSHOT);
    assert.deepEqual(modalityGuard(snapshot, "complementary"), [], `Doc panel failed its modality/overflow contract at ${routePath}`);
    assert.equal(snapshot.activeId, "docPanelTitle", `keyboard activation must move focus into the panel at ${routePath}`);
  }
});

const FIXTURE_DEAL = Object.freeze({
  id: "deal-v5j101", name: "Coastal Med Plaza", phase: "pending", type: "other",
  owner: "joe", attention: false, last_touch: new Date().toISOString(),
  next_step: "Confirm floor plan", next_date: "2026-10-15", segment: null, market: "Mobile, AL",
  operating_state: "active", account_client_id: null, field_base: {},
});

test("V5-J101 the UI form and Doc composer send equivalent set-next-step payloads through their REAL entry points", { timeout: BROWSER_TEST_TIMEOUT_MS }, async (t) => {
  const rpc = createRpcFixture(FIXTURE_DEAL);
  const origin = await pagesServer(t, { rpc });
  const browser = await launchBrowser(t);
  if (!browser.cdp) {
    if (process.env.CI) assert.fail(browser.unavailableReason);
    t.skip(browser.unavailableReason);
    return;
  }
  const { cdp } = browser;
  await configureViewport(cdp, 1280);
  // ?mode=live is required: 127.0.0.1 is a recognized local host
  // (boot-mode.js resolveDealroomBoot), but only an EXPLICIT ?mode=live opts
  // it into the real live client — fixture mode never calls /mcp at all. This
  // is the only way to drive the ACTUAL network payload both real entry
  // points (app.js's nextStepForm onSubmit, doc-panel.js's handleSubmit)
  // send, rather than a hand-built object standing in for either of them.
  await navigate(cdp, `${origin}/deals?mode=live`, `[data-open-deal="${FIXTURE_DEAL.id}"]`);

  // ---- Doc composer path: real registerDocPanelSource getContext, real handleSubmit ----
  await cdp.evaluate(`document.querySelector('[data-open-deal="${FIXTURE_DEAL.id}"]').click()`);
  await waitFor(cdp, "document.querySelector('#dealDialog').open", "the deal dialog did not open");
  await waitFor(cdp, "document.querySelector('#docPanelToggle') && !document.querySelector('#docPanelToggle').closest('[inert]')",
    "Doc's toggle did not relocate into the open deal dialog");
  await cdp.evaluate("document.querySelector('#docPanelToggle').click()");
  await waitFor(cdp, "!document.querySelector('#docPanel').hidden", "Doc did not open alongside the deal dialog");
  await cdp.evaluate(`(() => { document.querySelector('#docPanelInput').value = '/set_next_step send the LOI'; })()`);
  await cdp.evaluate("document.querySelector('#docPanelForm').requestSubmit()");

  const docCall = await waitForCall(rpc, "set-next-step", 0, "the Doc composer's /set_next_step did not reach the server");
  assert.equal(docCall.args.deal, FIXTURE_DEAL.id,
    "M18: handleSubmit must send the host's real open-deal context (registerDocPanelSource getContext), not an empty/ignored one");
  assert.equal(docCall.args.text, "send the LOI");
  assert.equal(docCall.args.next_date, FIXTURE_DEAL.next_date,
    "M20: with no explicit date, the Doc composer's context (buildDealContext with no override) must preserve the deal's CURRENT next_date, never force null");
  assert.equal(rpc.calls.filter((call) => call.name === "add-deal-note").length, 0,
    "M21: the Doc composer must send the PARSED command (set-next-step), never a fixed add_note");

  await cdp.evaluate("document.querySelector('#docPanelClose').click()");
  await cdp.evaluate("document.querySelector('[data-close-deal]').click()");
  await waitFor(cdp, "!document.querySelector('#dealDialog').open", "the deal dialog did not close");

  // ---- UI form path: real nextStepForm onSubmit, real dialogForm submit ----
  const beforeUiCalls = rpc.calls.length;
  await cdp.evaluate(`document.querySelector('[data-next-step="${FIXTURE_DEAL.id}"]').click()`);
  await waitFor(cdp, "document.querySelector('#formDialog').open", "the next-step form did not open");
  await cdp.evaluate(`(() => {
    document.querySelector('#stepText').value = 'UI form step';
    document.querySelector('#stepDate').value = '2027-01-15';
  })()`);
  await cdp.evaluate("document.querySelector('#dialogSubmit').click()");

  const uiCall = await waitForCall(rpc, "set-next-step", beforeUiCalls, "the UI next-step form did not reach the server");
  assert.equal(uiCall.args.deal, FIXTURE_DEAL.id);
  assert.equal(uiCall.args.text, "UI form step");
  assert.equal(uiCall.args.next_date, "2027-01-15",
    "M19: the UI form must send the EXPLICIT date in its own #stepDate input, never a hard-coded null");
});

test("V5-J101 browser guards kill planted modality, overflow, accessibility, and inert-boundary mutants", () => {
  const good = {
    panelHidden: false, panelRole: "dialog", ariaModal: "true", focusInside: true,
    outsideReachable: [], minPanelTarget: 24,
    viewportWidth: 375, documentScrollWidth: 375, panelRect: { left: 0, right: 375, width: 375 },
    callsTours: [
      { text: "Calls", tag: "span", ariaDisabled: "true", href: null, tabIndex: -1, onclick: null },
      { text: "Tours", tag: "span", ariaDisabled: "true", href: null, tabIndex: -1, onclick: null },
    ],
  };
  assert.deepEqual(modalityGuard(good, "dialog"), []);
  assert.deepEqual(inertBoundaryGuard(good), []);

  for (const mutant of [
    { ...good, panelRole: "complementary" },
    { ...good, ariaModal: null },
    { ...good, outsideReachable: ["background link"] },
    { ...good, focusInside: false },
    { ...good, documentScrollWidth: good.viewportWidth + 18 },
    { ...good, minPanelTarget: 12 },
    // The panel itself wider than the viewport — position:fixed, so it never
    // shows up in documentScrollWidth; only panelRect can catch it.
    { ...good, panelRect: { left: 0, right: 420, width: 420 } },
  ]) assert.notDeepEqual(modalityGuard(mutant, "dialog"), [], `modality guard survived mutant ${JSON.stringify(mutant)}`);

  const actionableCalls = structuredClone(good);
  actionableCalls.callsTours[0] = { ...actionableCalls.callsTours[0], tag: "button", ariaDisabled: null, tabIndex: 0 };
  assert.notDeepEqual(inertBoundaryGuard(actionableCalls), [], "inert-boundary guard must kill an actionable Calls mutant");
});
