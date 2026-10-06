// The browser half of the Clients and Vendors unit. It lives under mcp-server's
// `node --test test/*.test.mjs` glob for the same reason the Home browser suite
// does: dealroom/ carries no runner of its own, so anything asserted only there
// would sit outside the merge gate.
import test from "node:test";
import assert from "node:assert/strict";
import {
  DATASET_LABEL, DEFAULT_SCOPE, NOT_RECORDED, PAGE_SIZE, PIPELINE_LABEL, REFUSAL_COPY, SCOPE_LABEL, SORT_LABEL,
  SOURCE_LABEL, acceptsResponse, cachedPayload, clientPipelineTone, createBusinessState, datasetForPath,
  defaultQuery, displayedFreshness, echoesQuery, emptyCopy, expireSession, filterChips, freshnessSignature,
  hasActiveFilters, isSessionExpiry, listPhase, listRequestUrl, ownerPresentation, pageSummary, panelModality,
  panelTabTarget, parseViewState, partyKindText, recordRequestUrl, recordSections, recordedCode, recordedValue, refusalCopy,
  rememberPayload, restoreSession, rowTone, sameQuery, scrollIntent, searchBoxValue, sourceIsFresh,
  validListPayload, validRecordPayload, vendorDispositionTone, viewHref,
} from "../../dealroom/js/workspace-business-model.js";
import { parseBusinessQuery, readBusinessList, readBusinessRecord } from "../src/workspace-business-read.js";

const ID = "3f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8";
const OTHER_ID = "11111111-2222-3333-4444-555555555555";
const OBSERVED = "2026-09-10T15:00:00.000Z";
const INSIDE = () => Date.parse("2026-09-10T15:00:30.000Z");
const OUTSIDE = () => Date.parse("2026-09-10T15:02:00.000Z");

const clientRow = (overrides = {}) => ({
  id: ID, ref: "C-101", name: "Ridgeline Dental", party_kind: "org", city: "Ocala", state: "FL",
  recorded_status: "active", recorded_status_label: "Active", recorded_status_active_pipeline: true,
  recorded_etl_status: "signed", recorded_client_type: "practice", recorded_client_type_label: "Practice",
  vertical: "dental", owner_label: "Joe", owned_by_viewer: true, updated_at: OBSERVED, ...overrides,
});

const vendorRow = (overrides = {}) => ({
  id: ID, ref: "V-CPA-006", name: "Gulfside CPA", party_kind: "org", city: "Tampa", state: "FL",
  recorded_category: "cpa", recorded_category_label: "CPA", recorded_stage: "warm", recorded_stage_label: "Warm",
  recorded_disposition: "active", recorded_disposition_label: "Active", recorded_disposition_workable: true,
  relationship_level: 3, relationship_level_label: "Trusted", referral_active: true, is_target: false,
  out_of_market: false, last_touch: "2026-08-30", owner_label: "Dell", owned_by_viewer: false,
  updated_at: OBSERVED, ...overrides,
});

const clientRecord = (overrides = {}) => ({
  ...clientRow(), county: "Marion", title: "Practice owner", specialty: "General dentistry", npi: "1234567890",
  phone: "352-555-0100", cell: null, email: "office@example.com", contact_state: "active", contact_state_reason: null,
  contact_state_until: null, contact_state_cadence: null, recorded_status_note: "In an active search",
  subtype: "multi-site", acquisition_source: "referral", acquisition_detail: null, contact_label: "Front desk",
  deal_type_label: "Lease", specialty_type_label: "Dental", possible_duplicate_label: null, notes: null,
  record_version: 4, created_at: "2026-01-04T10:00:00.000Z", ...overrides,
});

const vendorRecord = (overrides = {}) => ({
  ...vendorRow(), county: "Hillsborough", title: "Partner", cell: null, phone: "813-555-0111",
  email: "hello@example.com", contact_state: "active", contact_state_reason: null, contact_state_until: null,
  contact_state_cadence: null, relationship_level_note: "Has sent work both ways", verticals: ["dental", "vet"],
  territory: "Tampa Bay", offers: "Practice accounting", seeking: "Startup practices", rivalry_group: "cpa",
  originated: "conference", intro_notes: null, links_label: null, record_version: 2,
  created_at: "2026-02-11T10:00:00.000Z", ...overrides,
});

const source = (overrides = {}) => ({
  source: "client", source_ref: "client+party+client_status+client_type", observed_at: OBSERVED,
  valid_until: "2026-09-10T15:01:00.000Z", freshness: "fresh", correlation_id: "corr", safe_explanation: "fresh", ...overrides,
});

