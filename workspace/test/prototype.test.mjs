// Test workspace prototype surfaces and routes.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { resolvePrototypePath } from "../public/server.mjs";

const read = relative => readFile(new URL(`../${relative}`, import.meta.url), "utf8");

test("global shell has the settled modules and exactly one Call Mode control", async () => {
  const [html, app] = await Promise.all([read("public/index.html"), read("public/js/app.js")]);
  assert.equal((html.match(/id="call-mode"/g) ?? []).length, 1);
  assert.match(app, /Command Center/);
  assert.match(app, /The Lead Board/);
  assert.match(app, /The Deal Room/);
  assert.match(app, /Calls/);
  assert.match(app, /Marketing/);
  assert.match(app, /More/);
  assert.match(app, /Market Map & Trip Planner/);
  assert.doesNotMatch(`${html}\n${app}`, /Start agenda/i);
});

test("the six BDUF prototype journeys are implemented as local interactions", async () => {
  const app = await read("public/js/app.js");
  for (const action of ["confirm-park", "restore-record", "begin-recording", "complete-call", "request-queue", "request-add-evidence", "request-complete", "confirm-route", "capture-note", "capture-photo", "tour-offline", "resolve-tour-conflict", "tour-resume", "complete-tour-review"]) {
    assert.match(app, new RegExp(action));
  }
  assert.match(app, /Open owning source/);
  assert.match(app, /Consent gate/);
  assert.match(app, /Fresh verification/);
});

test("engineering request lifecycle is guarded and cannot be arbitrarily selected", async () => {
  const [app, fixtureText] = await Promise.all([read("public/js/app.js"), read("fixtures/doc-request.v1.json")]);
  const fixture = JSON.parse(fixtureText);
  assert.equal(fixture.states.normal.request.state, "draft");
  assert.doesNotMatch(app, /id="request-state"/);
  assert.match(app, /evidence\.fresh_tests && evidence\.fresh_read_back && evidence\.requester_visible_outcome/);
  assert.match(app, /request\.state === "in_progress"/);
  assert.match(app, /No backward transition is available/);
});

test("call review uses one session twin and exposes the complete structured detail", async () => {
  const [app, fixtureText] = await Promise.all([read("public/js/app.js"), read("fixtures/call-review.v1.json")]);
  const call = JSON.parse(fixtureText).states.normal.call;
  for (const section of ["facts", "decisions", "objectives_deliverables", "joe_actions", "dell_actions", "outside_party_needs", "draft_communications", "initiated_work_history"]) assert.ok(call.sections[section], section);
  assert.match(app, /model\.callSession = structuredClone\(data\.call\)/);
  assert.doesNotMatch(app, /callStage|candidateDisposition/);
  assert.match(app, /all_candidates_dispositioned/);
  assert.match(app, /aggregate_summary_confirmed/);
});

test("tour journey covers route parity, capture, offline exact resume, conflict, and review", async () => {
  const [app, fixtureText] = await Promise.all([read("public/js/app.js"), read("fixtures/tour.v1.json")]);
  const tour = JSON.parse(fixtureText).states.normal.tour;
  assert.ok(tour.stops.every(stop => stop.marker === stop.order));
  assert.equal(tour.route_confirmed, false);
  for (const text of ["Map/list parity", "Pencil-equivalent note", "Add synthetic photo", "Exact resume", "No last-write-wins", "Post-tour proposal"]) assert.match(app, new RegExp(text));
});

