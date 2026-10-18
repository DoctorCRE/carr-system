import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const file = (path) => readFile(ROOT + path, "utf8");
const APP = new URL("../../dealroom/js/app.js", import.meta.url).href;


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
  assert.deepEqual(seen.reached, [], "the Calls entrypoint must make no request of any kind");
  assert.equal(seen.setInterval, 0, "the Calls entrypoint must start no poll");
  assert.equal(seen.setTimeout, 0, "the Calls entrypoint must schedule nothing");
  assert.equal(button.handlerCount(), 1);
  assert.equal(close.handlerCount(), 1);
});

test("the boundary installs cleanly on a document that has no Calls markup", async () => {
  const { installCallsBoundary } = await import(APP);
  const seen = await underWatch(async () => installCallsBoundary(new StubDocument([])));
  assert.deepEqual(seen.result, { entrypoint: false, dialog: false, neutralized: 0 });
  assert.deepEqual(seen.reached, []);
});


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
  assert.match(await file("dealroom/css/app.css"), /\[hidden\]\{display:none!important\}/,
    "the sweep's hiding depends on this rule");
  assert.deepEqual(seen.reached, [], "pressing every retired control must reach nothing");
  assert.equal(seen.setInterval, 0);
  assert.equal(seen.setTimeout, 0);

  doc.getElementById("callsButton").press();
  assert.equal(doc.getElementById("callsDialog").open, true);
});


test("the shipped markup carries an inactive Calls entrypoint and no recorder", async () => {
  const html = await file("dealroom/index.html");

  assert.match(html, /id="callsButton"/);
  assert.match(html, /id="callsDialog"/);
  assert.match(html, /id="callsClose"/);
  assert.match(html, /<button type="button"[^>]*id="callsButton"[^>]*aria-label="Calls — not part of this release"/);
  assert.match(html, /id="callsDialog"[^>]*aria-labelledby="callsTitle"[^>]*aria-describedby="callsDetail"/);

  assert.match(html, /Recording a call is not part of this release/);
  assert.match(html, /cannot start a recording or stop one/);
  assert.match(html, /already recorded are untouched/);
  assert.doesNotMatch(html, /check whether one is running/,
    "the shell can still be told about a capture it did not start");
  assert.match(html, /If a recording is running somewhere else, its status still appears/);
  assert.match(html, /Recording is not available in this release/);
  assert.doesNotMatch(html, /later release|future release/i,
    "the shell cannot promise a release it does not ship");
  assert.doesNotMatch(html, /coming soon|in beta|enable Calls|turn on Calls/i,
    "an inactive surface must not read as a switch someone could find");

  for (const gone of [/id="callModeButton"/, /id="callModeStop"/, /id="callModeConsent"/,
    /data-call-mode-start/, /id="postCallPanel"/, /id="callModeStandalone"/,
    /127\.0\.0\.1/, /Start weekly deal call/, /Stop and process/]) {
    assert.doesNotMatch(html, gone, `${gone} must not be in the released shell`);
  }

  assert.match(html, /<tbody id="rows">/);
  assert.equal((html.match(/<script/g) || []).length, 1, "CSP is script-src 'self': still one module tag");
});

test("the shell module holds no recorder path, and boots only inside its own page", async () => {
  const app = await file("dealroom/js/app.js");

  assert.doesNotMatch(app, /https?:\/\/(127\.0\.0\.1|localhost)/);
  assert.doesNotMatch(app, /targetAddressSpace/);
  assert.doesNotMatch(app, /\/api\/(start|stop|state|call-context|post-call)/);
  assert.doesNotMatch(app, /post-call-client\.js/);
  assert.doesNotMatch(app, /consent_confirmed|weekly_deal_call/);

  assert.equal((app.match(/setInterval\(/g) || []).length, 2);
  assert.match(app, /state\.pollTimer = setInterval\(/);
  assert.match(app, /state\.boardRefreshTimer = setInterval\(/);

  assert.match(app, /\n  installCallsBoundary\(\);/);
  assert.equal((app.match(/installCallsBoundary\(/g) || []).length, 2,
    "the definition and the one boot call site, and nothing else");
  assert.doesNotMatch(app, /closest\('\[data-call-mode-start\]'\)/);

  const bootBody = app.slice(app.indexOf("async function boot()"),
    app.indexOf("/**\n * The shell starts itself"));
  assert.notEqual(bootBody, "", "boot() body not found");
  assert.ok(bootBody.indexOf("installCallsBoundary();") > -1
    && bootBody.indexOf("installCallsBoundary();") < bootBody.indexOf("await createClient("),
    "the retired-control sweep must precede client creation");
  assert.doesNotMatch(app.slice(app.indexOf("function wireEvents"), app.indexOf("async function boot()")),
    /installCallsBoundary\(\)/, "and it is not also wired from the listener setup");

  assert.match(app, /if \(typeof document !== 'undefined' && document\.getElementById\('rows'\)\) \{\s*boot\(\)\.catch/);
  assert.doesNotMatch(app, /CALLS_ENABLED|ENABLE_CALLS|callsEnabled|FEATURE_/,
    "there is no flag that could switch Calls back on from this workspace");
  for (const match of app.matchAll(/localStorage\.\w+\('([^']+)'/g)) {
    assert.ok(["dealroom-theme", "dealroom-color-assist"].includes(match[1]),
      `no stored preference may reach Calls; found localStorage key ${match[1]}`);
  }
});
