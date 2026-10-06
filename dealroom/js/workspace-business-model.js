// The Clients and Vendors browser model: URL state, session state, payload
// validation and presentation, with no DOM in it.
//
// THIS FILE COUNTS NOTHING AND FILTERS NOTHING. The total, the page window and
// the filtered set are the server's answers; everything here either validates
// them or decides how to say them. That is deliberate — a browser-side count
// would be a second definition of "how many clients match", and the two would
// eventually disagree in front of a partner.
//
// It also never fills a blank. A missing value reads "Not recorded", never 0,
// never an empty string that reads as "none", and never a guessed label.

export const DATASETS = ["clients", "vendors"];
export const DATASET_ROUTE = { clients: "/clients", vendors: "/vendors" };
export const DATASET_LABEL = { clients: "Clients", vendors: "Vendors" };
export const DATASET_SINGULAR = { clients: "Client", vendors: "Vendor" };
export const SOURCE_LABEL = { clients: "Client records", vendors: "Vendor records" };
export const HOME_DESTINATION = "/";
export const API_PREFIX = "/api/v1/business/";
export const SCOPES = ["team", "mine"];
export const DEFAULT_SCOPE = "team";
export const SCOPE_LABEL = { team: "Team", mine: "My work" };
export const SORTS = ["name", "recent"];
export const DEFAULT_SORT = "name";
export const SORT_LABEL = { name: "Name (A–Z)", recent: "Recently updated" };
export const PIPELINE_FILTERS = ["any", "active", "other", "unknown"];
export const PIPELINE_LABEL = {
  any: "Any status",
  active: "In the active pipeline",
  other: "Not in the active pipeline",
  unknown: "Pipeline not set",
};
export const PAGE_SIZE = 25;
export const MAX_PAGE = 200;
export const MAX_QUERY_LENGTH = 80;
export const NOT_RECORDED = "Not recorded";
export const SIGNED_OUT_CODE = "AUTHENTICATION_REQUIRED";