const listPayload = (overrides = {}) => ({
  viewer: "joe", dataset: "clients",
  query: { dataset: "clients", scope: "team", q: null, sort: "name", page: 1, page_size: PAGE_SIZE, status: null, type: null, pipeline: "any" },
  total: 1, page: 1, page_size: PAGE_SIZE, page_count: 1, out_of_range: false, rows: [clientRow()],
  facets: { statuses: [{ slug: "active", label: "Active", is_active_pipeline: true }], types: [{ slug: "practice", label: "Practice" }] },
  partial: null, recorded_field_note: "Status and ETL status are fields recorded on the client record.",
  source: source(), ...overrides,
});

const recordPayload = (overrides = {}) => ({
  viewer: "joe", dataset: "clients", record: clientRecord(), partial: null,
  not_in_this_read: ["assignments", "negotiations"], recorded_field_note: "recorded", source: source(), ...overrides,
});

test("the two routes are the only datasets and the URL carries the whole view", () => {
  assert.equal(datasetForPath("/clients"), "clients");
  assert.equal(datasetForPath("/vendors"), "vendors");
  assert.equal(datasetForPath("/business.html"), null);
  assert.equal(datasetForPath("/deals"), null);
  const parsed = parseViewState("/clients", "?scope=mine&q=ridge&status=active&type=practice&pipeline=active&sort=recent&page=3&record=" + ID);
  assert.deepEqual(parsed.query, { dataset: "clients", scope: "mine", q: "ridge", status: "active", type: "practice", pipeline: "active", sort: "recent", page: 3 });
  assert.equal(parsed.recordId, ID);
  // Anything unrecognised is dropped rather than half-applied, and the canonical
  // href is what the address bar is rewritten to.
  const junk = parseViewState("/vendors", "?scope=everyone&sort=owner&page=0&category=bad%20slug&record=nope&owner=dell");
  assert.deepEqual(junk.query, { ...defaultQuery("vendors") });
  assert.equal(junk.recordId, null);
  assert.equal(viewHref(defaultQuery("clients")), "/clients");
  assert.equal(viewHref({ ...defaultQuery("clients"), scope: "mine", q: "ridge line", page: 2 }), "/clients?scope=mine&q=ridge+line&page=2");
  assert.equal(viewHref(defaultQuery("vendors"), ID), `/vendors?record=${ID}`);
  // A round trip is lossless, which is what makes Back restore a place.
  const round = parseViewState("/clients", viewHref(parsed.query, ID).split("?")[1]);
  assert.deepEqual(round.query, parsed.query);
});

test("the request the browser sends carries only filters and never the open record", () => {
  assert.equal(listRequestUrl(defaultQuery("clients")), "/api/v1/business/clients");
  assert.equal(listRequestUrl({ ...defaultQuery("vendors"), stage: "warm", scope: "mine", page: 4 }),
    "/api/v1/business/vendors?scope=mine&stage=warm&page=4");
  assert.equal(recordRequestUrl("clients", ID), `/api/v1/business/clients/${ID}`);
  // Every parameter the browser can produce is one the server's parser accepts.
  const search = new URLSearchParams(listRequestUrl({ ...defaultQuery("clients"), scope: "mine", q: "ridge", status: "active", type: "practice", pipeline: "active", sort: "recent", page: 2 }).split("?")[1]);
  assert.doesNotThrow(() => parseBusinessQuery("clients", search, "joe"));
});

test("scope and filter state are visible, removable and comparable", () => {
  const base = defaultQuery("clients");
  assert.equal(hasActiveFilters(base), false);
  assert.equal(hasActiveFilters({ ...base, scope: "mine" }), true);
  assert.equal(hasActiveFilters({ ...base, page: 3 }), false, "a page is a position, not a narrowing");
  assert.equal(sameQuery(base, { ...base }), true);
  assert.equal(sameQuery(base, { ...base, page: 2 }), false);
  assert.equal(sameQuery(base, defaultQuery("vendors")), false);
  const chips = filterChips({ ...base, scope: "mine", q: "ridge", status: "active", pipeline: "active" }, listPayload().facets);
  assert.deepEqual(chips.map((chip) => chip.key), ["scope", "q", "status", "pipeline"]);
  assert.equal(chips.find((chip) => chip.key === "status").value, "Active");
  assert.equal(chips.find((chip) => chip.key === "pipeline").reset, "any");
  assert.equal(chips.find((chip) => chip.key === "q").reset, "");
  // A recorded code with no lookup label still shows as itself in the chip.
  assert.equal(filterChips({ ...base, status: "legacy_code" }, { statuses: [] })[0].value, "legacy_code");
  assert.equal(DEFAULT_SCOPE, "team");
});

