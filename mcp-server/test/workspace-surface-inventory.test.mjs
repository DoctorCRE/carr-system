// V5-J101 — the proof perimeter for what the workspace has already built.
//
// Nothing here measures anything, issues anything or activates anything. Each test
// takes a claim J101 has been making and puts it under the merge gate, and each one
// is written so that it fails if the claim stops being true — including the three
// negatives at the end, which exist because an assertion that cannot go red proves
// nothing.

import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { DEAL_ROOM_FIELDS } from "../src/dealroom.js";
import { BENCHMARK_SLO_THRESHOLDS } from "../src/benchmark-minimum.v5.js";
import { BUSINESS_API_PREFIX, BUSINESS_ASSET_PATH, CLIENTS_ROUTE, VENDORS_ROUTE, isBusinessApiPath } from "../src/workspace-business-read.js";
import { COMMAND_CENTER_PATH } from "../src/workspace-command-center.js";
import {
  ACKNOWLEDGEMENT_ENDPOINTS, AUTHENTICATED_SURFACES, COMMAND_ENDPOINT, COMMAND_WRITE_VERBS,
  WORKSPACE_ASSET_PATHS, WORKSPACE_READ_ENDPOINTS, WORKSPACE_ROUTES, shellCoverage, surfaceForAsset,
} from "../src/workspace-surface-inventory.js";

const REPO = fileURLToPath(new URL("../..", import.meta.url));
const DEALROOM_TEST_DIR = `${REPO}dealroom/test`;
const SHIM = `${REPO}mcp-server/test/workspace-command-center-browser.test.mjs`;

const read = (relative) => readFile(`${REPO}${relative}`, "utf8");

/**
 * What the file DECLARES, as opposed to what it explains about itself. A module
 * whose comment says it carries no p95 must not fail a p95 check because it said so
 * — the same reason the static suite strips comments before checking copy.
 */