const VIEWERS = new Set(["joe", "dell"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SLUG = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const FILTER_KEYS = { clients: ["status", "type", "pipeline"], vendors: ["category", "stage", "disposition"] };
const SOURCE_FOR = { clients: "client", vendors: "vendor" };

const LIST_ROW_KEYS = {
  clients: ["id", "ref", "name", "party_kind", "city", "state", "recorded_status", "recorded_status_label",
    "recorded_status_active_pipeline", "recorded_etl_status", "recorded_client_type", "recorded_client_type_label",
    "vertical", "owner_label", "owned_by_viewer", "updated_at"],
  vendors: ["id", "ref", "name", "party_kind", "city", "state", "recorded_category", "recorded_category_label",
    "recorded_stage", "recorded_stage_label", "recorded_disposition", "recorded_disposition_label",
    "recorded_disposition_workable", "relationship_level", "relationship_level_label", "referral_active",
    "is_target", "out_of_market", "last_touch", "owner_label", "owned_by_viewer", "updated_at"],
};

const RECORD_KEYS = {
  clients: ["id", "ref", "name", "party_kind", "city", "state", "county", "title", "specialty", "npi", "phone",
    "cell", "email", "contact_state", "contact_state_reason", "contact_state_until", "contact_state_cadence",
    "recorded_status", "recorded_status_label", "recorded_status_active_pipeline", "recorded_status_note",
    "recorded_etl_status", "recorded_client_type", "recorded_client_type_label", "vertical", "subtype",
    "acquisition_source", "acquisition_detail", "contact_label", "deal_type_label", "specialty_type_label",
    "possible_duplicate_label", "notes", "owner_label", "owned_by_viewer", "record_version", "created_at", "updated_at"],
  vendors: ["id", "ref", "name", "party_kind", "city", "state", "county", "title", "phone", "cell", "email",
    "contact_state", "contact_state_reason", "contact_state_until", "contact_state_cadence", "recorded_category",
    "recorded_category_label", "recorded_stage", "recorded_stage_label", "recorded_disposition",
    "recorded_disposition_label", "recorded_disposition_workable", "relationship_level", "relationship_level_label",
    "relationship_level_note", "verticals", "territory", "offers", "seeking", "rivalry_group", "originated",
    "referral_active", "is_target", "out_of_market", "last_touch", "intro_notes", "links_label", "owner_label",
    "owned_by_viewer", "record_version", "created_at", "updated_at"],
};

// ------------------------------------------------------------------ URL state

export function datasetForPath(pathname) {
  return DATASETS.find((dataset) => DATASET_ROUTE[dataset] === pathname) || null;
}

export function defaultQuery(dataset) {
  const base = { dataset, scope: DEFAULT_SCOPE, q: "", sort: DEFAULT_SORT, page: 1 };
  return dataset === "clients"
    ? { ...base, status: "", type: "", pipeline: "any" }
    : { ...base, category: "", stage: "", disposition: "" };
}

function readSlug(params, key) {
  const raw = params.get(key) || "";
  return SLUG.test(raw) ? raw : "";
}

function readPage(params) {
  const raw = params.get("page") || "";
  const value = Number(raw);
  return /^[1-9][0-9]{0,3}$/.test(raw) && value <= MAX_PAGE ? value : 1;
}

/**
 * The URL is the view's memory: search text, every filter, the sort, the page
 * and the open record all live in it, which is what makes Back restore a place
 * rather than reload a default. Anything unrecognised is dropped and the URL is
 * rewritten to its canonical form, so what is on screen and what is in the
 * address bar are the same thing.
 */
export function parseViewState(pathname, search) {
  const dataset = datasetForPath(pathname);
  if (!dataset) return null;
  const params = new URLSearchParams(search || "");
  const scope = SCOPES.includes(params.get("scope")) ? params.get("scope") : DEFAULT_SCOPE;
  const sort = SORTS.includes(params.get("sort")) ? params.get("sort") : DEFAULT_SORT;
  const rawQuery = (params.get("q") || "").trim().slice(0, MAX_QUERY_LENGTH);
  const query = { ...defaultQuery(dataset), scope, sort, q: rawQuery, page: readPage(params) };
  if (dataset === "clients") {
    query.status = readSlug(params, "status");
    query.type = readSlug(params, "type");
    query.pipeline = PIPELINE_FILTERS.includes(params.get("pipeline")) ? params.get("pipeline") : "any";
  } else {
    query.category = readSlug(params, "category");
    query.stage = readSlug(params, "stage");
    query.disposition = readSlug(params, "disposition");
  }
  const record = params.get("record");
  return { dataset, query, recordId: record && UUID.test(record) ? record.toLowerCase() : null };
}

function queryPairs(query) {
  const pairs = [];
  if (query.scope !== DEFAULT_SCOPE) pairs.push(["scope", query.scope]);
  if (query.q) pairs.push(["q", query.q]);
  for (const key of FILTER_KEYS[query.dataset]) {
    const value = query[key];
    if (value && !(key === "pipeline" && value === "any")) pairs.push([key, value]);
  }
  if (query.sort !== DEFAULT_SORT) pairs.push(["sort", query.sort]);
  if (query.page > 1) pairs.push(["page", String(query.page)]);
  return pairs;
}

/** The address a partner keeps. Defaults are omitted so the common URL stays short. */
export function viewHref(query, recordId = null) {
  const params = new URLSearchParams(queryPairs(query));
  if (recordId) params.set("record", recordId);
  const search = params.toString();
  return `${DATASET_ROUTE[query.dataset]}${search ? `?${search}` : ""}`;
}

/** The read the server answers. `record` is a UI concern and never travels here. */
export function listRequestUrl(query) {
  const params = new URLSearchParams(queryPairs(query));
  const search = params.toString();
  return `${API_PREFIX}${query.dataset}${search ? `?${search}` : ""}`;
}

export function recordRequestUrl(dataset, id) {
  return `${API_PREFIX}${dataset}/${id}`;
}

export function sameQuery(left, right) {
  if (!left || !right || left.dataset !== right.dataset) return false;
  const keys = ["scope", "q", "sort", "page", ...FILTER_KEYS[left.dataset]];
  return keys.every((key) => left[key] === right[key]);
}

export function hasActiveFilters(query) {
  const base = defaultQuery(query.dataset);
  return ["scope", "q", "sort", ...FILTER_KEYS[query.dataset]].some((key) => query[key] !== base[key]);
}

/** Visible chips: every narrowing currently applied, each removable on its own. */
export function filterChips(query, facets = {}) {
  const chips = [];
  if (query.scope === "mine") chips.push({ key: "scope", label: "Scope", value: SCOPE_LABEL.mine, reset: DEFAULT_SCOPE });
  if (query.q) chips.push({ key: "q", label: "Search", value: query.q, reset: "" });
  const labelFor = (list, slug) => (list || []).find((option) => option.slug === slug)?.label || slug;
  if (query.dataset === "clients") {
    if (query.status) chips.push({ key: "status", label: "Status", value: labelFor(facets.statuses, query.status), reset: "" });
    if (query.type) chips.push({ key: "type", label: "Client type", value: labelFor(facets.types, query.type), reset: "" });
    if (query.pipeline !== "any") chips.push({ key: "pipeline", label: "Pipeline", value: PIPELINE_LABEL[query.pipeline], reset: "any" });
  } else {
    if (query.category) chips.push({ key: "category", label: "Category", value: labelFor(facets.categories, query.category), reset: "" });
    if (query.stage) chips.push({ key: "stage", label: "Stage", value: labelFor(facets.stages, query.stage), reset: "" });
    if (query.disposition) chips.push({ key: "disposition", label: "Disposition", value: labelFor(facets.dispositions, query.disposition), reset: "" });
  }
  if (query.sort !== DEFAULT_SORT) chips.push({ key: "sort", label: "Sorted by", value: SORT_LABEL[query.sort], reset: DEFAULT_SORT });
  return chips;
}

// ------------------------------------------------------ navigation intent
//
// Three small decisions the view used to make inline, and got wrong. They are
// here because they are decisions, not drawing: each one is a rule about what
// the reader should experience, and each is now checkable without a browser.

/**
 * Opening, closing or swapping a record leaves the SAME list underneath, so the
 * reader keeps their exact place. Changing the list itself — filter, sort,
 * page, dataset — is a different list and starts at the top.
 */
export function scrollIntent(currentQuery, href) {
  if (!currentQuery) return "top";
  const [pathname, search = ""] = String(href).split("?");
  const parsed = parseViewState(pathname, search);
  return parsed && sameQuery(parsed.query, currentQuery) ? "keep" : "top";
}

/**
 * What the search box should say, or null to leave it alone. A LOCATION change
 * (first load, a link, Back, forward) is authoritative even while the box has
 * focus — otherwise Back restores the chips and rows but leaves stale text in
 * the input. An ordinary repaint is not authoritative and never overwrites a
 * draft someone is still typing.
 */
export function searchBoxValue({ current, query, editing = false, fromLocation = false }) {
  const wanted = query || "";
  if (String(current ?? "") === wanted) return null;
  if (fromLocation) return wanted;
  return editing ? null : wanted;
}

/**
 * The same panel is a side panel on a desktop and a full-screen cover on a
 * phone. Only the second one is a dialog, and only the second one may take the
 * background out of the keyboard and screen-reader order.
 */
export function panelModality({ recordId, phoneWidth = false }) {
  if (!recordId) return "closed";
  return phoneWidth ? "modal" : "inline";
}

/**
 * Where a Tab inside the phone dialog must land, or null to let the browser
 * move focus itself. Total on purpose: the heading the panel opens on carries
 * `tabindex="-1"`, so it is INSIDE the dialog but is not one of the tab stops,
 * and "inside but not a stop" was the case that fell through to the browser —
 * the very first Shift+Tab after opening then walked out into the background
 * wherever `inert` is not supported, which is the only place this trap matters.
 * Anything inside that is not a stop clamps: back to the last stop, forward to
 * the first, which is also where the heading's own forward Tab already goes.
 */
export function panelTabTarget({ inside, stopIndex, stopCount, shiftKey = false }) {
  if (stopCount === 0) return "title";
  if (!inside) return "first";
  if (stopIndex === -1) return shiftKey ? "last" : "first";
  if (!shiftKey && stopIndex === stopCount - 1) return "first";
  if (shiftKey && stopIndex === 0) return "last";
  return null;
}

// ----------------------------------------------------------- session state
//
// A KNOWN SIGN-OUT IS NOT A FAILED READ. When the server says the session has
// ended, everything this view is holding stops being showable: the list, the
// open record, the remembered answers Back would repaint, and any read still in
// flight. The two sequence numbers are bumped so a reply that left before the
// expiry cannot land after it. Ordinary failures — unreachable, refused filter,
// disagreeing counts, offline — are NOT this, and deliberately keep the
// remembered answers so Back and refresh still work once the trouble passes.

export function createBusinessState() {
  return {
    dataset: null, query: null, recordId: null, facets: {}, signedOut: false,
    list: { key: null, status: "loading", payload: null, code: null, sequence: 0 },
    record: { id: null, status: "idle", payload: null, code: null, sequence: 0 },
    cache: new Map(),
  };
}

/** True only for the answer that means "this session is over". */
export function isSessionExpiry(status, code) {
  return status === 401 || status === "unauthorized" || code === SIGNED_OUT_CODE;
}

/**
 * The whole erasure, in one place: cleared cache, cleared list, cleared record,
 * cleared filter options, and both sequences moved past anything in flight.
 * Returns the replacement state and empties the caller's cache.
 */
export function expireSession(state) {
  if (state.cache instanceof Map) state.cache.clear();
  return {
    ...state,
    signedOut: true,
    facets: {},
    list: { key: null, status: "unauthorized", payload: null, code: SIGNED_OUT_CODE, sequence: state.list.sequence + 1 },
    record: { id: null, status: "unauthorized", payload: null, code: SIGNED_OUT_CODE, sequence: state.record.sequence + 1 },
  };
}

/** A verified answer proves the session is back; nothing else may clear it. */
export function restoreSession(state) {
  return state.signedOut ? { ...state, signedOut: false } : state;
}

/**
 * The only door to a remembered answer. While signed out it opens onto nothing,
 * which is what stops Back and forward from repainting records the reader is no
 * longer entitled to see.
 */
export function cachedPayload(state, key) {
  if (!state || state.signedOut || !(state.cache instanceof Map)) return null;
  return state.cache.get(key) || null;
}

export function rememberPayload(state, key, payload, limit = 8) {
  if (!state || state.signedOut || !(state.cache instanceof Map)) return;
  state.cache.set(key, payload);
  while (state.cache.size > limit) state.cache.delete(state.cache.keys().next().value);
}

// -------------------------------------------------------------- freshness

function iso(value) {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

export function sourceValid(source, dataset) {
  const keys = ["source", "source_ref", "observed_at", "valid_until", "freshness", "correlation_id", "safe_explanation"];
  return Boolean(source) && typeof source === "object" &&
    Object.keys(source).length === keys.length && keys.every((key) => key in source) &&
    source.source === SOURCE_FOR[dataset] && ["fresh", "stale", "missing", "unknown"].includes(source.freshness) &&
    iso(source.observed_at) && iso(source.valid_until) && Date.parse(source.valid_until) > Date.parse(source.observed_at) &&
    typeof source.correlation_id === "string" && source.correlation_id.length > 0;
}

export function sourceIsFresh(source, dataset, now = () => Date.now()) {
  return sourceValid(source, dataset) && source.freshness === "fresh" && Date.parse(source.valid_until) > now();
}

/** What the read is against the CURRENT clock, not what it claimed when stamped. */
export function displayedFreshness(source, dataset, now = () => Date.now()) {
  if (!sourceValid(source, dataset)) return "unknown";
  if (source.freshness !== "fresh") return source.freshness;
  return Date.parse(source.valid_until) > now() ? "fresh" : "expired";
}

// ------------------------------------------------------------- validation

function exactKeys(value, keys) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === keys.slice().sort().join(",");
}

function counted(value) {
  return Number.isInteger(value) && value >= 0;
}

function facetList(value) {
  return Array.isArray(value) && value.every((option) => option && typeof option.slug === "string" &&
    typeof option.label === "string");
}

function partialValid(value) {
  return value === null || (exactKeys(value, ["kind", "count", "fields", "note"]) &&
    value.kind === "unlabelled_recorded_codes" && counted(value.count) && value.count > 0 &&
    Array.isArray(value.fields) && value.fields.every((field) => typeof field === "string") &&
    typeof value.note === "string");
}

function rowValid(row, dataset) {
  return exactKeys(row, LIST_ROW_KEYS[dataset]) && typeof row.id === "string" && UUID.test(row.id) &&
    typeof row.name === "string" && row.name.length > 0;
}

/** The echoed query must be the query that was asked, or the answer is not this view's answer. */
export function echoesQuery(payload, query) {
  const echo = payload?.query;
  if (!echo || typeof echo !== "object" || echo.dataset !== query.dataset) return false;
  const searched = query.q ? query.q : null;
  if ((echo.q ?? null) !== searched) return false;
  if (echo.scope !== query.scope || echo.sort !== query.sort || echo.page !== query.page || echo.page_size !== PAGE_SIZE) return false;
  return FILTER_KEYS[query.dataset].every((key) => {
    const wanted = query[key];
    if (key === "pipeline") return echo.pipeline === wanted;
    return (echo[key] ?? null) === (wanted || null);
  });
}

export function validListPayload(payload, dataset) {
  if (!exactKeys(payload, ["viewer", "dataset", "query", "total", "page", "page_size", "page_count", "out_of_range",
    "rows", "facets", "partial", "recorded_field_note", "source"])) return false;
  if (!VIEWERS.has(payload.viewer) || payload.dataset !== dataset || !sourceValid(payload.source, dataset)) return false;
  if (!counted(payload.total) || !Number.isInteger(payload.page) || payload.page < 1) return false;
  if (payload.page_size !== PAGE_SIZE || !Number.isInteger(payload.page_count) || payload.page_count < 1) return false;
  if (typeof payload.out_of_range !== "boolean" || typeof payload.recorded_field_note !== "string") return false;
  if (!Array.isArray(payload.rows) || payload.rows.length > PAGE_SIZE) return false;
  if (!payload.rows.every((row) => rowValid(row, dataset))) return false;
  // The page and the total have to be able to coexist: a page of rows that
  // reaches past its own total is a disagreement, not a rounding difference.
  // An empty page past the end is the one honest exception.
  if (payload.rows.length > 0 && (payload.page - 1) * PAGE_SIZE + payload.rows.length > payload.total) return false;
  if (payload.page_count !== Math.max(1, Math.ceil(payload.total / PAGE_SIZE))) return false;
  if (payload.out_of_range !== (payload.page > payload.page_count)) return false;
  if (!partialValid(payload.partial)) return false;
  // "N records use codes with no name" is a claim about the rows on screen, so
  // it cannot exceed them. Enforced here rather than trusted from the server:
  // the sentence is rendered by this file and must be true in this file.
  if (payload.partial && payload.partial.count > payload.rows.length) return false;
  const facets = payload.facets;
  if (!facets || typeof facets !== "object") return false;
  return dataset === "clients"
    ? exactKeys(facets, ["statuses", "types"]) && facetList(facets.statuses) && facetList(facets.types)
    : exactKeys(facets, ["categories", "stages", "dispositions"]) && facetList(facets.categories) &&
      facetList(facets.stages) && facetList(facets.dispositions);
}

export function validRecordPayload(payload, dataset, id = null) {
  if (!exactKeys(payload, ["viewer", "dataset", "record", "partial", "not_in_this_read", "recorded_field_note", "source"])) return false;
  if (!VIEWERS.has(payload.viewer) || payload.dataset !== dataset || !sourceValid(payload.source, dataset)) return false;
  if (!Array.isArray(payload.not_in_this_read) || !payload.not_in_this_read.every((item) => typeof item === "string")) return false;
  if (typeof payload.recorded_field_note !== "string" || !partialValid(payload.partial)) return false;
  // One record is one record: the same bound as the list, at its smallest size.
  if (payload.partial && payload.partial.count > 1) return false;
  const record = payload.record;
  if (!exactKeys(record, RECORD_KEYS[dataset]) || typeof record.id !== "string" || !UUID.test(record.id)) return false;
  if (typeof record.name !== "string" || !record.name) return false;
  return id === null || record.id.toLowerCase() === id.toLowerCase();
}

// ------------------------------------------------------------ presentation

/** A missing value stays missing. Nothing here turns an absent value into a fact. */
export function recordedValue(value) {
  if (value === null || value === undefined) return { known: false, text: NOT_RECORDED };
  if (typeof value === "boolean") return { known: true, text: value ? "Yes" : "No" };
  if (Array.isArray(value)) return value.length ? { known: true, text: value.join(", ") } : { known: false, text: NOT_RECORDED };
  const text = String(value).trim();
  return text ? { known: true, text } : { known: false, text: NOT_RECORDED };
}

/**
 * A stored code and the name for it are two different facts. When the code is
 * not in the current list of options, the code itself is shown and marked —
 * never renamed, never blanked.
 */
export function recordedCode(code, label) {
  if (code === null || code === undefined || code === "") return { known: false, resolved: false, text: NOT_RECORDED, code: null };
  if (label === null || label === undefined || label === "") return { known: true, resolved: false, text: String(code), code: String(code) };
  return { known: true, resolved: true, text: String(label), code: String(code) };
}

/** People and organisations, said the way a person would say them. */
export function partyKindText(value) {
  if (value === "org") return { known: true, text: "Organisation" };
  if (value === "person") return { known: true, text: "Person" };
  return recordedValue(value);
}

/**
 * Clients: the warm-versus-active distinction a partner needs to see, taken
 * only from what is on the record. The flag lives on the STATUS list, so the
 * three answers are exactly the three that list can give, and a status missing
 * from it is UNSET rather than quietly "not active". The word for a warm
 * relationship is whatever the status itself is called — this never renames it,
 * and it never stands in for a countersigned agreement or a written assignment.
 */
export function clientPipelineTone(row) {
  const flag = row?.recorded_status_active_pipeline;
  if (flag === true) return { tone: "active", label: "In the active pipeline" };
  if (flag === false) return { tone: "warm", label: "Not in the active pipeline" };
  return { tone: "unknown", label: "Pipeline not set" };
}

/** Vendors: workable is a property of the disposition on the record, not a judgement. */
export function vendorDispositionTone(row) {
  const workable = row?.recorded_disposition_workable;
  if (workable === true) return { tone: "active", label: "Workable" };
  if (workable === false) return { tone: "warm", label: "Not workable" };
  return { tone: "unknown", label: "Workable not set" };
}

export function rowTone(dataset, row) {
  return dataset === "clients" ? clientPipelineTone(row) : vendorDispositionTone(row);
}

/**
 * The owner shown is the name written on the record. It is never what decides
 * whose work this is: that is `owned_by_viewer`, which the server works out
 * from the signed-in account rather than from this text.
 */
export function ownerPresentation(row) {
  const label = recordedValue(row?.owner_label);
  return {
    text: label.text,
    known: label.known,
    ownedByViewer: row?.owned_by_viewer === true,
    note: "Owner is shown as written on the record.",
  };
}

export function pageSummary(payload) {
  const total = payload.total;
  const first = total === 0 ? 0 : (payload.page - 1) * PAGE_SIZE + 1;
  const last = (payload.page - 1) * PAGE_SIZE + payload.rows.length;
  return {
    total, page: payload.page, pageCount: payload.page_count,
    from: payload.rows.length ? first : 0, to: last,
    hasPrevious: payload.page > 1,
    hasNext: payload.page < payload.page_count,
    text: total === 0
      ? "0 records"
      : payload.rows.length === 0
        ? `No records on page ${payload.page} · ${total} match this filter`
        : `${first}–${last} of ${total}`,
  };
}

/**
 * Loading, refreshing, not current, empty-because-nothing-exists,
 * empty-because-the-filter-excludes-everything, past-the-last-page, signed out
 * and unavailable are eight different situations and must render as eight
 * different things.
 */
export function listPhase({ status, payload, query, dataset, signedOut = false, now = () => Date.now() }) {
  if (signedOut || status === "unauthorized") return "unauthorized";
  if (status === "error") return "unavailable";
  // Refreshing means an earlier verified answer is still on screen. Without one
  // there is nothing to refresh and this is simply a first read.
  if (!payload || !validListPayload(payload, dataset)) return "loading";
  if (status === "refreshing") return "refreshing";
  if (!sourceIsFresh(payload.source, dataset, now)) return "stale";
  if (payload.out_of_range) return "out-of-range";
  if (payload.total > 0) return "ready";
  return hasActiveFilters(query) ? "empty-no-matches" : "empty-no-records";
}

export function emptyCopy(dataset, phase) {
  const plural = DATASET_LABEL[dataset].toLowerCase();
  if (phase === "empty-no-matches") {
    return { title: `No ${plural} match these filters`, copy: "Nothing matched. Clear a filter to widen the search." };
  }
  if (phase === "out-of-range") {
    return { title: "Nothing on this page", copy: "This page is past the end of the list. The number below is the real count." };
  }
  return { title: `No ${plural} yet`, copy: `There are no ${plural} to show.` };
}

export const REFUSAL_COPY = {
  AUTHENTICATION_REQUIRED: "Your session has ended. Sign in again to see these records.",
  AUTHORIZATION_REFUSED: "This account is not allowed to see these records.",
  TENANT_SCOPE_REFUSED: "These records are outside this account.",
  QUERY_INVALID: "That is not a filter this page accepts, so nothing was read.",
  RECORD_NOT_FOUND: "This record is not here. Merged and deleted records are left out.",
  VIEWER_OWNER_UNKNOWN: "My work cannot be worked out for this account, so nothing is shown rather than an empty list.",
  FRESHNESS_UNKNOWN: "The count and the rows did not agree, so nothing is shown as current.",
  DEPENDENCY_UNAVAILABLE: "These records cannot be reached right now.",
  // Not a failure of the read and not a statement about the records: this
  // deployment has not been given access to them yet.
  DEPENDENCY_NOT_PROVISIONED: "This workspace has not been given access to these records yet, so nothing was read.",
  // The installed service worker answers an unreachable read with this exact
  // code rather than a cached list, so it is a reachable state here.
  offline: "You are offline, so nothing was read. Nothing here is filled in from an older answer.",
  METHOD_NOT_ALLOWED: "This page only reads records.",
  INTERNAL_ERROR: "The read did not finish. Nothing here is filled in from an older answer.",
};

export function refusalCopy(code) {
  return REFUSAL_COPY[code] || REFUSAL_COPY.INTERNAL_ERROR;
}

// -------------------------------------------------------------- detail view

const CLIENT_SECTIONS = [
  { title: "Status and type", fields: [
    ["recorded_status", "Status", "recorded_status_label"],
    ["recorded_status_note", "What this status means"],
    ["recorded_etl_status", "ETL status"],
    ["recorded_client_type", "Client type", "recorded_client_type_label"],
    ["vertical", "Vertical"],
    ["subtype", "Subtype"],
    ["specialty_type_label", "Specialty type"],
    ["deal_type_label", "Deal type"],
  ] },
  { title: "Who they are", fields: [
    ["ref", "Client reference"],
    ["title", "Title"],
    ["specialty", "Specialty"],
    ["npi", "NPI"],
  ] },
  { title: "Where", fields: [
    ["city", "City"], ["state", "State"], ["county", "County"],
  ] },
  { title: "Contact", fields: [
    ["contact_label", "Main contact"],
    ["phone", "Phone"], ["cell", "Mobile"], ["email", "Email"],
    ["contact_state", "Contact status"],
    ["contact_state_reason", "Why"],
    ["contact_state_until", "Until"],
    ["contact_state_cadence", "How often to reach out"],
  ] },
  { title: "How it started", fields: [
    ["acquisition_source", "How they found us"],
    ["acquisition_detail", "More on how they found us"],
    ["possible_duplicate_label", "Possible duplicate"],
  ] },
  { title: "Notes", fields: [["notes", "Notes"]] },
  { title: "About this record", fields: [
    ["owner_label", "Owner"],
    ["record_version", "Version"],
    ["created_at", "Added"], ["updated_at", "Last updated"],
  ] },
];

const VENDOR_SECTIONS = [
  { title: "Category and relationship", fields: [
    ["recorded_category", "Category", "recorded_category_label"],
    ["recorded_stage", "Stage", "recorded_stage_label"],
    ["recorded_disposition", "Disposition", "recorded_disposition_label"],
    ["recorded_disposition_workable", "Workable"],
    ["relationship_level", "Relationship level", "relationship_level_label"],
    ["relationship_level_note", "What this level means"],
    ["verticals", "Verticals"],
  ] },
  { title: "Who they are", fields: [
    ["ref", "Vendor reference"], ["title", "Title"],
  ] },
  { title: "Where", fields: [
    ["city", "City"], ["state", "State"], ["county", "County"], ["territory", "Territory"],
    ["out_of_market", "Out of market"],
  ] },
  { title: "Contact", fields: [
    ["phone", "Phone"], ["cell", "Mobile"], ["email", "Email"],
    ["contact_state", "Contact status"],
    ["contact_state_reason", "Why"],
    ["contact_state_until", "Until"],
    ["contact_state_cadence", "How often to reach out"],
  ] },
  { title: "Working together", fields: [
    ["referral_active", "Sending referrals"],
    ["is_target", "Target"],
    ["last_touch", "Last touch"],
    ["offers", "What they offer"], ["seeking", "What they are looking for"],
    ["rivalry_group", "Competes with"], ["originated", "Where we met"],
    ["links_label", "Links"],
  ] },
  { title: "Notes", fields: [["intro_notes", "Intro notes"]] },
  { title: "About this record", fields: [
    ["owner_label", "Owner"],
    ["record_version", "Version"],
    ["created_at", "Added"], ["updated_at", "Last updated"],
  ] },
];

/**
 * The detail panel's whole content, derived once. Every field in the read model
 * appears exactly once, and a field with nothing in it still appears — as
 * "Not recorded", because "we do not know" is information a partner needs.
 */
export function recordSections(dataset, record) {
  const groups = dataset === "clients" ? CLIENT_SECTIONS : VENDOR_SECTIONS;
  return groups.map((group) => ({
    title: group.title,
    fields: group.fields.map(([key, label, labelKey]) => {
      const presented = labelKey ? recordedCode(record?.[key], record?.[labelKey]) : recordedValue(record?.[key]);
      return {
        key, label, text: presented.text, known: presented.known,
        resolved: presented.resolved !== false,
        code: presented.code ?? null,
      };
    }),
  }));
}

/** A response is only allowed to paint if no newer read has started since it left. */
export function acceptsResponse(currentSequence, responseSequence) {
  return Number.isInteger(currentSequence) && Number.isInteger(responseSequence) && responseSequence === currentSequence;
}

/** Everything whose rendering can change purely because the clock moved. */
export function freshnessSignature(payload, dataset, now = () => Date.now()) {
  if (!validListPayload(payload, dataset)) return "invalid";
  return [dataset, payload.page, displayedFreshness(payload.source, dataset, now)].join("|");
}