test("a payload is only rendered when it is this view's answer, in a shape that agrees with itself", () => {
  assert.equal(validListPayload(listPayload(), "clients"), true);
  assert.equal(validListPayload(listPayload(), "vendors"), false);
  assert.equal(validListPayload(listPayload({ extra: 1 }), "clients"), false);
  assert.equal(validListPayload(listPayload({ viewer: "stranger" }), "clients"), false);
  assert.equal(validListPayload(listPayload({ rows: [{ ...clientRow(), surprise: true }] }), "clients"), false);
  assert.equal(validListPayload(listPayload({ rows: [{ ...clientRow(), id: "not-a-uuid" }] }), "clients"), false);
  // A page of rows that reaches past its own total is a disagreement, not a rounding difference.
  assert.equal(validListPayload(listPayload({ total: 0 }), "clients"), false);
  assert.equal(validListPayload(listPayload({ total: 30, page: 2, page_count: 2, rows: [clientRow()] }), "clients"), true);
  assert.equal(validListPayload(listPayload({ total: 26, page_count: 1 }), "clients"), false);
  assert.equal(validListPayload(listPayload({ total: 26, page_count: 2, page: 3, out_of_range: false, rows: [] }), "clients"), false);
  assert.equal(validListPayload(listPayload({ source: source({ source: "vendor" }) }), "clients"), false);
  assert.equal(validListPayload(listPayload({ source: source({ valid_until: OBSERVED }) }), "clients"), false);
  assert.equal(validListPayload(listPayload({ partial: { kind: "unlabelled_recorded_codes", count: 0, fields: [], note: "x" } }), "clients"), false);
  // The partial sentence counts records on screen, so it can never claim more
  // of them than were sent — whatever a future server decides to count.
  const flagged = { kind: "unlabelled_recorded_codes", count: 1, fields: ["recorded_status"], note: "x" };
  assert.equal(validListPayload(listPayload({ partial: flagged }), "clients"), true);
  assert.equal(validListPayload(listPayload({ partial: { ...flagged, count: 2 } }), "clients"), false, "one row cannot hold two flagged records");
  assert.equal(validListPayload(listPayload({ total: 0, rows: [], partial: flagged }), "clients"), false, "no rows, nothing to flag");
  assert.equal(validRecordPayload(recordPayload({ partial: flagged }), "clients", ID), true);
  assert.equal(validRecordPayload(recordPayload({ partial: { ...flagged, count: 2 } }), "clients", ID), false, "one record is at most one");
  assert.equal(validListPayload(listPayload({ facets: { statuses: [] } }), "clients"), false);
  assert.equal(validRecordPayload(recordPayload(), "clients", ID), true);
  assert.equal(validRecordPayload(recordPayload(), "clients", OTHER_ID), false, "a record answer must be the record that was asked for");
  assert.equal(validRecordPayload(recordPayload({ record: { ...clientRecord(), extra: 1 } }), "clients"), false);
  assert.equal(validRecordPayload({ ...recordPayload(), record: vendorRecord() }, "clients"), false);
  assert.equal(validRecordPayload(recordPayload({ dataset: "vendors" }), "clients"), false);
});

test("an answer to a different filter is refused however fresh it is", () => {
  const query = { ...defaultQuery("clients"), scope: "mine", q: "ridge", status: "active", page: 2 };
  const echo = { dataset: "clients", scope: "mine", q: "ridge", sort: "name", page: 2, page_size: PAGE_SIZE, status: "active", type: null, pipeline: "any" };
  assert.equal(echoesQuery({ query: echo }, query), true);
  assert.equal(echoesQuery({ query: { ...echo, q: null } }, query), false);
  assert.equal(echoesQuery({ query: { ...echo, page: 1 } }, query), false);
  assert.equal(echoesQuery({ query: { ...echo, scope: "team" } }, query), false);
  assert.equal(echoesQuery({ query: { ...echo, status: null } }, query), false);
  assert.equal(echoesQuery({ query: { ...echo, dataset: "vendors" } }, query), false);
  assert.equal(echoesQuery({}, query), false);
  // The sequence guard is the other half: a stale response never paints.
  assert.equal(acceptsResponse(4, 4), true);
  assert.equal(acceptsResponse(5, 4), false);
  assert.equal(acceptsResponse(4, 5), false);
});