test("market map is an interactive visual planning surface with map/list parity", async () => {
  const [app, css, fixtureText] = await Promise.all([read("public/js/app.js"), read("public/css/app.css"), read("fixtures/market-map.v1.json")]);
  const market = JSON.parse(fixtureText).states.normal.market;
  const mapped = market.records.flatMap(record => record.locations).filter(location => location.position);
  assert.ok(mapped.length > 4);
  const implementation = `${app}\n${fixtureText}`;
  for (const text of ["Prospects", "Active clients", "Projects in process", "Visits / appointments", "All layers", "unlocated / needs location", "Map/list parity", "Location role", "Open Lead Board source", "Open Deal Room source"]) assert.match(implementation, new RegExp(text));
  assert.match(app, /class="market-marker/);
  assert.match(app, /aria-label="\$\{escape\(record\.record_type_label\)\}/);
  assert.match(app, /data-market-filter/);
  assert.match(app, /data-market-location/);
  assert.match(app, /data-market-stop/);
  assert.match(app, /data-market-preset/);
  assert.match(app, /data-market-list-location/);
  assert.match(app, /data-market-row/);
  assert.match(app, /CSS\.escape/);
  assert.match(css, /\.market-visual-grid/);
  assert.match(css, /\.market-marker/);
  assert.match(css, /body\.color-assist \.market-marker/);
  for (const truth of ["Mixed / Partial", "current records", "stale records", "Unknown records", "Planning horizon", "Whole-market view refused in Sterile mode", "dense-point clustering and pan/zoom are not simulated", "keyboard-focusable 48-pixel control"]) assert.match(app, new RegExp(truth));
  for (const insight of ["Travel opportunity", "explainable cross-layer match", "Exact source rows", "No opaque score"]) assert.match(implementation, new RegExp(insight));
  assert.match(app, /every\(layer => model\.marketFilters\[layer\]\)/);
});

test("trip planner preserves locked appointments and does not overclaim routing", async () => {
  const [app, fixtureText] = await Promise.all([read("public/js/app.js"), read("fixtures/market-map.v1.json")]);
  const market = JSON.parse(fixtureText).states.normal.market;
  assert.ok(market.visit_appointments.some(item => item.locked));
  for (const action of ["prepare-market-trip", "confirm-market-trip", "edit-market-trip"]) assert.match(app, new RegExp(action));
  for (const boundary of ["not a live or optimal route", "no live traffic", "Locked schedule anchors", "sequence pending verified travel estimates", "preserves locked appointments"]) assert.match(app, new RegExp(boundary, "i"));
  assert.match(app, /Planning refused/);
  assert.match(app, /stale, Unknown, or unmapped/);
  assert.match(app, /omitted because/);
  assert.match(app, /!candidate\?\.location\.trip_eligible/);
  assert.match(app, /candidate\?\.location\.trip_eligibility_reason/);
  for (const input of ["trip-start", "trip-end", "trip-dwell", "trip-buffer"]) assert.match(app, new RegExp(input));
  for (const evidence of ["provider", "method", "input_as_of", "skipped_stops", "live_traffic", "optimality_claim"]) assert.match(app, new RegExp(evidence));
  for (const canonical of ["map_item_id", "business_workspace_id", "record_type", "record_id", "location_source", "location_projection_version", "location_precision", "redaction_profile"]) assert.match(fixtureText, new RegExp(canonical));
  for (const filter of ["data-market-control=\\\"scope", "data-market-control=\\\"horizon", "data-market-control=\\\"freshness", "data-market-control=\\\"changed"]) assert.match(app, new RegExp(filter));
  assert.match(app, /model\.marketSelectedLocationIds = \[\]/);
  assert.match(app, /model\.marketDetailLocationId = null/);
  assert.match(app, /Alternative route comparison, route version, and planning fingerprint are future construction gates/);
  assert.match(app, /Open separate Tour Mode for downstream execution/);
  assert.match(app, /Tour Mode is separate/);
});

test("prototype has no mutation transport or external-effect button", async () => {
  const [html, app, client] = await Promise.all([read("public/index.html"), read("public/js/app.js"), read("public/js/client.js")]);
  assert.doesNotMatch(`${app}\n${client}`, /method:\s*["'](?:POST|PUT|PATCH|DELETE)["']/i);
  assert.doesNotMatch(`${app}\n${client}`, /\bWebSocket\b|EventSource|sendBeacon/);
  const buttonLabels = [...`${html}\n${app}`.matchAll(/<button\b[^>]*>([^<]*)<\/button>/gi)].map(match => match[1].trim());
  for (const label of buttonLabels) assert.doesNotMatch(label, /^(?:send|publish|deploy)(?:\s|$)/i);
});

test("Doc boundaries and transcript-library exclusion are explicit", async () => {
  const [html, app] = await Promise.all([read("public/index.html"), read("public/js/app.js")]);
  assert.match(html, /Doc cannot send, publish, spend, change ownership, move phases, or deploy/);
  assert.match(app, /There is no transcript library/);
  assert.match(app, /sending is structurally absent/i);
});

test("accessibility and responsive contracts are authored", async () => {
  const [html, css] = await Promise.all([read("public/index.html"), read("public/css/app.css")]);
  assert.match(html, /class="skip-link"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /aria-controls="doc-drawer"/);
  assert.match(css, /min-height:\s*44px/);
  assert.match(css, /@media \(max-width: 767px\)/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /:focus-visible/);
});

test("local server is loopback-only and path traversal guarded", async () => {
  const server = await read("public/server.mjs");
  assert.match(server, /"127\.0\.0\.1"/);
  assert.match(server, /const publicFiles = new Map/);
  assert.match(server, /const fixtureFiles = new Set/);
  assert.doesNotMatch(server, /pathname\.slice\(1\)/);
  assert.match(server, /"Cache-Control": "no-store"/);
  assert.match(server, /405/);
  assert.match(server, /"Allow": "GET, HEAD, OPTIONS"/);
});

test("advertised root and its assets resolve while internal workspace files remain unreachable", async () => {
  for (const path of ["/", "/index.html", "/css/app.css", "/js/app.js", "/js/client.js", "/fixtures/market-map.v1.json", "/fixtures/tour.v1.json"]) {
    const resolved = resolvePrototypePath(path);
    assert.ok(resolved, path);
    await assert.doesNotReject(() => readFile(resolved));
  }
  for (const path of ["/package.json", "/contracts/domain-glossary.v1.json", "/test/prototype.test.mjs", "/fixtures/not-allowed.v1.json", "/../package.json"]) assert.equal(resolvePrototypePath(path), null, path);
  const [html, client] = await Promise.all([read("public/index.html"), read("public/js/client.js")]);
  assert.match(html, /href="\/css\/app\.css"/);
  assert.match(html, /src="\/js\/app\.js"/);
  assert.match(client, /fetch\(`\/fixtures\/\$\{surface\}\.v1\.json`/);
});

test("Doc page context is visible, removable, and removal causes page-specific refusal", async () => {
  const [html, app] = await Promise.all([read("public/index.html"), read("public/js/app.js")]);
  assert.match(html, /id="doc-remove-context"/);
  assert.match(html, /id="doc-explain-page"/);
  assert.match(html, /id="doc-context-result"/);
  assert.match(app, /Page context removed\. Doc will not use or name this page/);
  assert.match(app, /Refused: page context was removed/);
  assert.match(app, /Explaining only the visible/);
});

test("Tour pack, map parity, and review completion gates are mechanical", async () => {
  const [app, fixtureText] = await Promise.all([read("public/js/app.js"), read("fixtures/tour.v1.json")]);
  const tour = JSON.parse(fixtureText).states.normal.tour;
  assert.equal(tour.offline_pack.status, "not_requested");
  assert.equal(tour.offline_pack.verified, false);
  assert.ok(tour.review_items.length > 1);
  assert.match(app, /stage = "confirmed"/);
  assert.match(app, /offline_pack\.status = "downloading"/);
  assert.match(app, /offline_pack\.status = "ready"/);
  assert.match(app, /stage === "confirmed" && model\.tourSession\.offline_pack\.status === "ready" && model\.tourSession\.offline_pack\.verified/);
  for (const stage of ["in_progress", "paused", "review_ready"]) assert.match(app, new RegExp(`stage = "${stage}"`));
  for (const drift of ["route_confirmed", "touring", "offline", "review"]) assert.doesNotMatch(app, new RegExp(`model\\.tourSession\\.stage = "${drift}"`));
  assert.match(app, /review_items\.every\(item => item\.disposition !== "pending"\)/);
  assert.match(app, /class="synthetic-map"/);
  assert.match(app, /data-stop-id=/);
});

test("Call purge eligibility requires every explicit retention gate", async () => {
  const [app, fixtureText] = await Promise.all([read("public/js/app.js"), read("fixtures/call-review.v1.json")]);
  const retention = JSON.parse(fixtureText).states.normal.call.retention;
  assert.equal(retention.legal_hold, false);
  assert.equal(retention.policy_resolved, false);
  assert.equal(retention.recovery_window_elapsed, false);
  assert.equal(retention.purge_eligible, false);
  assert.match(app, /retention\.legal_hold === false/);
  assert.match(app, /retention\.policy_resolved === true/);
  assert.match(app, /retention\.recovery_window_elapsed === true/);
  assert.doesNotMatch(app, /retention receipt is eligible/i);
  assert.match(app, /Retention eligibility: \$\{call\.retention\.purge_eligible \? "Eligible" : "Blocked"\}/);
  assert.match(app, /\$\{escape\(call\.retention\.reason\)\}/);
});
