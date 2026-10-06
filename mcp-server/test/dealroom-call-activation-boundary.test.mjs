/**
 * V5-J101 excludes active Calls from the first workspace release, and requires
 * Calls to be INERT rather than merely unadvertised.
 *
 * "Inert" is a claim about behaviour, so most of this file is behaviour: the
 * shell module is imported with no page around it and observed to start nothing,
 * and the Calls entrypoint is installed on a document, pressed, and observed to
 * open a dialog and make no request. The static half covers what only the
 * shipped markup can say — that the recorder controls are gone from it, and that
 * the boot sentinel the module looks for is really there.
 *
 * What this file deliberately does NOT assert: anything about the recorder
 * itself, the standalone controller, or calls already recorded. Those are
 * preserved, and dealroom-release.test.js covers the libraries and routes that
 * a later release will use again.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const file = (path) => readFile(ROOT + path, "utf8");
const APP = new URL("../../dealroom/js/app.js", import.meta.url).href;

// --------------------------------------------------------------- a tiny DOM
//
// Small on purpose. The boundary under test uses four DOM capabilities —
// find by id or attribute, listen for a click, set a couple of properties, and
// open or close a dialog — so a stub that supports exactly those keeps the test
// about the boundary rather than about a DOM implementation.

class StubElement {
  constructor(tag, attributes = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = new Map(Object.entries(attributes));
    this.listeners = new Map();
    this.hidden = false;
    this.disabled = false;
  }

  get id() { return this.attributes.get("id") || ""; }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  /** What a person pressing this control does, and nothing more. */
  press() {
    for (const handler of this.listeners.get("click") || []) handler({ type: "click", target: this });
    return this;
  }

  handlerCount(type = "click") { return (this.listeners.get(type) || []).length; }

  matches(selector) {
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith("[")) return this.attributes.has(selector.slice(1, -1));
    return this.tagName === selector.toUpperCase();
  }
}

class StubDialog extends StubElement {
  constructor(attributes = {}) {
    super("dialog", attributes);
    this.open = false;
    this.modalOpens = 0;
    this.closes = 0;
  }

  showModal() { this.open = true; this.modalOpens += 1; }
  close() { this.open = false; this.closes += 1; }
}

class StubDocument {
  constructor(nodes) { this.nodes = nodes; }
  querySelector(selector) { return this.nodes.find((node) => node.matches(selector)) || null; }
  querySelectorAll(selector) { return this.nodes.filter((node) => node.matches(selector)); }
  getElementById(id) { return this.querySelector(`#${id}`); }
}

function releaseShellDocument(extra = []) {
  return new StubDocument([
    new StubElement("button", { id: "callsButton", "aria-haspopup": "dialog" }),
    new StubDialog({ id: "callsDialog" }),
    new StubElement("button", { id: "callsClose" }),
    ...extra,
  ]);
}

/**
 * Record every way this module could reach the outside world or schedule work,
 * for the duration of one call.
 *
 * The transport globals are tripwires, in the style board-sync.mjs is already
 * held to. The two timer globals are counted rather than blocked, and delegate
 * to the real ones so nothing they are legitimately needed for breaks; anything
 * a watched call left running is cancelled on the way out, so a failure of these
 * assertions reports rather than hangs.
 */
const realClearInterval = globalThis.clearInterval;
const realClearTimeout = globalThis.clearTimeout;