test("loading, refreshing, stale, both empties, past-the-end, unauthorized and unavailable are eight different states", () => {
  const query = defaultQuery("clients");
  const phase = (status, payload, options = {}) => listPhase({ status, payload, query: options.query || query, dataset: "clients", now: options.now || INSIDE });
  assert.equal(phase("loading", null), "loading");
  assert.equal(phase("refreshing", null), "loading", "there is nothing to refresh without an earlier answer");
  assert.equal(phase("refreshing", listPayload()), "refreshing");
  assert.equal(phase("ready", listPayload()), "ready");
  assert.equal(phase("ready", listPayload(), { now: OUTSIDE }), "stale");
  assert.equal(phase("ready", listPayload({ total: 0, rows: [] })), "empty-no-records");
  assert.equal(phase("ready", listPayload({ total: 0, rows: [] }), { query: { ...query, q: "nobody" } }), "empty-no-matches");
  assert.equal(phase("ready", listPayload({ total: 30, page: 5, page_count: 2, out_of_range: true, rows: [] })), "out-of-range");
  assert.equal(phase("unauthorized", listPayload()), "unauthorized");
  assert.equal(phase("error", null), "unavailable");
  // A known sign-out outranks whatever answer is still in hand.
  assert.equal(listPhase({ status: "ready", payload: listPayload(), query, dataset: "clients", signedOut: true, now: INSIDE }), "unauthorized");
  assert.equal(emptyCopy("clients", "empty-no-records").title, "No clients yet");
  assert.equal(emptyCopy("clients", "empty-no-matches").title, "No clients match these filters");
  assert.match(emptyCopy("vendors", "out-of-range").copy, /past the end/);
  assert.notEqual(emptyCopy("vendors", "empty-no-records").title, emptyCopy("vendors", "empty-no-matches").title);
  // Each refusal says its own thing; none of them says "no records".
  for (const code of ["AUTHENTICATION_REQUIRED", "AUTHORIZATION_REFUSED", "QUERY_INVALID", "RECORD_NOT_FOUND",
    "VIEWER_OWNER_UNKNOWN", "FRESHNESS_UNKNOWN", "DEPENDENCY_UNAVAILABLE", "DEPENDENCY_NOT_PROVISIONED", "offline"]) {
    assert.doesNotMatch(refusalCopy(code), /^No records/);
    assert.notEqual(refusalCopy(code), refusalCopy("INTERNAL_ERROR"), code);
  }
  // A deployment that has not been given access says so, and says nothing about
  // the records themselves or about the database.
  assert.match(refusalCopy("DEPENDENCY_NOT_PROVISIONED"), /has not been given access/);
  assert.doesNotMatch(refusalCopy("DEPENDENCY_NOT_PROVISIONED"), /grant|role|table|select|schema|sql/i);
});

test("freshness is measured against the current clock and repaints only when it changes", () => {
  assert.equal(sourceIsFresh(source(), "clients", INSIDE), true);
  assert.equal(sourceIsFresh(source(), "clients", OUTSIDE), false);
  assert.equal(displayedFreshness(source(), "clients", INSIDE), "fresh");
  assert.equal(displayedFreshness(source(), "clients", OUTSIDE), "expired");
  assert.equal(displayedFreshness(source({ freshness: "unknown" }), "clients", INSIDE), "unknown");
  assert.equal(displayedFreshness(null, "clients", INSIDE), "unknown");
  const payload = listPayload();
  assert.equal(freshnessSignature(payload, "clients", INSIDE), freshnessSignature(payload, "clients", INSIDE));
  assert.notEqual(freshnessSignature(payload, "clients", INSIDE), freshnessSignature(payload, "clients", OUTSIDE));
});

test("the page window is the server's, said in words, including past the last page", () => {
  assert.deepEqual(pageSummary(listPayload({ total: 0, rows: [] })).text, "0 records");
  const full = listPayload({ total: 60, page: 2, page_count: 3, rows: Array.from({ length: PAGE_SIZE }, (_, index) => clientRow({ id: ID.slice(0, -1) + index.toString(16) })) });
  const summary = pageSummary(full);
  assert.equal(summary.text, "26–50 of 60");
  assert.equal(summary.hasPrevious, true);
  assert.equal(summary.hasNext, true);
  const beyond = pageSummary(listPayload({ total: 30, page: 4, page_count: 2, out_of_range: true, rows: [] }));
  assert.match(beyond.text, /No records on page 4 · 30 match this filter/);
  assert.equal(beyond.total, 30, "the total stays truthful when the page is empty");
});

test("a nullable field stays unknown, and a recorded code without a label stays the code", () => {
  assert.deepEqual(recordedValue(null), { known: false, text: NOT_RECORDED });
  assert.deepEqual(recordedValue(""), { known: false, text: NOT_RECORDED });
  assert.deepEqual(recordedValue([]), { known: false, text: NOT_RECORDED });
  assert.deepEqual(recordedValue(0), { known: true, text: "0" });
  assert.deepEqual(recordedValue(false), { known: true, text: "No" });
  assert.deepEqual(recordedValue(true), { known: true, text: "Yes" });
  assert.deepEqual(recordedValue(["dental", "vet"]), { known: true, text: "dental, vet" });
  assert.deepEqual(recordedCode(null, null), { known: false, resolved: false, text: NOT_RECORDED, code: null });
  assert.deepEqual(recordedCode("legacy_code", null), { known: true, resolved: false, text: "legacy_code", code: "legacy_code" });
  assert.deepEqual(recordedCode("active", "Active"), { known: true, resolved: true, text: "Active", code: "active" });
});