const withoutComments = (source) => source
  .replace(/\/\*[\s\S]*?\*\//g, " ")
  .replace(/(^|\s)\/\/.*$/gm, "$1");

// ---------------------------------------------------------------------------
// The three checkers. Each is a pure function so the negatives at the bottom can
// hand it a mutated input and prove it says no.
// ---------------------------------------------------------------------------

/** Every dealroom/test suite must be imported by the shim. Returns the orphans. */
function orphanedSuites(suiteFiles, shimSource) {
  const imported = new Set([...shimSource.matchAll(/import\s+"\.\.\/\.\.\/dealroom\/test\/([^"]+)"/g)].map((match) => match[1]));
  return suiteFiles.filter((file) => !imported.has(file));
}

const SHELL_PARTS = Object.freeze({
  workspace_stylesheet: /<link rel="stylesheet" href="\/css\/workspace\.css">/,
  nav_more: /<details class="nav-more">/,
  mobile_nav: /<nav class="mobile-nav"/,
  links_clients: /href="\/clients"/,
  links_vendors: /href="\/vendors"/,
});

/** Where the declared shell state and the file disagree, in either direction. */
function shellMismatches(surface, html) {
  return Object.entries(SHELL_PARTS)
    .filter(([part, pattern]) => pattern.test(html) !== surface[part])
    .map(([part]) => `${surface.asset}.${part}: declared ${surface[part]}, file says ${!surface[part]}`);
}

/** The router's own exact-path table, with its named constants resolved. */
function routerExactPaths(dealroomWebSource) {
  const block = dealroomWebSource.match(/const DEALROOM_EXACT_PATHS = new Set\(\[([\s\S]*?)\]\);/)?.[1];
  assert.ok(block, "DEALROOM_EXACT_PATHS literal not found in dealroom-web.js");
  const NAMED = { CLIENTS_ROUTE, VENDORS_ROUTE, BUSINESS_ASSET_PATH };
  const served = new Set([...block.matchAll(/"([^"]+)"/g)].map((match) => match[1]));
  for (const [name, value] of Object.entries(NAMED)) {
    if (new RegExp(`\\b${name}\\b`).test(block)) served.add(value);
  }
  return served;
}

/** Routes the inventory names that the router does not serve. */
function unservedRoutes(routes, served) {
  return routes.filter((route) => !served.has(route));
}

/**
 * The health label ships TWICE: once server-rendered in the markup, and again from
 * the script the instant the module runs. The markup one is the first thing every
 * reader sees, and it is the ONLY one they ever see if the module does not execute
 * — a content-security refusal, a script error, an offline first paint. That is
 * precisely the state in which an unqualified claim about the workspace is least
 * honest, because nothing has been read at all.
 *
 * So the markup label is not checked against a literal spelled out here, which would
 * only move the drift one file over. It is bound to the script's own loading label:
 * change either one and forget the other and this reports the drift by name.
 */
function healthLabelDrift(asset, html, js) {
  const block = js.match(/const HEALTH_LABEL = \{[\s\S]*?\};/)?.[0];
  if (!block) return `${asset}: no HEALTH_LABEL in the script`;
  const loading = block.match(/\bloading: "([^"]+)"/)?.[1];
  if (!loading) return `${asset}: HEALTH_LABEL declares no loading state`;
  const markup = html.match(/id="healthLabel">([^<]*)</)?.[1];
  if (markup === undefined) return `${asset}: no server-rendered #healthLabel in the markup`;
  if (markup !== loading) return `${asset}: markup ships "${markup}", script's loading label is "${loading}"`;
  return null;
}

// ---------------------------------------------------------------------------
// Clause 1 — nothing in dealroom/test is orphaned.
// ---------------------------------------------------------------------------

test("every dealroom/test suite is imported by the one CI shim that collects them", async () => {
  const suiteFiles = (await readdir(DEALROOM_TEST_DIR)).filter((file) => file.endsWith(".test.mjs")).sort();
  const shim = await readFile(SHIM, "utf8");

  // Read from the DIRECTORY, never from a list written here. A list written here
  // would have to be edited by the same person who forgot to edit the shim, which
  // is exactly how four suites — boot-mode, lead-board-client, lead-board-static
  // and system-work-branding, twelve tests — ran at no merge gate at all.
  assert.deepEqual(orphanedSuites(suiteFiles, shim), [], "these dealroom/test suites are collected by nobody");
  assert.ok(suiteFiles.length >= 6, `expected at least the six known suites, found ${suiteFiles.length}`);

  // dealroom/ has no package.json, so ops/ci.sh's unit loop cannot reach it; the
  // shim is the only door. If a second door is ever added this assertion is the
  // place to say so, rather than letting two half-collections drift apart.
  assert.equal((shim.match(/^import "\.\.\/\.\.\/dealroom\/test\//gm) || []).length, suiteFiles.length);
});

// ---------------------------------------------------------------------------
// Clause 2 — one shell inventory across all seven authenticated surfaces.
// ---------------------------------------------------------------------------

test("one shell inventory records the actual state of all seven authenticated surfaces", async () => {
  const onDisk = (await readdir(`${REPO}dealroom`)).filter((file) => file.endsWith(".html")).sort();
  assert.deepEqual(AUTHENTICATED_SURFACES.map((surface) => surface.asset).sort(), onDisk,
    "a surface added to dealroom/ must be declared in the inventory, not discovered later");

  const mismatches = [];
  for (const surface of AUTHENTICATED_SURFACES) {
    mismatches.push(...shellMismatches(surface, await read(`dealroom/${surface.asset}`)));
  }
  // Checked in BOTH directions: a declared part that has gone missing fails, and a
  // part declared absent that has quietly appeared fails too. That is what makes
  // this a fence today and the acceptance test for the single-shell unit later.
  assert.deepEqual(mismatches, []);

  // The true count today, stated once and out loud. Two of seven.
  assert.deepEqual(shellCoverage(), { on_shell: 2, total: 7, off_shell: 5 });
  assert.deepEqual(
    AUTHENTICATED_SURFACES.filter((surface) => surface.workspace_stylesheet).map((surface) => surface.asset),
    ["workspace.html", "business.html"],
  );
});

test("the shell split is one deliberate statement, not two tests that happen to agree", async () => {
  const receipts = await read("mcp-server/test/deal-change-receipts.test.mjs");
  const staticSuite = await read("dealroom/test/workspace-command-center-static.test.mjs");

  // deal-change-receipts positively asserts the Deal Room carries none of the
  // business navigation. The inventory's index.html row says the same thing from
  // the other side. Either one alone reads as an accident; pinned to each other
  // they are the split being recorded on purpose.
  assert.match(receipts, /assert\.doesNotMatch\(html, \/Clients\|Vendors\|Tours\/, "navigation is out of this increment"\)/);
  const dealRoom = surfaceForAsset("index.html");
  assert.equal(dealRoom.links_clients, false);
  assert.equal(dealRoom.links_vendors, false);
  assert.equal(dealRoom.workspace_stylesheet, false);

  // And the static suite's six-surface navigation test is the third party to the
  // same statement: global nav on six, business nav on two.
  assert.match(staticSuite, /all six authenticated surfaces expose deterministic global navigation/);
  assert.match(staticSuite, /workspace-surface-inventory/,
    "the static suite points at the one declaration rather than restating the split");
});

// ---------------------------------------------------------------------------
// Clause 3 — the current page is announced AND shown.
// ---------------------------------------------------------------------------

test("the current page is announced and shown on every workspace surface", async () => {
  const home = await read("dealroom/workspace.html");
  const business = await read("dealroom/business.html");
  const businessJs = await read("dealroom/js/workspace-business.js");
  const css = await read("dealroom/css/workspace.css");

  // Home does both in markup: the class that paints and the attribute that announces.
  assert.match(home, /<a class="active" href="\/" aria-current="page">Home<\/a>/);
  const homeBar = home.match(/<nav class="mobile-nav"[\s\S]*?<\/nav>/)?.[0] || "";
  assert.equal((homeBar.match(/aria-current="page"/g) || []).length, 1, "Home's phone bar marks exactly one current page");

  // Clients and Vendors are one asset serving two routes, so both marks are set from
  // the one dataset at render time. Before this unit only aria-current was set, and
  // `.primary-nav a.active` — the only rule in the stylesheet that paints a current
  // tab — therefore never fired on either page.
  assert.match(css, /\.primary-nav a:hover,\.primary-nav a\.active\{/);
  assert.match(css, /\.primary-nav a\.active\{[^}]*box-shadow/);
  assert.match(businessJs, /function markCurrentPage\(dataset\)/);
  assert.match(businessJs, /link\.setAttribute\("aria-current", here \? "page" : "false"\)/);
  assert.match(businessJs, /link\.classList\.toggle\("active", here\)/);
  assert.match(businessJs, /markCurrentPage\(dataset\)/);
  assert.doesNotMatch(businessJs, /navClients\.setAttribute\("aria-current"/,
    "the two marks are set together, in one place, so they cannot disagree");

  // The phone bar on both business routes now marks the current page, matching Home.
  assert.match(businessJs, /document\.querySelectorAll\("\.mobile-nav a\[href\]"\)/);
  assert.match(businessJs, /link\.setAttribute\("aria-current", "page"\)/);
  assert.match(businessJs, /link\.removeAttribute\("aria-current"\)/);

  // aria-current and not a class, because there is no rule that would paint a class
  // there. A mark that paints nothing is the defect this test exists to close, so it
  // must not be reintroduced on the other navigation.
  assert.doesNotMatch(css, /\.mobile-nav a\.active/);
  const businessBar = business.match(/<nav class="mobile-nav"[\s\S]*?<\/nav>/)?.[0] || "";
  assert.doesNotMatch(businessBar, /aria-current/,
    "the business phone bar is marked at render time, from the route actually being shown");
});

// ---------------------------------------------------------------------------
// Clause 4 — the Home payload contract carries no dead fields.
// ---------------------------------------------------------------------------

test("the Home payload contract carries no dead fields, on both sides", async () => {
  const { validWorkspacePayload } = await import("../../dealroom/js/workspace-command-center-model.js");
  const model = await read("dealroom/js/workspace-command-center-model.js");
  const producer = await read("mcp-server/src/workspace-command-center.js");

  // PRODUCER SIDE: both keys are emitted, and both are emitted empty.
  assert.match(producer, /this_week: \[\],/);
  assert.match(producer, /recent_calls: \[\],/);

  // VALIDATOR SIDE: `Array.isArray` alone accepted any shape at all in either list
  // and no renderer ever looked, so a future producer could have filled one with
  // anything and nothing would have said a word.
  assert.match(model, /function emptyContractList\(value\)/);
  assert.match(model, /Array\.isArray\(value\) && value\.length === 0/);
  assert.match(model, /!emptyContractList\(payload\.this_week\) \|\| !emptyContractList\(payload\.recent_calls\)/);
  assert.doesNotMatch(withoutComments(model), /!Array\.isArray\(payload\.this_week\)/);

  const fresh = (offsetMs = 60_000) => ({
    source: "v_deal_room_board", source_ref: "v_deal_room_board",
    observed_at: new Date().toISOString(), valid_until: new Date(Date.now() + offsetMs).toISOString(),
    freshness: "fresh", correlation_id: "c-1",
    safe_explanation: "Fresh because this is a no-store request-time canonical database aggregate; valid for 60 seconds.",
  });
  const payload = () => ({
    viewer: "joe",
    needs_you_now: [
      { kind: "team_flagged_deals", scope: "team", count: 1, destination: "/deals?workspace=team&filter=flagged" },
      { kind: "my_flagged_deals", scope: "mine", count: 0, destination: "/deals?workspace=team&filter=flagged&owner=me" },
    ],
    this_week: [],
    metrics: [
      { scope: "team", active_deals: 3, flagged_deals: 1, active_destination: "/deals?workspace=team", flagged_destination: "/deals?workspace=team&filter=flagged", source: fresh() },
      { scope: "mine", active_deals: 1, flagged_deals: 0, active_destination: null, flagged_destination: "/deals?workspace=team&filter=flagged&owner=me", source: fresh() },
    ],
    recent_calls: [],
    doc_at_work: [{ kind: "active_nonhuman_work", count: 2, source: { ...fresh(), source: "ops.work_request", source_ref: "ops.work_request" } }],
    recent_activity: [{ kind: "changed_work", count: 4, observed_at: new Date().toISOString(), source: { ...fresh(), source: "ops.work_request", source_ref: "ops.work_request" } }],
    source: { ...fresh(), source: "command_center", source_ref: "v_deal_room_board+ops.work_request" },
  });

  assert.equal(validWorkspacePayload(payload()), true, "the shape the producer actually emits is accepted");

  // THE NEGATIVE. A wrongly-shaped element in either list is now refused instead of
  // silently accepted. Any element is wrongly shaped: no element shape is declared,
  // because there is no honest one to declare — a this_week row would need a Deal
  // Room destination the board has no URL form for, and a recent_calls row would be
  // a Calls affordance in a release where Calls is inert. Declaring the list empty
  // is what forces a future producer to bring the element and its renderer together.
  for (const element of [{}, { kind: "anything" }, { kind: "deal_due", count: 2 }, "a string", 7, null]) {
    const withThisWeek = payload();
    withThisWeek.this_week = [element];
    assert.equal(validWorkspacePayload(withThisWeek), false, `this_week must refuse ${JSON.stringify(element)}`);
    const withCalls = payload();
    withCalls.recent_calls = [element];
    assert.equal(validWorkspacePayload(withCalls), false, `recent_calls must refuse ${JSON.stringify(element)}`);
  }
  // The keys themselves stay in the exact-key set: the producer emits them and three
  // suites outside this unit pin them there. Removing a key is still refused.
  const missing = payload();
  delete missing.this_week;
  assert.equal(validWorkspacePayload(missing), false);
});

// ---------------------------------------------------------------------------
// Clause 5 — the health label says only what it measures.
// ---------------------------------------------------------------------------

test("the workspace makes no health claim it does not read", async () => {
  const js = withoutComments(await read("dealroom/js/workspace-command-center.js"));
  const html = await read("dealroom/workspace.html");

  // The orb is driven by one thing: the state of the Command Center fetch. Every
  // label it can display therefore names the READ. The word is bounded because
  // "read" is a substring of "already" and "ready", and an unbounded match would
  // pass a label like "Workspace already unavailable" — the very claim this clause
  // exists to keep out.
  const labels = js.match(/const HEALTH_LABEL = \{[\s\S]*?\};/)?.[0] || "";
  assert.notEqual(labels, "", "HEALTH_LABEL not found");
  const values = [...labels.matchAll(/: "([^"]+)"/g)].map((match) => match[1]);
  assert.equal(values.length, 4);
  for (const value of values) {
    assert.match(value, /\bread\b/i, `"${value}" must name what it measures`);
  }

  // FIVE labels ship, not four. The fifth is server-rendered in the markup, it is
  // what a reader sees first, and it is the ONLY one they ever see if the module
  // never runs. The same rule is applied to it DIRECTLY, and before the binding
  // below, so that each half can go red on its own: a markup label that stops
  // naming the read fails here even while the binding still holds.
  const CLAIMS = [/Workspace available/, /Workspace unavailable/, /System online/i, /All systems/i, /\bhealthy\b/i, /operational/i];
  const markupLabel = html.match(/id="healthLabel">([^<]*)</)?.[1];
  assert.notEqual(markupLabel, undefined, "no server-rendered #healthLabel in workspace.html");
  assert.match(markupLabel, /\bread\b/i, `the markup label "${markupLabel}" must name what it measures`);
  for (const claim of CLAIMS) {
    assert.doesNotMatch(markupLabel, claim, `the first label a reader sees must not claim ${claim}`);
  }

  // And it is BOUND to the script's loading label rather than restated here, which
  // would only move the drift one file over. Clause 5b proves this reports drift.
  assert.equal(healthLabelDrift("workspace.html", html, js), null);

  // The claims it used to make, and the neighbouring ones it must not start making.
  // This is the discipline the static suite already applies to "System online".
  for (const claim of CLAIMS) {
    assert.doesNotMatch(js, claim, `the surface must not claim ${claim}`);
    assert.doesNotMatch(html, claim, `the markup must not claim ${claim}`);
  }

  // And it reads no projection, because there is none to read: the slice's
  // interfaces name an ops.doctorcre.com read-only health projection and no producer
  // for one exists anywhere in the tree. Naming one here would invent the contract.
  assert.doesNotMatch(js, /ops\.doctorcre\.com/);
  assert.doesNotMatch(js, /["'`][^"'`]*\/health\b/);
  assert.match(js, /const ENDPOINT = "\/api\/v1\/command-center"/);
  assert.equal((js.match(/fetch\(/g) || []).length, 1, "one read, and the orb reports that one read");
  assert.equal((js.match(/setHealth\(/g) || []).length, 5,
    "setHealth is defined once and called from the four render paths, every one of them a read state");
});

// ---------------------------------------------------------------------------
// Clause 5b — the pre-script label is the script's own, on BOTH surfaces.
//
// Clause 5 binds the pair on Home. This binds the same pair on the sibling business
// surface, which already ships a markup label byte-identical to its own script's,
// and it proves the checker can go red — the drift on Home existed for a whole
// review round because the clause that owned the markup only listed phrases it must
// not say, and never asked whether it said the same thing as the script.
// ---------------------------------------------------------------------------

test("the server-rendered health label is the script's own loading label on every workspace surface", async () => {
  const surfaces = [
    { asset: "workspace.html", script: "dealroom/js/workspace-command-center.js" },
    { asset: "business.html", script: "dealroom/js/workspace-business.js" },
  ];

  const live = [];
  for (const surface of surfaces) {
    const html = await read(`dealroom/${surface.asset}`);
    const js = withoutComments(await read(surface.script));
    assert.equal(healthLabelDrift(surface.asset, html, js), null);
    live.push({ ...surface, html, js });
  }

  // Real mutations of real inputs, each pinned to the exact sentence it must report.
  // The first is the defect this round fixed, replayed against both surfaces.
  for (const surface of live) {
    const loading = surface.js.match(/\bloading: "([^"]+)"/)[1];
    const stale = surface.html.replace(/id="healthLabel">([^<]*)</, 'id="healthLabel">Checking workspace…<');
    assert.equal(healthLabelDrift(surface.asset, stale, surface.js),
      `${surface.asset}: markup ships "Checking workspace…", script's loading label is "${loading}"`);

    const stripped = surface.html.replace(/ id="healthLabel"/, "");
    assert.equal(healthLabelDrift(surface.asset, stripped, surface.js),
      `${surface.asset}: no server-rendered #healthLabel in the markup`);

    const unlabelled = surface.js.replace(/\bloading: "[^"]+",\n/, "");
    assert.equal(healthLabelDrift(surface.asset, surface.html, unlabelled),
      `${surface.asset}: HEALTH_LABEL declares no loading state`);
  }
});

// ---------------------------------------------------------------------------
// Clause 6 — Tours inertness is a whole-surface property.
// ---------------------------------------------------------------------------

test("no authenticated surface offers a Tours affordance", async () => {
  for (const surface of AUTHENTICATED_SURFACES) {
    const html = await read(`dealroom/${surface.asset}`);
    const where = surface.asset;

    // Nothing a pointer or a keyboard can reach. Same shape as the Calls boundary's
    // markup assertions: the control is absent, not merely styled away.
    assert.doesNotMatch(html, /<a[^>]*>\s*Tours\s*</i, `${where} must offer no Tours link`);
    assert.doesNotMatch(html, /<button[^>]*>\s*Tours\s*</i, `${where} must offer no Tours button`);
    assert.doesNotMatch(html, /href="[^"]*\/tours[^"]*"/i, `${where} must address no Tours route`);
    assert.doesNotMatch(html, /data-tour|id="tour|tourHandler|startTour/i, `${where} must wire no Tours entrypoint`);

    // And it must not read as a switch someone could go and find.
    assert.doesNotMatch(html, /enable Tours|turn on Tours|Tours,? coming soon|Book a tour|Start a tour/i,
      `${where} must not promise Tours`);
  }

  // The one place Tours is named at all says it is unavailable, and says it as text
  // rather than as a control. business.html is asserted here as the whole-surface
  // property's single exception, so the guarantee no longer rests on that one label.
  const named = [];
  for (const surface of AUTHENTICATED_SURFACES) {
    if (/Tours/.test(await read(`dealroom/${surface.asset}`))) named.push(surface.asset);
  }
  assert.deepEqual(named, ["business.html"]);
  const business = await read("dealroom/business.html");
  assert.match(business, /<span class="inert-entry" aria-disabled="true">Tours<\/span>/);

  // The workspace scripts reach no Tours path either.
  for (const script of ["dealroom/js/workspace-command-center.js", "dealroom/js/workspace-business.js"]) {
    // Word-bounded on purpose: this must fail on a Tours reference, not on the next
    // identifier that happens to contain the letters (contour, detour, tourniquet).
    assert.doesNotMatch(withoutComments(await read(script)), /\btours?\b/i, `${script} must not address Tours`);
  }
});

// ---------------------------------------------------------------------------
// Clause 7 — the routes and acknowledgement endpoints, and nothing else.
// ---------------------------------------------------------------------------

test("the inventory names only routes and endpoints the router actually serves", async () => {
  const web = await read("mcp-server/src/dealroom-web.js");
  const served = routerExactPaths(web);

  assert.deepEqual(unservedRoutes(WORKSPACE_ROUTES, served), [],
    "an inventory entry naming a route the router does not serve");
  assert.deepEqual(unservedRoutes(WORKSPACE_ASSET_PATHS, served), []);
  assert.deepEqual([...WORKSPACE_ROUTES].sort(),
    ["/", "/clients", "/deals", "/leads", "/queue.html", "/room.html", "/system-work.html", "/vendors"]);

  // The read endpoints are the two the workspace actually calls, resolved through
  // the same predicates the router dispatches on rather than restated as strings.
  assert.equal(WORKSPACE_READ_ENDPOINTS[0], COMMAND_CENTER_PATH);
  assert.match(web, /url\.pathname === COMMAND_CENTER_PATH/);
  for (const endpoint of WORKSPACE_READ_ENDPOINTS.slice(1)) {
    assert.ok(endpoint.startsWith(BUSINESS_API_PREFIX));
    assert.equal(isBusinessApiPath(endpoint), true, `${endpoint} is not a business API path`);
  }
  assert.match(web, /url\.pathname === "\/mcp"/);
  assert.equal(COMMAND_ENDPOINT, "/mcp");

  // Tours is deliberately not a J101 route. The router carries /tours; declaring it
  // here would be naming an affordance this release does not ship.
  assert.equal(served.has("/tours"), true, "the router does serve /tours");
  assert.equal(WORKSPACE_ROUTES.includes("/tours"), false, "and J101 does not claim it");
});

test("the acknowledgement endpoints are exactly the verbs the board actually writes", async () => {
  const liveClient = await read("dealroom/js/live-client.js");

  // Derived, not asserted from memory: every verb the board sends through `write(...)`,
  // read back off the client that sends them.
  const written = [...new Set([...liveClient.matchAll(/\bwrite\('([a-z0-9-]+)'/g)].map((match) => match[1]))].sort();
  assert.ok(written.length > 0, "no write verbs found in live-client.js");
  assert.deepEqual([...COMMAND_WRITE_VERBS], written);

  // The board's patchable fields travel on one of these verbs, so the axis covers
  // every cell the Deal Room can change.
  assert.ok(COMMAND_WRITE_VERBS.includes("patch-deal-field") && COMMAND_WRITE_VERBS.includes("revert-deal-field"));
  assert.deepEqual(DEAL_ROOM_FIELDS, ["phase", "owner", "attention", "next_date", "operating_state"]);

  // One acknowledgement subject per verb, on the one mount that carries them.
  assert.equal(ACKNOWLEDGEMENT_ENDPOINTS.length, COMMAND_WRITE_VERBS.length);
  ACKNOWLEDGEMENT_ENDPOINTS.forEach((endpoint, index) => {
    assert.equal(endpoint, `POST ${COMMAND_ENDPOINT} tools/call ${COMMAND_WRITE_VERBS[index]}`);
  });
  // Read-only surfaces issue none of them.
  const businessJs = await read("dealroom/js/workspace-business.js");
  const homeJs = await read("dealroom/js/workspace-command-center.js");
  for (const surface of [businessJs, homeJs]) {
    assert.doesNotMatch(surface, /tools\/call/);
    assert.doesNotMatch(surface, /method:\s*"(POST|PUT|PATCH|DELETE)"/);
  }
});

test("the surface inventory declares no measurement, no threshold and no receipt", async () => {
  const declared = withoutComments(await read("mcp-server/src/workspace-surface-inventory.js"));

  // It declares no latency figure. Checked on the DECLARATIONS, not on the prose
  // that explains why there are none.
  assert.doesNotMatch(declared, /p95|percentile|latency|_ms\b|milliseconds?\b|duration|elapsed/i);
  assert.doesNotMatch(declared, /threshold|budget|slo\b|receipt|acceptance|manifest|measure/i);

  // And it reads no service-level constant: no import of the benchmark module, and
  // no number anywhere in anything it exports. BENCHMARK_SLO_THRESHOLDS is imported
  // HERE, read-only, purely to prove the inventory shares nothing with it.
  assert.doesNotMatch(declared, /benchmark/i);
  assert.doesNotMatch(declared, /\bfrom\s+["'][^"']*benchmark/);
  const exported = { ACKNOWLEDGEMENT_ENDPOINTS, AUTHENTICATED_SURFACES, COMMAND_ENDPOINT, COMMAND_WRITE_VERBS, WORKSPACE_ASSET_PATHS, WORKSPACE_READ_ENDPOINTS, WORKSPACE_ROUTES };
  const serialized = JSON.stringify(exported);

  // No exported value is a number. A figure is how a measurement gets in, and the
  // inventory names subjects only — so every leaf it holds is a string or a boolean.
  const leaves = [];
  (function walk(value) {
    if (Array.isArray(value)) return value.forEach(walk);
    if (value && typeof value === "object") return Object.values(value).forEach(walk);
    leaves.push(value);
  })(exported);
  assert.ok(leaves.length > 0);
  for (const leaf of leaves) {
    assert.ok(typeof leaf === "string" || typeof leaf === "boolean", `${JSON.stringify(leaf)} is not a subject name`);
  }
  // The only digits it may carry are the API version segment the router already
  // serves. A multi-digit run is what a duration or a threshold looks like.
  for (const digits of serialized.match(/\d+/g) || []) {
    assert.equal(digits, "1", `"${digits}" reads as a figure, not as a path segment`);
  }
  for (const [key, value] of Object.entries(BENCHMARK_SLO_THRESHOLDS)) {
    assert.doesNotMatch(serialized, new RegExp(key, "i"), `the inventory must not restate ${key}`);
    assert.doesNotMatch(serialized, new RegExp(`\\b${value}\\b`), `the inventory must not restate ${key}'s value`);
  }
  // shellCoverage counts surfaces, which is why it is a function and not a stored
  // figure: it can never be read as an observation of a running deployment.
  assert.equal(typeof shellCoverage, "function");
});

// ---------------------------------------------------------------------------
// Clause 8 — the three negatives, so none of the above can pass vacuously.
// ---------------------------------------------------------------------------

test("negative: a surface file that drops the workspace stylesheet fails the inventory", async () => {
  const surface = surfaceForAsset("workspace.html");
  const html = await read("dealroom/workspace.html");
  assert.deepEqual(shellMismatches(surface, html), [], "the real file matches its declaration");

  const withoutStylesheet = html.replace('<link rel="stylesheet" href="/css/workspace.css">', "");
  assert.deepEqual(shellMismatches(surface, withoutStylesheet),
    ["workspace.html.workspace_stylesheet: declared true, file says false"]);

  // The other direction too: a surface declared off the shell that quietly picks it
  // up is just as much a drift, and the single-shell unit must announce itself.
  const dealRoom = surfaceForAsset("index.html");
  const adopted = `<link rel="stylesheet" href="/css/workspace.css">${await read("dealroom/index.html")}`;
  assert.deepEqual(shellMismatches(dealRoom, adopted),
    ["index.html.workspace_stylesheet: declared false, file says true"]);
});

test("negative: a dealroom/test suite added and not imported fails", async () => {
  const shim = await readFile(SHIM, "utf8");
  const real = (await readdir(DEALROOM_TEST_DIR)).filter((file) => file.endsWith(".test.mjs")).sort();
  assert.deepEqual(orphanedSuites(real, shim), []);

  // The next suite someone adds to that directory, before they remember the shim.
  assert.deepEqual(orphanedSuites([...real, "tomorrows-suite.test.mjs"], shim), ["tomorrows-suite.test.mjs"]);
  // A shim importing only the original two Home suites orphans every other
  // real suite on disk today — not a fixed historical count, since `real`
  // above is read live: this list grows exactly when a suite is added to
  // dealroom/test, the same event orphanedSuites exists to catch.
  const twoOnly = 'import "../../dealroom/test/workspace-command-center-model.test.mjs";\nimport "../../dealroom/test/workspace-command-center-static.test.mjs";';
  assert.deepEqual(orphanedSuites(real, twoOnly),
    real.filter((file) => !file.startsWith("workspace-command-center-")));
});

test("negative: an inventory entry naming a route the router does not serve fails", async () => {
  const served = routerExactPaths(await read("mcp-server/src/dealroom-web.js"));
  assert.deepEqual(unservedRoutes(WORKSPACE_ROUTES, served), []);

  assert.deepEqual(unservedRoutes([...WORKSPACE_ROUTES, "/timelines"], served), ["/timelines"]);
  assert.deepEqual(unservedRoutes(["/clients", "/doc", "/deals"], served), ["/doc"]);
  // A route the router serves under a different spelling is still not served.
  assert.deepEqual(unservedRoutes(["/clients/"], served), ["/clients/"]);
});