async function underWatch(run) {
  const saved = new Map();
  const seen = { reached: [], setInterval: 0, setTimeout: 0 };
  const handles = { interval: [], timeout: [] };
  for (const name of ["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "setInterval", "setTimeout"]) {
    saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
  }
  const define = (name, value) => Object.defineProperty(globalThis, name,
    { configurable: true, writable: true, value });
  for (const name of ["fetch", "XMLHttpRequest", "WebSocket", "EventSource"]) {
    define(name, function tripwire() {
      seen.reached.push(name);
      throw new Error(`the release shell reached ${name}`);
    });
  }
  const realSetInterval = saved.get("setInterval")?.value;
  const realSetTimeout = saved.get("setTimeout")?.value;
  define("setInterval", (...args) => {
    seen.setInterval += 1;
    const handle = realSetInterval(...args);
    handles.interval.push(handle);
    return handle;
  });
  define("setTimeout", (...args) => {
    seen.setTimeout += 1;
    const handle = realSetTimeout(...args);
    handles.timeout.push(handle);
    return handle;
  });
  try {
    seen.result = await run();
  } finally {
    for (const handle of handles.interval) realClearInterval(handle);
    for (const handle of handles.timeout) realClearTimeout(handle);
    for (const [name, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
  return seen;
}

// ------------------------------------------------------------------ boot
//
// The module is imported with NO document in scope. In a browser the same
// module boots itself; here the sentinel is absent, so nothing runs — which is
// what makes the rest of this file possible, and is itself the strongest
// statement that importing the shell cannot start a read, a poll or a recorder.

test("importing the release shell with no page around it starts nothing at all", async () => {
  assert.equal(typeof globalThis.document, "undefined",
    "this suite must not run with a document installed, or the shell would boot");
  const seen = await underWatch(() => import(APP));
  assert.deepEqual(seen.reached, [], "importing the shell must make no request");
  assert.equal(seen.setInterval, 0, "importing the shell must start no poll");
  assert.equal(seen.setTimeout, 0, "importing the shell must schedule no work");
  assert.equal(typeof seen.result.installCallsBoundary, "function",
    "the Calls boundary is exported so it can be exercised, not only read");
});

// ------------------------------------------------------- the entrypoint

test("pressing Calls opens the inactive notice and reaches nothing", async () => {
  const { installCallsBoundary } = await import(APP);
  const doc = releaseShellDocument();
  const button = doc.getElementById("callsButton");
  const dialog = doc.getElementById("callsDialog");
  const close = doc.getElementById("callsClose");

  const seen = await underWatch(async () => {
    const wiring = installCallsBoundary(doc);
    button.press();
    button.press();
    close.press();
    return wiring;
  });

  assert.deepEqual(seen.result, { entrypoint: true, dialog: true, neutralized: 0 });
  assert.equal(dialog.modalOpens, 1, "a second press must not stack a second modal");
  assert.equal(dialog.closes, 1);
  assert.equal(dialog.open, false);
  // The whole point: two presses of the Calls control, and nothing left the page.
  assert.deepEqual(seen.reached, [], "the Calls entrypoint must make no request of any kind");
  assert.equal(seen.setInterval, 0, "the Calls entrypoint must start no poll");
  assert.equal(seen.setTimeout, 0, "the Calls entrypoint must schedule nothing");
  // Exactly one handler each: the entrypoint is not a delegation point that some
  // other control could fall into.
  assert.equal(button.handlerCount(), 1);
  assert.equal(close.handlerCount(), 1);
});

test("the boundary installs cleanly on a document that has no Calls markup", async () => {
  const { installCallsBoundary } = await import(APP);
  const seen = await underWatch(async () => installCallsBoundary(new StubDocument([])));
  assert.deepEqual(seen.result, { entrypoint: false, dialog: false, neutralized: 0 });
  assert.deepEqual(seen.reached, []);
});

// ------------------------------------------- a cached shell from an older day
//
// The service worker caches a navigation and a script separately, so an
// installed app can pair yesterday's index.html with today's app.js. Those
// controls arrive with no handler and are already inert — but a "Start weekly
// deal call" button that looks live and silently does nothing is a worse answer
// than one that is visibly not there.

test("a recorder control left in a cached shell is disabled, hidden and never wired", async () => {
  const { installCallsBoundary } = await import(APP);
  const stale = [
    new StubElement("button", { id: "callModeButton" }),
    new StubElement("button", { id: "callModeStop" }),
    new StubElement("button", { "data-call-mode-start": "weekly_deal_call" }),
    new StubElement("button", { "data-call-mode-start": "other_call" }),
    new StubElement("button", { id: "postCallRefresh" }),
    new StubElement("button", { "data-post-call-confirm": "c1" }),
    new StubElement("button", { "data-post-call-skip": "c1" }),
    new StubElement("button", { "data-create-outlook-draft": "d1" }),
    new StubElement("button", { "data-retry-call-context": "" }),
    new StubElement("a", { id: "callModeStandalone", href: "http://127.0.0.1:4682/" }),
  ];
  const doc = releaseShellDocument(stale);

  const seen = await underWatch(async () => {
    const wiring = installCallsBoundary(doc);
    // Press every one of them, the way a stale page or a script could.
    for (const node of stale) node.press();
    return wiring;
  });

  assert.equal(seen.result.neutralized, stale.length, "every retired control is accounted for");
  for (const node of stale) {
    const name = node.id || [...node.attributes.keys()].join(",");
    assert.equal(node.disabled, true, `${name} must be disabled`);
    assert.equal(node.hidden, true, `${name} must be hidden`);
    assert.equal(node.getAttribute("aria-hidden"), "true", `${name} must leave the accessibility tree`);
    assert.equal(node.handlerCount(), 0, `${name} must never be wired to anything`);
  }
  const link = doc.getElementById("callModeStandalone");
  assert.equal(link.getAttribute("href"), null,
    "a link to a local controller is an entrypoint too, and this workspace offers none");
  // `hidden` on a stub is a property, not a rendering. The shipped stylesheet is
  // what turns it into a real disappearance, so the rule is pinned here rather
  // than assumed: without it the sweep would leave a visible, aria-hidden
  // control on screen — worse than the state it set out to fix. Read only; the
  // stylesheet is not this slice's to change.
  assert.match(await file("dealroom/css/app.css"), /\[hidden\]\{display:none!important\}/,
    "the sweep's hiding depends on this rule");
  assert.deepEqual(seen.reached, [], "pressing every retired control must reach nothing");
  assert.equal(seen.setInterval, 0);
  assert.equal(seen.setTimeout, 0);

  // And the live entrypoint still works in the same document.
  doc.getElementById("callsButton").press();
  assert.equal(doc.getElementById("callsDialog").open, true);
});

// ------------------------------------------------------- the shipped shell

test("the shipped markup carries an inactive Calls entrypoint and no recorder", async () => {
  const html = await file("dealroom/index.html");

  assert.match(html, /id="callsButton"/);
  assert.match(html, /id="callsDialog"/);
  assert.match(html, /id="callsClose"/);
  // Reachable and explained without a mouse: a real button with a label that
  // states the product state, and a dialog description tied to it.
  assert.match(html, /<button type="button"[^>]*id="callsButton"[^>]*aria-label="Calls — not part of this release"/);
  assert.match(html, /id="callsDialog"[^>]*aria-labelledby="callsTitle"[^>]*aria-describedby="callsDetail"/);

  // Intuitive inactive messaging: what is off, and what was NOT taken away.
  assert.match(html, /Recording a call is not part of this release/);
  assert.match(html, /cannot start a recording or stop one/);
  assert.match(html, /already recorded are untouched/);
  // The dialog must not overclaim in the other direction either. #captureStatus
  // still reports a capture session the record layer knows about, so the dialog
  // may not say this page cannot see one — it says where it will show up.
  assert.doesNotMatch(html, /check whether one is running/,
    "the shell can still be told about a capture it did not start");
  assert.match(html, /If a recording is running somewhere else, its status still appears/);
  // Present-release truth, and no roadmap this shell cannot keep.
  assert.match(html, /Recording is not available in this release/);
  assert.doesNotMatch(html, /later release|future release/i,
    "the shell cannot promise a release it does not ship");
  assert.doesNotMatch(html, /coming soon|in beta|enable Calls|turn on Calls/i,
    "an inactive surface must not read as a switch someone could find");

  // No recorder in the markup at all — not hidden, not disabled, absent.
  for (const gone of [/id="callModeButton"/, /id="callModeStop"/, /id="callModeConsent"/,
    /data-call-mode-start/, /id="postCallPanel"/, /id="callModeStandalone"/,
    /127\.0\.0\.1/, /Start weekly deal call/, /Stop and process/]) {
    assert.doesNotMatch(html, gone, `${gone} must not be in the released shell`);
  }

  // The sentinel the module boots on. Named here so it cannot drift out of the
  // markup and leave the shell silently not booting.
  assert.match(html, /<tbody id="rows">/);
  assert.equal((html.match(/<script/g) || []).length, 1, "CSP is script-src 'self': still one module tag");
});

test("the shell module holds no recorder path, and boots only inside its own page", async () => {
  const app = await file("dealroom/js/app.js");

  // Nothing addresses a local bridge. The one loopback pattern that remains is
  // the test for a stale LINK to neutralize, which has no scheme and is never a
  // request target — so the assertion is about addresses, not about the word.
  assert.doesNotMatch(app, /https?:\/\/(127\.0\.0\.1|localhost)/);
  assert.doesNotMatch(app, /targetAddressSpace/);
  assert.doesNotMatch(app, /\/api\/(start|stop|state|call-context|post-call)/);
  assert.doesNotMatch(app, /post-call-client\.js/);
  assert.doesNotMatch(app, /consent_confirmed|weekly_deal_call/);

  // Two timers, both about the board. A third would be the recorder clock.
  assert.equal((app.match(/setInterval\(/g) || []).length, 2);
  assert.match(app, /state\.pollTimer = setInterval\(/);
  assert.match(app, /state\.boardRefreshTimer = setInterval\(/);

  // The entrypoint is wired to its own control, not to the delegated listener
  // that sees every click in the page.
  assert.match(app, /\n  installCallsBoundary\(\);/);
  assert.equal((app.match(/installCallsBoundary\(/g) || []).length, 2,
    "the definition and the one boot call site, and nothing else");
  assert.doesNotMatch(app, /closest\('\[data-call-mode-start\]'\)/);

  // The sweep runs before anything that can fail. A boot that dies on sign-in or
  // the network must not leave a cached page's recorder controls live, so this
  // ordering is the guarantee and is pinned as one.
  const bootBody = app.slice(app.indexOf("async function boot()"),
    app.indexOf("/**\n * The shell starts itself"));
  assert.notEqual(bootBody, "", "boot() body not found");
  assert.ok(bootBody.indexOf("installCallsBoundary();") > -1
    && bootBody.indexOf("installCallsBoundary();") < bootBody.indexOf("await createClient("),
    "the retired-control sweep must precede client creation");
  assert.doesNotMatch(app.slice(app.indexOf("function wireEvents"), app.indexOf("async function boot()")),
    /installCallsBoundary\(\)/, "and it is not also wired from the listener setup");

  // Boot is conditional on the page, and on nothing a person or a query string
  // can set. No flag, no override, no backdoor.
  assert.match(app, /if \(typeof document !== 'undefined' && document\.getElementById\('rows'\)\) \{\s*boot\(\)\.catch/);
  assert.doesNotMatch(app, /CALLS_ENABLED|ENABLE_CALLS|callsEnabled|FEATURE_/,
    "there is no flag that could switch Calls back on from this workspace");
  for (const match of app.matchAll(/localStorage\.\w+\('([^']+)'/g)) {
    assert.ok(["dealroom-theme", "dealroom-color-assist"].includes(match[1]),
      `no stored preference may reach Calls; found localStorage key ${match[1]}`);
  }
});