test("warm and active are told apart only by what is recorded, and unknown is its own answer", () => {
  assert.deepEqual(clientPipelineTone(clientRow()), { tone: "active", label: "In the active pipeline" });
  assert.deepEqual(clientPipelineTone(clientRow({ recorded_status_active_pipeline: false })), { tone: "warm", label: "Not in the active pipeline" });
  assert.deepEqual(clientPipelineTone(clientRow({ recorded_status_active_pipeline: null })), { tone: "unknown", label: "Pipeline not set" });
  assert.equal(clientPipelineTone(clientRow({ recorded_status_active_pipeline: null })).tone, "unknown", "an unknown flag is never folded into warm");
  assert.deepEqual(rowTone("clients", clientRow()), clientPipelineTone(clientRow()));
  assert.deepEqual(rowTone("vendors", vendorRow()), vendorDispositionTone(vendorRow()));
  assert.equal(vendorDispositionTone(vendorRow({ recorded_disposition_workable: false })).tone, "warm");
  assert.equal(vendorDispositionTone(vendorRow({ recorded_disposition_workable: null })).tone, "unknown");
  // Nothing in the presentation claims a signed engagement or an assignment.
  for (const tone of [clientPipelineTone(clientRow()), clientPipelineTone(clientRow({ recorded_status_active_pipeline: false }))]) {
    assert.doesNotMatch(tone.label, /signed|engaged|assigned|represent/i);
  }
});

test("the owner label is shown as recorded and is never the thing that decides My work", () => {
  const mine = ownerPresentation(clientRow());
  assert.equal(mine.text, "Joe");
  assert.equal(mine.ownedByViewer, true);
  const theirs = ownerPresentation(clientRow({ owner_label: "Joe", owned_by_viewer: false }));
  assert.equal(theirs.text, "Joe");
  assert.equal(theirs.ownedByViewer, false, "the recorded label does not make a record yours");
  const unlabelled = ownerPresentation(clientRow({ owner_label: null }));
  assert.equal(unlabelled.text, NOT_RECORDED);
  assert.equal(unlabelled.known, false);
  assert.match(mine.note, /shown as written on the record/);
  assert.doesNotMatch(mine.note, /authenticated|uuid|column|field/i, "the note is for a reader, not a schema");
});

test("people and organisations are named, not coded", () => {
  assert.deepEqual(partyKindText("org"), { known: true, text: "Organisation" });
  assert.deepEqual(partyKindText("person"), { known: true, text: "Person" });
  assert.deepEqual(partyKindText(null), { known: false, text: NOT_RECORDED });
  // An unexpected value is shown as stored rather than guessed at.
  assert.deepEqual(partyKindText("practice"), { known: true, text: "practice" });
});

test("the detail panel renders every field of the read model exactly once", () => {
  const shownElsewhere = {
    // party_kind rides the panel heading as a plain "Organisation"/"Person" chip.
    clients: ["id", "name", "party_kind", "recorded_status_label", "recorded_status_active_pipeline", "recorded_client_type_label", "owned_by_viewer"],
    vendors: ["id", "name", "party_kind", "recorded_category_label", "recorded_stage_label", "recorded_disposition_label", "relationship_level_label", "owned_by_viewer"],
  };
  for (const [dataset, record] of [["clients", clientRecord()], ["vendors", vendorRecord()]]) {
    const sections = recordSections(dataset, record);
    const keys = sections.flatMap((section) => section.fields.map((field) => field.key));
    assert.equal(new Set(keys).size, keys.length, `${dataset} renders no field twice`);
    assert.deepEqual([...keys, ...shownElsewhere[dataset]].sort(), Object.keys(record).sort(), `${dataset} leaves no field unrendered`);
    assert.ok(sections.every((section) => section.fields.length > 0));
  }
  const sparse = recordSections("clients", clientRecord({ notes: null, acquisition_detail: null, recorded_status_label: null }));
  const notes = sparse.flatMap((section) => section.fields).find((field) => field.key === "notes");
  assert.deepEqual({ text: notes.text, known: notes.known }, { text: NOT_RECORDED, known: false });
  const status = sparse.flatMap((section) => section.fields).find((field) => field.key === "recorded_status");
  assert.equal(status.text, "active");
  assert.equal(status.resolved, false, "a recorded code with no label is flagged, not relabelled");
});

// REGRESSION: a signed-out list once left the open record panel, its contact
// details and the remembered answers Back repaints fully intact on screen.
function openState(overrides = {}) {
  const state = createBusinessState();
  state.dataset = "vendors";
  state.query = defaultQuery("vendors");
  state.recordId = ID;
  state.facets = { categories: [{ slug: "cpa", label: "CPA" }], stages: [], dispositions: [] };
  state.list = { key: "/api/v1/business/vendors", status: "ready", payload: listPayload({ dataset: "vendors" }), code: null, sequence: 4 };
  state.record = { id: ID, status: "ready", payload: recordPayload({ dataset: "vendors", record: vendorRecord() }), code: null, sequence: 2 };
  state.cache.set("/api/v1/business/vendors", listPayload({ dataset: "vendors" }));
  state.cache.set("/api/v1/business/vendors?q=fixture", listPayload({ dataset: "vendors" }));
  return Object.assign(state, overrides);
}

test("a known sign-out erases the list, the open record, the remembered answers and both in-flight reads", () => {
  const state = openState();
  const cache = state.cache;
  const next = expireSession(state);

  assert.equal(next.signedOut, true);
  // Nothing readable is left holding a record the reader may no longer see.
  assert.equal(next.list.payload, null);
  assert.equal(next.record.payload, null);
  assert.equal(next.record.id, null);
  assert.equal(next.list.status, "unauthorized");
  assert.equal(next.record.status, "unauthorized");
  assert.equal(next.list.code, "AUTHENTICATION_REQUIRED");
  assert.equal(next.record.code, "AUTHENTICATION_REQUIRED");
  assert.deepEqual(next.facets, {});
  // Back and forward cannot repaint a remembered answer after a known expiry,
  // either because the cache is empty or because the door refuses to open.
  assert.equal(cache.size, 0);
  assert.equal(cachedPayload(next, "/api/v1/business/vendors"), null);
  rememberPayload(next, "/api/v1/business/vendors", listPayload({ dataset: "vendors" }));
  assert.equal(next.cache.size, 0, "a signed-out view does not start remembering again");
  assert.equal(cachedPayload(next, "/api/v1/business/vendors"), null);
  // Replies that left before the expiry cannot land after it.
  assert.equal(acceptsResponse(next.list.sequence, 4), false);
  assert.equal(acceptsResponse(next.record.sequence, 2), false);
  // And the panel renders as signed out rather than as a record.
  assert.equal(listPhase({ status: next.list.status, payload: next.list.payload, query: next.query, dataset: "vendors", signedOut: next.signedOut }), "unauthorized");
});

test("an ordinary failure is not a sign-out, and keeps refresh and Back working", () => {
  // Only the answer that means the session is over triggers the erasure.
  assert.equal(isSessionExpiry(401, undefined), true);
  assert.equal(isSessionExpiry("unauthorized", null), true);
  assert.equal(isSessionExpiry(200, "AUTHENTICATION_REQUIRED"), true);
  for (const [status, code] of [[503, "DEPENDENCY_UNAVAILABLE"], [500, "INTERNAL_ERROR"], [409, "FRESHNESS_UNKNOWN"],
    [400, "QUERY_INVALID"], [403, "AUTHORIZATION_REFUSED"], [404, "RECORD_NOT_FOUND"], [503, "offline"]]) {
    assert.equal(isSessionExpiry(status, code), false, code);
  }
  // A transient failure leaves the remembered answers alone, which is what lets
  // the stale/refresh path keep working once the trouble passes.
  const state = openState();
  assert.equal(state.cache.size, 2);
  assert.equal(cachedPayload(state, "/api/v1/business/vendors")?.dataset, "vendors");
  assert.equal(state.signedOut, false);
  // A verified answer is the only thing that clears a sign-out.
  const recovered = restoreSession(expireSession(openState()));
  assert.equal(recovered.signedOut, false);
  assert.equal(restoreSession(state), state, "an already-signed-in state is untouched");
});

test("opening, closing or swapping a record keeps the reader's place; a new list starts at the top", () => {
  // REGRESSION: every push navigated with scrollY 0, so clicking row 20 threw
  // the reader back to the top of the list they were reading.
  const query = { ...defaultQuery("clients"), scope: "mine", q: "ridge", page: 2 };
  assert.equal(scrollIntent(query, viewHref(query, ID)), "keep", "opening a record");
  assert.equal(scrollIntent(query, viewHref(query)), "keep", "closing it again");
  assert.equal(scrollIntent(query, viewHref(query, OTHER_ID)), "keep", "swapping to another record");
  assert.equal(scrollIntent(query, viewHref({ ...query, page: 3 })), "top", "a different page is a different list");
  assert.equal(scrollIntent(query, viewHref({ ...query, q: "other" })), "top", "a different search");
  assert.equal(scrollIntent(query, viewHref({ ...query, scope: "team" })), "top", "a different scope");
  assert.equal(scrollIntent(query, viewHref(defaultQuery("vendors"))), "top", "the other dataset");
  assert.equal(scrollIntent(null, viewHref(query)), "top", "nothing to preserve on a first load");
  assert.equal(scrollIntent(query, "/deals"), "top", "somewhere that is not this view at all");
});

test("Back reconciles the search box; an ordinary refresh never clobbers a draft", () => {
  // REGRESSION: the box was skipped whenever it had focus, so Back restored the
  // chips and rows while stale text stayed in the input.
  assert.equal(searchBoxValue({ current: "ridge", query: "", editing: true, fromLocation: true }), "",
    "history is authoritative even while the reader is in the box");
  assert.equal(searchBoxValue({ current: "ridge", query: "gulf", editing: false, fromLocation: true }), "gulf");
  assert.equal(searchBoxValue({ current: "ridg", query: "", editing: true, fromLocation: false }), null,
    "a finished read never overwrites what is being typed");
  assert.equal(searchBoxValue({ current: "stale", query: "ridge", editing: false, fromLocation: false }), "ridge",
    "an unfocused box still catches up on an ordinary repaint");
  assert.equal(searchBoxValue({ current: "ridge", query: "ridge", editing: true, fromLocation: true }), null,
    "no needless write, so the caret is left alone when nothing changed");
  assert.equal(searchBoxValue({ current: "", query: null, editing: false, fromLocation: true }), null);
});

test("the phone panel is a dialog and the desktop panel is not", () => {
  // REGRESSION: the full-screen phone panel kept role="complementary" with no
  // aria-modal and no inert background, so Tab walked into the covered list.
  assert.equal(panelModality({ recordId: ID, phoneWidth: true }), "modal");
  assert.equal(panelModality({ recordId: ID, phoneWidth: false }), "inline");
  assert.equal(panelModality({ recordId: null, phoneWidth: true }), "closed");
  assert.equal(panelModality({ recordId: null, phoneWidth: false }), "closed");
});

test("Tab inside the phone dialog is decided for every position, including the heading it opens on", () => {
  const stops = 3;
  // REGRESSION: the panel opens with focus on its heading, which carries
  // tabindex="-1" and so is inside the dialog but is not one of the stops. That
  // position fell through to the browser, and the first Shift+Tab after opening
  // walked out into the background wherever `inert` is unsupported.
  assert.equal(panelTabTarget({ inside: true, stopIndex: -1, stopCount: stops, shiftKey: true }), "last");
  assert.equal(panelTabTarget({ inside: true, stopIndex: -1, stopCount: stops, shiftKey: false }), "first");
  // The two ends still wrap, and the middle is still the browser's to move.
  assert.equal(panelTabTarget({ inside: true, stopIndex: stops - 1, stopCount: stops, shiftKey: false }), "first");
  assert.equal(panelTabTarget({ inside: true, stopIndex: 0, stopCount: stops, shiftKey: true }), "last");
  assert.equal(panelTabTarget({ inside: true, stopIndex: 0, stopCount: stops, shiftKey: false }), null);
  assert.equal(panelTabTarget({ inside: true, stopIndex: 1, stopCount: stops, shiftKey: true }), null);
  assert.equal(panelTabTarget({ inside: true, stopIndex: stops - 1, stopCount: stops, shiftKey: true }), null);
  // A single stop wraps onto itself in both directions rather than escaping.
  assert.equal(panelTabTarget({ inside: true, stopIndex: 0, stopCount: 1, shiftKey: false }), "first");
  assert.equal(panelTabTarget({ inside: true, stopIndex: 0, stopCount: 1, shiftKey: true }), "last");
  // Focus that has already left is pulled back; a dialog with nothing tabbable
  // keeps focus on its heading either way.
  assert.equal(panelTabTarget({ inside: false, stopIndex: -1, stopCount: stops, shiftKey: true }), "first");
  assert.equal(panelTabTarget({ inside: false, stopIndex: -1, stopCount: stops, shiftKey: false }), "first");
  assert.equal(panelTabTarget({ inside: true, stopIndex: -1, stopCount: 0, shiftKey: true }), "title");
  assert.equal(panelTabTarget({ inside: false, stopIndex: -1, stopCount: 0, shiftKey: false }), "title");
  // No position anywhere is left undecided except a genuine mid-list move.
  for (const shiftKey of [true, false]) {
    for (const stopIndex of [-1, 0, 1, 2]) {
      const target = panelTabTarget({ inside: true, stopIndex, stopCount: stops, shiftKey });
      const midList = stopIndex > 0 && stopIndex < stops - 1;
      const atWrappingEnd = shiftKey ? stopIndex === 0 : stopIndex === stops - 1;
      assert.equal(target === null, midList || (!atWrappingEnd && stopIndex !== -1),
        `stopIndex ${stopIndex}, shift ${shiftKey}`);
    }
  }
});

test("nothing a reader sees claims an agreement, a representation or an assignment", () => {
  const forbidden = /signed ETL|accepted representation|assignment created|representation agreement in place|engagement signed/i;
  const jargon = /lookup table|read model|canonical|tenant|uuid|payload|predicate|book of record|v5 /i;
  const rows = [clientRow(), clientRow({ recorded_status_active_pipeline: false }), clientRow({ recorded_status_active_pipeline: null })];
  const vendorRows = [vendorRow(), vendorRow({ recorded_disposition_workable: false }), vendorRow({ recorded_disposition_workable: null })];
  const copy = [
    ...Object.values(REFUSAL_COPY), ...Object.values(PIPELINE_LABEL), ...Object.values(SORT_LABEL),
    ...Object.values(SCOPE_LABEL), ...Object.values(DATASET_LABEL), ...Object.values(SOURCE_LABEL),
    NOT_RECORDED, ownerPresentation(clientRow()).note,
    ...rows.map((row) => clientPipelineTone(row).label),
    ...vendorRows.map((row) => vendorDispositionTone(row).label),
    ...["empty-no-records", "empty-no-matches", "out-of-range"].flatMap((phase) =>
      ["clients", "vendors"].flatMap((dataset) => [emptyCopy(dataset, phase).title, emptyCopy(dataset, phase).copy])),
    ...filterChips({ ...defaultQuery("clients"), scope: "mine", q: "a", status: "active", type: "practice", pipeline: "active", sort: "recent" }, {}).flatMap((chip) => [chip.label, chip.value]),
    ...["clients", "vendors"].flatMap((dataset) =>
      recordSections(dataset, dataset === "clients" ? clientRecord() : vendorRecord()).flatMap((section) => [section.title, ...section.fields.map((field) => field.label)])),
  ];
  for (const line of copy) {
    assert.equal(typeof line, "string");
    assert.doesNotMatch(line, forbidden, line);
    assert.doesNotMatch(line, jargon, line);
  }
});

test("the server's own answer is accepted by the browser model, for both datasets", async () => {
  const clientFor = (rows, facets) => ({
    async query(text) {
      if (text.startsWith("with filtered")) return { rows: [{ total_count: rows.length, viewer_owner_resolved: true, rows, record: rows[0], match_count: 1 }] };
      return { rows: [facets] };
    },
  });
  const clients = await readBusinessList({
    client: clientFor([clientRow()], { statuses: [{ slug: "active", label: "Active", is_active_pipeline: true }], types: [] }),
    actor: { slug: "joe" }, query: parseBusinessQuery("clients", new URLSearchParams({ scope: "mine", q: "ridge" }), "joe"),
    correlationId: "corr-cross", now: () => new Date(OBSERVED),
  });
  assert.equal(validListPayload(clients, "clients"), true);
  assert.equal(echoesQuery(clients, { ...defaultQuery("clients"), scope: "mine", q: "ridge" }), true);
  assert.equal(listPhase({ status: "ready", payload: clients, query: { ...defaultQuery("clients"), scope: "mine", q: "ridge" }, dataset: "clients", now: INSIDE }), "ready");

  const vendors = await readBusinessList({
    client: clientFor([vendorRow()], { categories: [], stages: [], dispositions: [] }),
    actor: { slug: "dell" }, query: parseBusinessQuery("vendors", new URLSearchParams(), "dell"),
    correlationId: "corr-cross-v", now: () => new Date(OBSERVED),
  });
  assert.equal(validListPayload(vendors, "vendors"), true);
  assert.equal(validListPayload(vendors, "clients"), false);

  const record = await readBusinessRecord({
    client: clientFor([clientRecord()], {}), actor: { slug: "joe" }, dataset: "clients", id: ID,
    correlationId: "corr-cross-r", now: () => new Date(OBSERVED),
  });
  assert.equal(validRecordPayload(record, "clients", ID), true);
  assert.equal(recordSections("clients", record.record).length > 0, true);

  const vendorDetail = await readBusinessRecord({
    client: clientFor([vendorRecord()], {}), actor: { slug: "joe" }, dataset: "vendors", id: ID,
    correlationId: "corr-cross-vr", now: () => new Date(OBSERVED),
  });
  assert.equal(validRecordPayload(vendorDetail, "vendors", ID), true);
});
