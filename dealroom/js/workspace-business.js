import { escapeHtml } from "./esc.js";
import { claimInert, releaseInert } from "./inert-registry.js";
// Clients and Vendors: the browser half of the Journey 1 business read.
//
// THE URL IS THE VIEW'S MEMORY. Search text, every filter, the sort, the page
// and the open record all live in the address, so Back restores a place instead
// of reloading a default, and opening a record never costs the reader the list
// they were reading. Scroll rides in the history entry beside it.
//
// A LATE ANSWER NEVER PAINTS. Every read carries a sequence number, and the
// server's echoed query has to be the query that was asked; a response failing
// either test is dropped rather than allowed to repaint someone else's filter
// over the one on screen.
//
// A SIGN-OUT ERASES EVERYTHING THIS VIEW HOLDS. One 401 clears the list, the
// open record, the remembered answers Back would repaint and both in-flight
// generations (see expireSession). Ordinary trouble — unreachable, offline, a
// refused filter — deliberately does none of that.
//
// NOTHING HERE COUNTS OR FILTERS. The total, the page window and the matching
// set are the server's answers; this file renders them or says why it cannot.

import {
  DATASET_LABEL, DATASET_ROUTE, DATASET_SINGULAR, MAX_QUERY_LENGTH, PIPELINE_FILTERS, PIPELINE_LABEL, SORTS,
  SOURCE_LABEL, acceptsResponse, cachedPayload, createBusinessState, datasetForPath, defaultQuery,
  displayedFreshness, echoesQuery, emptyCopy, expireSession, filterChips, freshnessSignature, hasActiveFilters,
  isSessionExpiry, listPhase, listRequestUrl, ownerPresentation, pageSummary, panelModality, panelTabTarget,
  parseViewState, partyKindText, recordRequestUrl, recordSections, recordedCode, recordedValue, refusalCopy,
  rememberPayload, restoreSession, rowTone, sameQuery, scrollIntent, searchBoxValue, sourceIsFresh,
  validListPayload, validRecordPayload, viewHref,
} from "./workspace-business-model.js";

const EXPIRY_TICK_MS = 5_000;
const SEARCH_DEBOUNCE_MS = 350;

const dom = {
  title: document.querySelector("#pageTitle"),
  intro: document.querySelector("#pageIntro"),
  viewer: document.querySelector("#viewerWorkspace"),
  healthOrb: document.querySelector("#healthOrb"),
  healthLabel: document.querySelector("#healthLabel"),
  observedAt: document.querySelector("#observedAt"),
  form: document.querySelector("#filterForm"),
  search: document.querySelector("#searchInput"),
  filterA: document.querySelector("#filterA"),
  filterB: document.querySelector("#filterB"),
  filterC: document.querySelector("#filterC"),
  filterALabel: document.querySelector("#filterALabel"),
  filterBLabel: document.querySelector("#filterBLabel"),
  filterCLabel: document.querySelector("#filterCLabel"),
  sort: document.querySelector("#sortSelect"),
  scopeSwitch: document.querySelector("#scopeSwitch"),
  scopeNote: document.querySelector("#scopeNote"),
  reset: document.querySelector("#resetFilters"),
  chips: document.querySelector("#chipBar"),
  notices: document.querySelector("#noticeRegion"),
  summary: document.querySelector("#resultSummary"),
  listRegion: document.querySelector("#listRegion"),
  list: document.querySelector("#recordList"),
  pager: document.querySelector("#pager"),
  source: document.querySelector("#listSource"),
  panel: document.querySelector("#recordPanel"),
  panelTitle: document.querySelector("#recordTitle"),
  panelEyebrow: document.querySelector("#recordEyebrow"),
  panelBody: document.querySelector("#recordBody"),
  panelClose: document.querySelector("#recordClose"),
  navClients: document.querySelector("#navClients"),
  navVendors: document.querySelector("#navVendors"),
};

// The phone bar is the only navigation a reader has under 767px, and on these two
// pages it marked nothing at all: no current tab in markup and none in script,
// while Home's bar has carried aria-current="page" since it shipped. These links
// have no ids, so they are addressed the way a reader addresses them — by where
// they go.
const phoneNavLinks = [...document.querySelectorAll(".mobile-nav a[href]")];

const scopeButtons = dom.scopeSwitch ? [...dom.scopeSwitch.querySelectorAll("[data-scope]")] : [];

// One place holds what this view believes. Every renderer reads it, and the
// session-state transitions live in the model so they can be tested without a
// browser.
const view = Object.assign(createBusinessState(), { freshnessKey: null, returnFocusId: null });
let searchTimer = null;


function formatMoment(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "an unknown time";
  return date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function formatDay(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date.toLocaleDateString([], { dateStyle: "medium" });
}

const HEALTH_LABEL = {
  loading: "Checking…",
  refreshing: "Refreshing…",
  available: "Up to date",
  stale: "Checked a while ago",
  unauthorized: "Signed out",
  unavailable: "Records unavailable",
};

function setHealth(state) {
  const orb = state === "stale" || state === "unauthorized" ? "unavailable" : state;
  if (dom.healthOrb) dom.healthOrb.className = `status-orb ${orb}`;
  if (dom.healthLabel) dom.healthLabel.textContent = HEALTH_LABEL[state] || HEALTH_LABEL.unavailable;
}

/** Where it came from and when, without naming a table. */
function sourceLabel(source, dataset) {
  if (!source) return "Source unknown";
  const current = displayedFreshness(source, dataset) === "fresh";
  return `${SOURCE_LABEL[dataset]} · checked ${formatMoment(source.observed_at)}${current ? "" : " · not current"}`;
}

// ------------------------------------------------------------- navigation

function currentHref() {
  return `${window.location.pathname}${window.location.search}`;
}

/**
 * Record where the reader is before leaving this history entry, so coming Back
 * to it puts them at the same row rather than at the top of the list.
 */
function rememberScroll() {
  window.history.replaceState({ ...(window.history.state || {}), scrollY: Math.round(window.scrollY) }, "", currentHref());
}

/**
 * Opening, closing or swapping a record leaves the list underneath untouched,
 * so the reader's exact position is CARRIED INTO the new history entry and
 * restored after any focus move. Changing the list itself — a filter, a sort, a
 * page, the other dataset — is a new list and starts at the top.
 */
function navigate(href, { replace = false } = {}) {
  if (href === currentHref()) return;
  const scrollY = Math.round(window.scrollY);
  const nextScroll = scrollIntent(view.query, href) === "keep" ? scrollY : 0;
  if (replace) window.history.replaceState({ ...(window.history.state || {}), scrollY: nextScroll }, "", href);
  else {
    rememberScroll();
    window.history.pushState({ scrollY: nextScroll }, "", href);
  }
  applyLocation({ reason: "navigate", restoreScroll: nextScroll });
}

/** Focus that never scrolls; the scroll position is restored deliberately, once. */
function focusWithoutScrolling(element) {
  element?.focus?.({ preventScroll: true });
}

// ------------------------------------------------------------- the controls

const FILTER_FIELDS = {
  clients: [
    { select: "filterA", label: "filterALabel", key: "status", text: "Status", empty: "Any status", facet: "statuses" },
    { select: "filterB", label: "filterBLabel", key: "type", text: "Client type", empty: "Any client type", facet: "types" },
    { select: "filterC", label: "filterCLabel", key: "pipeline", text: "Pipeline", empty: null, facet: null },
  ],
  vendors: [
    { select: "filterA", label: "filterALabel", key: "category", text: "Category", empty: "Any category", facet: "categories" },
    { select: "filterB", label: "filterBLabel", key: "stage", text: "Stage", empty: "Any stage", facet: "stages" },
    { select: "filterC", label: "filterCLabel", key: "disposition", text: "Disposition", empty: "Any disposition", facet: "dispositions" },
  ],
};

function optionsHtml(options, emptyLabel, selected) {
  const head = emptyLabel === null ? "" : `<option value="">${escapeHtml(emptyLabel)}</option>`;
  return head + options.map((option) =>
    `<option value="${escapeHtml(option.slug)}"${option.slug === selected ? " selected" : ""}>${escapeHtml(option.label)}</option>`).join("");
}

/**
 * `syncSearch` is the difference between the two reasons this runs. A LOCATION
 * change (first load, a link, Back, forward) is authoritative about what the
 * search box should say, even while it holds focus, so Back cannot leave stale
 * text next to restored chips — and the pending keystroke timer is dropped so
 * the text it was about to submit cannot fire afterwards. An ordinary repaint
 * (a finished read, a refresh) is not authoritative and never touches a draft
 * the reader is still typing.
 */
function renderControls({ syncSearch = false } = {}) {
  const { dataset, query, facets } = view;
  if (dom.search) {
    const next = searchBoxValue({
      current: dom.search.value, query: query.q,
      editing: document.activeElement === dom.search, fromLocation: syncSearch,
    });
    if (next !== null) {
      // A pending keystroke would otherwise fire after the reconciliation and
      // navigate back to the text the reader just left behind.
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = null;
      dom.search.value = next;
    }
  }
  if (dom.sort) dom.sort.value = SORTS.includes(query.sort) ? query.sort : "name";
  for (const field of FILTER_FIELDS[dataset]) {
    const select = dom[field.select];
    const label = dom[field.label];
    if (!select || !label) continue;
    label.textContent = field.text;
    if (field.key === "pipeline") {
      select.innerHTML = PIPELINE_FILTERS.map((value) =>
        `<option value="${value}"${value === query.pipeline ? " selected" : ""}>${escapeHtml(PIPELINE_LABEL[value])}</option>`).join("");
      continue;
    }
    select.innerHTML = optionsHtml(facets[field.facet] || [], field.empty, query[field.key]);
    // A stored code that is not in the current list of options cannot be shown
    // as one, so the current choice stays visible instead of vanishing.
    if (query[field.key] && !select.value) {
      select.insertAdjacentHTML("beforeend", `<option value="${escapeHtml(query[field.key])}" selected>${escapeHtml(query[field.key])} (code)</option>`);
      select.value = query[field.key];
    }
  }
  scopeButtons.forEach((button) => {
    const selected = button.dataset.scope === query.scope;
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    button.classList.toggle("on", selected);
  });
  if (dom.scopeNote) {
    dom.scopeNote.textContent = query.scope === "mine"
      ? "Showing the ones recorded as yours. Team is the usual view."
      : "Showing everyone's. Switch to My work for the ones recorded as yours.";
  }
  markCurrentPage(dataset);
}

/**
 * The current page is ANNOUNCED and SHOWN, on both navigations.
 *
 * aria-current alone was announced and invisible: `.primary-nav a.active` is the
 * only rule in workspace.css that paints a current tab, and nothing ever added the
 * class, so /clients and /vendors highlighted no tab for anyone reading with their
 * eyes. Both marks are now set together, from the same one dataset, so they cannot
 * disagree about which page this is.
 *
 * The phone bar takes aria-current only, exactly matching Home's bar. There is no
 * `.mobile-nav a.active` rule in the stylesheet, and adding a class that paints
 * nothing would be a second silent mark of the kind this function exists to end.
 */
function markCurrentPage(dataset) {
  const current = DATASET_ROUTE[dataset] || null;
  for (const [link, route] of [[dom.navClients, DATASET_ROUTE.clients], [dom.navVendors, DATASET_ROUTE.vendors]]) {
    if (!link) continue;
    const here = route === current;
    link.setAttribute("aria-current", here ? "page" : "false");
    link.classList.toggle("active", here);
  }
  for (const link of phoneNavLinks) {
    if (link.getAttribute("href") === current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

function renderChips() {
  if (!dom.chips) return;
  const chips = filterChips(view.query, view.facets);
  if (!chips.length) {
    dom.chips.innerHTML = '<p class="chip-empty">No filters. Showing everything.</p>';
    return;
  }
  dom.chips.innerHTML = `${chips.map((chip) =>
    `<button type="button" class="chip" data-chip="${escapeHtml(chip.key)}" data-chip-reset="${escapeHtml(chip.reset)}"><span class="chip-label">${escapeHtml(chip.label)}</span><span class="chip-value">${escapeHtml(chip.value)}</span><span class="chip-remove" aria-hidden="true">×</span><span class="visually-hidden">Remove this filter</span></button>`).join("")}<button type="button" class="chip chip-reset" data-chip="all">Clear all</button>`;
}

function renderNotices(notices) {
  if (!dom.notices) return;
  dom.notices.innerHTML = notices.map((notice) =>
    `<div class="notice notice-${escapeHtml(notice.kind)}"><p class="notice-title">${escapeHtml(notice.title)}</p><p class="notice-copy">${escapeHtml(notice.copy)}</p>${notice.retry ? '<button type="button" class="action secondary-action" data-retry="list">Check again</button>' : ""}</div>`).join("");
}

// --------------------------------------------------------------- the list

function rowFacts(dataset, row) {
  const facts = [];
  if (dataset === "clients") {
    const status = recordedCode(row.recorded_status, row.recorded_status_label);
    facts.push({ label: "Status", value: status.text, known: status.known, unresolved: status.known && !status.resolved });
    const etl = recordedValue(row.recorded_etl_status);
    facts.push({ label: "ETL", value: etl.text, known: etl.known });
    const type = recordedCode(row.recorded_client_type, row.recorded_client_type_label);
    facts.push({ label: "Client type", value: type.text, known: type.known, unresolved: type.known && !type.resolved });
  } else {
    const category = recordedCode(row.recorded_category, row.recorded_category_label);
    facts.push({ label: "Category", value: category.text, known: category.known, unresolved: category.known && !category.resolved });
    const stage = recordedCode(row.recorded_stage, row.recorded_stage_label);
    facts.push({ label: "Stage", value: stage.text, known: stage.known, unresolved: stage.known && !stage.resolved });
    const level = recordedCode(row.relationship_level, row.relationship_level_label);
    // A level with no entry in the list is a bare number; say so rather than
    // letting it read as a name everyone is supposed to recognise.
    facts.push({ label: "Relationship", value: level.text, known: level.known, unresolved: level.known && !level.resolved });
    const touch = row.last_touch ? formatDay(row.last_touch) : null;
    facts.push({ label: "Last touch", value: touch || "Not recorded", known: Boolean(touch) });
  }
  const place = [row.city, row.state].filter(Boolean).join(", ");
  facts.push({ label: "Location", value: place || "Not recorded", known: Boolean(place) });
  return facts;
}

function rowHtml(dataset, row, selectedId) {
  const tone = rowTone(dataset, row);
  const owner = ownerPresentation(row);
  const ref = recordedValue(row.ref);
  const selected = row.id === selectedId;
  const facts = rowFacts(dataset, row).map((fact) =>
    `<span class="row-fact${fact.known ? "" : " unknown"}${fact.unresolved ? " unresolved" : ""}"><span class="fact-label">${escapeHtml(fact.label)}</span><span class="fact-value">${escapeHtml(fact.value)}</span></span>`).join("");
  return `<li class="record-item"><a class="record-row${selected ? " selected" : ""}" id="row-${escapeHtml(row.id)}" data-record-id="${escapeHtml(row.id)}" href="${escapeHtml(viewHref(view.query, row.id))}"${selected ? ' aria-current="true"' : ""}><span class="row-head"><span class="row-name">${escapeHtml(row.name)}</span><span class="row-ref${ref.known ? "" : " unknown"}">${escapeHtml(ref.text)}</span><span class="tone tone-${escapeHtml(tone.tone)}">${escapeHtml(tone.label)}</span></span><span class="row-facts">${facts}</span><span class="row-foot"><span class="row-owner${owner.known ? "" : " unknown"}">Owner: ${escapeHtml(owner.text)}${owner.ownedByViewer ? '<span class="row-you">Yours</span>' : ""}</span><span class="row-updated">Updated ${escapeHtml(formatMoment(row.updated_at))}</span></span></a></li>`;
}

function paintList(html, { busy = false } = {}) {
  const activeId = dom.list?.contains(document.activeElement) ? document.activeElement?.id : null;
  if (dom.list) dom.list.innerHTML = html;
  if (dom.listRegion) dom.listRegion.setAttribute("aria-busy", busy ? "true" : "false");
  if (!activeId) return;
  // A repaint must not drop the reader's keyboard place. The same row is
  // preferred; when that row is genuinely gone, the results summary takes focus
  // so the next Tab continues from the list rather than from the page top.
  focusWithoutScrolling(dom.list?.querySelector(`#${CSS.escape(activeId)}`) || dom.summary);
}

function renderEmpty(phase) {
  const copy = emptyCopy(view.dataset, phase);
  paintList(`<li class="record-empty"><p class="empty-title">${escapeHtml(copy.title)}</p><p class="empty-copy">${escapeHtml(copy.copy)}</p>${hasActiveFilters(view.query) ? '<button type="button" class="action secondary-action" data-chip="all">Clear all filters</button>' : ""}</li>`);
}

function renderPager(payload) {
  if (!dom.pager) return;
  const summary = pageSummary(payload);
  dom.pager.hidden = summary.pageCount <= 1 && !payload.out_of_range;
  const link = (id, label, page, enabled, rel) => enabled
    ? `<a class="action secondary-action" id="${id}" rel="${rel}" href="${escapeHtml(viewHref({ ...view.query, page }))}">${label}</a>`
    : `<span class="action secondary-action disabled" id="${id}" aria-disabled="true">${label}</span>`;
  dom.pager.innerHTML = `${link("pagerPrevious", "Previous", Math.max(1, view.query.page - 1), summary.hasPrevious, "prev")}<span class="pager-position">Page ${escapeHtml(summary.page)} of ${escapeHtml(summary.pageCount)}</span>${link("pagerNext", "Next", view.query.page + 1, summary.hasNext, "next")}`;
}

function renderList() {
  const { dataset, list, query } = view;
  const phase = listPhase({ status: list.status, payload: list.payload, query, dataset, signedOut: view.signedOut });
  view.freshnessKey = list.payload ? freshnessSignature(list.payload, dataset) : null;

  if (phase === "loading") {
    setHealth("loading");
    if (dom.summary) dom.summary.textContent = "Checking…";
    if (dom.observedAt) dom.observedAt.textContent = "Checking…";
    if (dom.source) dom.source.textContent = "Checking…";
    if (dom.pager) dom.pager.hidden = true;
    renderNotices([]);
    paintList('<li class="record-skeleton"><span class="skeleton-line"></span><span class="skeleton-line short"></span></li>'.repeat(3), { busy: true });
    return;
  }

  if (phase === "unauthorized" || phase === "unavailable") {
    const signedOut = phase === "unauthorized";
    setHealth(signedOut ? "unauthorized" : "unavailable");
    if (dom.summary) dom.summary.textContent = signedOut ? "Signed out" : "Nothing loaded";
    if (dom.observedAt) dom.observedAt.textContent = signedOut ? "Session ended" : "Nothing loaded";
    if (dom.source) dom.source.textContent = signedOut ? "Signed out" : "Nothing loaded";
    if (dom.pager) dom.pager.hidden = true;
    renderNotices([]);
    paintList(`<li class="record-empty"><p class="empty-title">${signedOut ? "Your session has ended" : "This did not load"}</p><p class="empty-copy">${escapeHtml(refusalCopy(list.code || (signedOut ? "AUTHENTICATION_REQUIRED" : "INTERNAL_ERROR")))}</p>${signedOut
      ? `<a class="action primary-action" href="/auth/login?return_to=${encodeURIComponent(currentHref())}">Sign in</a>`
      : '<button type="button" class="action secondary-action" data-retry="list">Check again</button>'}</li>`);
    return;
  }

  const payload = list.payload;
  const refreshing = phase === "refreshing";
  const stale = phase === "stale";
  setHealth(refreshing ? "refreshing" : stale ? "stale" : "available");
  if (dom.viewer) {
    dom.viewer.textContent = payload.viewer === "joe" ? "Joe’s workspace" : payload.viewer === "dell" ? "Dell’s workspace" : "Partner workspace";
  }
  if (dom.observedAt) dom.observedAt.textContent = `${stale ? "Not current · " : ""}Checked ${formatMoment(payload.source.observed_at)}`;
  if (dom.source) dom.source.textContent = sourceLabel(payload.source, dataset);
  const summary = pageSummary(payload);
  // The server's total, said plainly. An empty page and an empty result are two
  // different sentences, and neither of them is a bare "0".
  const noun = DATASET_LABEL[dataset].toLowerCase();
  const counted = payload.total === 0
    ? `No ${noun} match this filter`
    : payload.rows.length === 0
      ? `No ${noun} on page ${summary.page} · ${payload.total} match this filter`
      : `${summary.from}–${summary.to} of ${payload.total} ${noun}`;
  if (dom.summary) dom.summary.textContent = `${counted}${refreshing ? " · refreshing" : ""}`;

  const notices = [];
  if (stale) {
    notices.push({ kind: "stale", title: "Checked a while ago",
      copy: "This list is no longer current. Check again before acting on it.", retry: true });
  }
  if (refreshing) {
    notices.push({ kind: "refreshing", title: "Refreshing", copy: "Showing the previous list while a fresh one loads." });
  }
  if (payload.partial) {
    // The count is records, not cells: one record with two unnamed codes is one
    // record here, and the read model counts it the same way.
    notices.push({ kind: "partial", title: `${payload.partial.count} ${payload.partial.count === 1 ? "record uses a code" : "records use codes"} with no name`, copy: payload.partial.note });
  }
  if (payload.out_of_range) {
    notices.push({ kind: "empty", title: "Past the last page",
      copy: `This page is past the end of the list. ${payload.total} ${payload.total === 1 ? "record matches" : "records match"} this filter.` });
  }
  renderNotices(notices);

  if (payload.rows.length === 0) {
    renderEmpty(payload.out_of_range ? "out-of-range" : hasActiveFilters(query) ? "empty-no-matches" : "empty-no-records");
  } else {
    paintList(payload.rows.map((row) => rowHtml(dataset, row, view.recordId)).join(""));
  }
  renderPager(payload);
}

// ------------------------------------------------------------- the record
//
// THE SAME PANEL IS TWO DIFFERENT THINGS AT TWO WIDTHS. On a desktop it sits
// beside the list and both are usable, so it stays a non-modal complementary
// region. On a phone the stylesheet takes it full screen, and a region that
// COVERS the list while leaving the list tabbable is a trap for anyone not
// using a mouse — so at that width it becomes a real dialog: aria-modal, the
// background made inert, Tab kept inside it, and focus handed back to the row
// that opened it on the way out.

const PHONE_PANEL = typeof window.matchMedia === "function" ? window.matchMedia("(max-width: 767px)") : null;
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function panelIsModal() {
  return panelModality({ recordId: view.recordId, phoneWidth: Boolean(PHONE_PANEL?.matches) }) === "modal";
}

function backgroundRegions() {
  return [...document.querySelectorAll("[data-panel-background]")];
}

// Round 3 (V5-J101 review of PR #1259 at afc85607): this used to force each
// region's inert flag to match `modal` unconditionally on every call, with no
// memory of whether something ELSE — Doc, on the same page — also currently
// needs the same region inert. Two independent panels writing the same boolean with no
// coordination meant whichever one wrote LAST won, discarding the other's
// still-active claim in either direction. claimInert/releaseInert
// (inert-registry.js) hold a real reference count shared with doc-panel.js:
// this panel's own claim can never undo Doc's, and Doc's can never undo this
// panel's, however the two happen to interleave.
const PANEL_OWNER = "record-panel";

function applyPanelModality() {
  if (!dom.panel) return;
  const modal = panelIsModal();
  dom.panel.setAttribute("role", modal ? "dialog" : "complementary");
  if (modal) dom.panel.setAttribute("aria-modal", "true");
  else dom.panel.removeAttribute("aria-modal");
  for (const region of backgroundRegions()) {
    if (modal) claimInert(region, PANEL_OWNER);
    else releaseInert(region, PANEL_OWNER);
  }
}

/**
 * Tab and Shift+Tab wrap inside the dialog instead of walking under it. The
 * heading the panel opens on is inside the dialog but is not a tab stop, so
 * panelTabTarget decides for every position — including that one — rather than
 * only for the two ends.
 */
function containPanelFocus(event) {
  if (event.key !== "Tab" || !panelIsModal() || !dom.panel) return;
  const stops = [...dom.panel.querySelectorAll(FOCUSABLE)];
  const active = document.activeElement;
  const target = panelTabTarget({
    inside: dom.panel.contains(active),
    stopIndex: stops.indexOf(active),
    stopCount: stops.length,
    shiftKey: event.shiftKey,
  });
  if (!target) return;
  event.preventDefault();
  if (target === "title") return focusWithoutScrolling(dom.panelTitle);
  focusWithoutScrolling(target === "first" ? stops[0] : stops[stops.length - 1]);
}

function renderRecordPanel() {
  if (!dom.panel) return;
  const { record, dataset } = view;
  if (!view.recordId) {
    dom.panel.hidden = true;
    dom.panel.classList.remove("open");
    applyPanelModality();
    return;
  }
  dom.panel.hidden = false;
  dom.panel.classList.add("open");
  applyPanelModality();
  if (dom.panelEyebrow) dom.panelEyebrow.textContent = DATASET_SINGULAR[dataset];
  // A known sign-out empties the panel before anything else can render from it.
  if (view.signedOut) {
    if (dom.panelTitle) dom.panelTitle.textContent = "Your session has ended";
    if (dom.panelBody) {
      dom.panelBody.innerHTML = `<p class="attention-copy">${escapeHtml(refusalCopy("AUTHENTICATION_REQUIRED"))}</p><a class="action primary-action" href="/auth/login?return_to=${encodeURIComponent(currentHref())}">Sign in</a>`;
    }
    return;
  }
  if (record.status === "loading") {
    if (dom.panelTitle) dom.panelTitle.textContent = "Opening…";
    if (dom.panelBody) dom.panelBody.innerHTML = '<p class="attention-copy">Opening this record.</p><div class="skeleton-line"></div><div class="skeleton-line short"></div>';
    return;
  }
  if (record.status !== "ready" || !record.payload) {
    if (dom.panelTitle) dom.panelTitle.textContent = record.code === "RECORD_NOT_FOUND" ? "Not here" : "This did not load";
    if (dom.panelBody) dom.panelBody.innerHTML = `<p class="attention-copy">${escapeHtml(refusalCopy(record.code))}</p><button type="button" class="action secondary-action" data-retry="record">Check again</button>`;
    return;
  }
  const payload = record.payload;
  const current = sourceIsFresh(payload.source, dataset);
  const tone = rowTone(dataset, payload.record);
  const owner = ownerPresentation(payload.record);
  const kind = partyKindText(payload.record.party_kind);
  if (dom.panelTitle) dom.panelTitle.textContent = payload.record.name;
  const sections = recordSections(dataset, payload.record).map((section) =>
    `<section class="record-section"><h3>${escapeHtml(section.title)}</h3><dl>${section.fields.map((field) =>
      `<div class="record-field${field.known ? "" : " unknown"}${field.resolved ? "" : " unresolved"}"><dt>${escapeHtml(field.label)}</dt><dd>${escapeHtml(field.text)}${field.known && !field.resolved ? '<span class="unresolved-flag">code with no name</span>' : ""}</dd></div>`).join("")}</dl></section>`).join("");
  if (dom.panelBody) {
    dom.panelBody.innerHTML = `${current ? "" : '<div class="notice notice-stale"><p class="notice-title">Checked a while ago</p><p class="notice-copy">Check again before acting on it.</p><button type="button" class="action secondary-action" data-retry="record">Check again</button></div>'}<p class="record-tone"><span class="tone tone-${escapeHtml(tone.tone)}">${escapeHtml(tone.label)}</span><span class="tone tone-plain${kind.known ? "" : " unknown"}">${escapeHtml(kind.text)}</span></p><p class="record-owner${owner.known ? "" : " unknown"}">Owner: ${escapeHtml(owner.text)}${owner.ownedByViewer ? '<span class="row-you">Yours</span>' : ""}</p><p class="record-note">${escapeHtml(payload.recorded_field_note)}</p>${payload.partial ? `<div class="notice notice-partial"><p class="notice-title">Code with no name</p><p class="notice-copy">${escapeHtml(payload.partial.note)}</p></div>` : ""}${sections}<p class="record-note">Not shown here: ${escapeHtml(payload.not_in_this_read.join(", "))}.</p><p class="source">${escapeHtml(sourceLabel(payload.source, dataset))}</p>`;
  }
}

// -------------------------------------------------------------- the reads

/**
 * The one place a known sign-out is handled. It throws away every answer this
 * view is holding — list, record, remembered answers and both in-flight
 * generations — and repaints as signed out.
 */
function expireNow() {
  Object.assign(view, expireSession(view));
  renderControls();
  renderChips();
  renderList();
  renderRecordPanel();
}

async function loadList(reason = "initial") {
  const query = view.query;
  const key = listRequestUrl(query);
  const sequence = ++view.list.sequence;
  // Remembered answers are only for restoring a place, and only while the
  // session is known good; cachedPayload refuses to open once signed out.
  const cached = reason === "initial" || reason === "history" ? cachedPayload(view, key) : null;
  view.list.key = key;
  view.list.code = view.signedOut ? "AUTHENTICATION_REQUIRED" : null;
  if (cached && validListPayload(cached, view.dataset)) {
    view.list.payload = cached;
    view.list.status = "refreshing";
  } else {
    view.list.payload = null;
    view.list.status = view.signedOut ? "unauthorized" : "loading";
  }
  renderList();
  try {
    const response = await fetch(key, { headers: { accept: "application/json" }, cache: "no-store" });
    // THE SIGN-OUT IS HEARD EVEN WHEN THE ANSWER IS STALE. A superseded read is
    // not allowed to paint its DATA, but it still learned something true about
    // this session, and dropping that would leave records on screen that the
    // session can no longer fetch. The expiry check therefore runs BEFORE the
    // sequence guard; everything below it stays behind the guard.
    if (response.status === 401) return expireNow();
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      if (isSessionExpiry(response.status, failure.error)) return expireNow();
      if (!acceptsResponse(view.list.sequence, sequence)) return;
      return settleList({ status: "error", code: failure.error || "INTERNAL_ERROR" }, sequence);
    }
    const payload = await response.json().catch(() => null);
    if (!acceptsResponse(view.list.sequence, sequence)) return;
    // Shape AND echo are checked: an answer to a different filter is not an
    // answer to this one, however fresh it is.
    if (!validListPayload(payload, view.dataset) || !echoesQuery(payload, query)) {
      return settleList({ status: "error", code: "FRESHNESS_UNKNOWN" }, sequence);
    }
    settleList({ status: "ready", payload }, sequence);
  } catch {
    if (!acceptsResponse(view.list.sequence, sequence)) return;
    settleList({ status: "error", code: "DEPENDENCY_UNAVAILABLE" }, sequence);
  }
}

function settleList({ status, payload = null, code = null }, sequence) {
  if (!acceptsResponse(view.list.sequence, sequence)) return;
  // A verified answer is the only thing that proves the session is back.
  if (status === "ready") Object.assign(view, restoreSession(view));
  view.list.status = status;
  view.list.code = code;
  // A refused or failed read never keeps an older answer alive as if current.
  view.list.payload = status === "ready" ? payload : null;
  if (status === "ready") {
    if (payload?.facets) view.facets = payload.facets;
    rememberPayload(view, view.list.key, payload);
  }
  renderControls();
  renderChips();
  renderList();
}

async function loadRecord(id, { focusOnOpen = false } = {}) {
  const sequence = ++view.record.sequence;
  view.record.id = id;
  view.record.status = "loading";
  view.record.payload = null;
  view.record.code = null;
  renderRecordPanel();
  if (focusOnOpen) focusWithoutScrolling(dom.panelTitle);
  const settle = (status, code, payload = null) => {
    if (!acceptsResponse(view.record.sequence, sequence) || view.recordId !== id) return;
    view.record.status = status;
    view.record.code = code;
    view.record.payload = payload;
    renderRecordPanel();
  };
  try {
    const response = await fetch(recordRequestUrl(view.dataset, id), { headers: { accept: "application/json" }, cache: "no-store" });
    // A record read is as authoritative about the session as a list read, and
    // it stays authoritative after the panel closes or another record is
    // opened. The expiry check runs before the selection and sequence guards
    // for exactly that reason; the DATA below it still cannot paint.
    if (response.status === 401) return expireNow();
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      if (isSessionExpiry(response.status, failure.error)) return expireNow();
      return settle("error", failure.error || "INTERNAL_ERROR");
    }
    if (!acceptsResponse(view.record.sequence, sequence) || view.recordId !== id) return;
    const payload = await response.json().catch(() => null);
    if (!validRecordPayload(payload, view.dataset, id)) return settle("error", "FRESHNESS_UNKNOWN");
    settle("ready", null, payload);
  } catch {
    settle("error", "DEPENDENCY_UNAVAILABLE");
  }
}

// ------------------------------------------------------------ orchestration

function applyLocation({ reason = "initial", restoreScroll = null } = {}) {
  const parsed = parseViewState(window.location.pathname, window.location.search);
  if (!parsed) return;
  // THE ADDRESS AND THE VIEW SAY THE SAME THING. Anything unrecognised was
  // already dropped by the parser; rewriting the current entry to the canonical
  // form is what stops a bookmarked `?scope=everyone&owner=dell&page=0` from
  // living on in the address bar beside an empty chip bar. It rewrites within
  // the route it was already on and never invents a destination.
  const wantedHref = viewHref(parsed.query, parsed.recordId);
  if (wantedHref !== currentHref()) {
    window.history.replaceState({ ...(window.history.state || {}) }, "", wantedHref);
  }
  const datasetChanged = parsed.dataset !== view.dataset;
  const queryChanged = datasetChanged || !sameQuery(parsed.query, view.query);
  const recordChanged = parsed.recordId !== view.recordId;
  const hadRecord = Boolean(view.recordId);
  view.dataset = parsed.dataset;
  view.query = parsed.query;
  view.recordId = parsed.recordId;
  document.title = `${DATASET_LABEL[parsed.dataset]} · DoctorCRE`;
  if (dom.title) dom.title.textContent = DATASET_LABEL[parsed.dataset];
  if (dom.intro) {
    dom.intro.textContent = parsed.dataset === "clients"
      ? "Everyone the team works with. Search, filter, and open one to see what is on the record."
      : "Every vendor the team knows. Search, filter, and open one to see what is on the record.";
  }
  if (datasetChanged) {
    view.cache.clear();
    view.facets = {};
  }
  // A location change is authoritative about the search box; an ordinary
  // repaint is not (see renderControls).
  renderControls({ syncSearch: true });
  renderChips();

  // Opening or closing a record never re-reads the list, so the reader's place,
  // scroll and rows survive the panel.
  if (queryChanged) loadList(reason === "navigate" ? "navigate" : "history");
  else renderList();

  if (recordChanged) {
    if (parsed.recordId) {
      loadRecord(parsed.recordId, { focusOnOpen: !hadRecord });
    } else {
      view.record = { id: null, status: "idle", payload: null, code: null, sequence: view.record.sequence + 1 };
      renderRecordPanel();
      if (hadRecord) {
        // Focus returns to the row that opened the panel — the modal case needs
        // this most — and never drags the list somewhere else while doing it.
        const row = view.returnFocusId ? document.querySelector(`#${CSS.escape(view.returnFocusId)}`) : null;
        focusWithoutScrolling(row || dom.summary);
        view.returnFocusId = null;
      }
    }
  } else if (parsed.recordId) {
    renderRecordPanel();
  }
  // Last, so it corrects any scroll a focus move would otherwise have caused.
  if (typeof restoreScroll === "number") window.scrollTo({ top: restoreScroll, behavior: "auto" });
}

function updateQuery(changes, { replace = false } = {}) {
  const next = { ...view.query, ...changes };
  // Any narrowing returns to the first page; a page number carried over from
  // the previous filter would silently point at a different set of records.
  if (!("page" in changes)) next.page = 1;
  navigate(viewHref(next, view.recordId), { replace });
}

function closeRecord() {
  if (!view.recordId) return;
  // Close is deterministic: it rewrites the current entry as the list it came
  // from, so it always lands on the list and never reopens whichever record
  // happened to be behind this one. Back is the other way out, and it closes
  // the panel by returning to the entry that existed before it opened.
  navigate(viewHref(view.query), { replace: true });
}

// ------------------------------------------------------------------ events

function wireControls() {
  dom.form?.addEventListener("submit", (event) => {
    event.preventDefault();
    if (searchTimer) clearTimeout(searchTimer);
    updateQuery({ q: (dom.search?.value || "").trim().slice(0, MAX_QUERY_LENGTH) });
  });
  dom.search?.addEventListener("input", () => {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const value = (dom.search?.value || "").trim().slice(0, MAX_QUERY_LENGTH);
      if (value === view.query.q) return;
      // The first character of a search earns its own history entry, so Back
      // clears the search; the keystrokes after it replace that entry.
      updateQuery({ q: value }, { replace: Boolean(view.query.q) && Boolean(value) });
    }, SEARCH_DEBOUNCE_MS);
  });
  dom.sort?.addEventListener("change", () => updateQuery({ sort: SORTS.includes(dom.sort.value) ? dom.sort.value : "name" }));
  for (const key of ["filterA", "filterB", "filterC"]) {
    dom[key]?.addEventListener("change", () => {
      const field = FILTER_FIELDS[view.dataset].find((candidate) => candidate.select === key);
      if (field) updateQuery({ [field.key]: dom[key].value });
    });
  }
  dom.reset?.addEventListener("click", () => navigate(viewHref(defaultQuery(view.dataset), view.recordId)));

  scopeButtons.forEach((button) => {
    button.addEventListener("click", () => updateQuery({ scope: button.dataset.scope }));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const index = scopeButtons.indexOf(button);
      const next = event.key === "Home" ? 0 : event.key === "End" ? scopeButtons.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : scopeButtons.length - 1)) % scopeButtons.length;
      const target = scopeButtons[next];
      focusWithoutScrolling(target);
      if (target && target.dataset.scope !== view.query.scope) updateQuery({ scope: target.dataset.scope });
    });
  });

  dom.chips?.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-chip]");
    if (!chip) return;
    if (chip.dataset.chip === "all") return navigate(viewHref(defaultQuery(view.dataset), view.recordId));
    updateQuery({ [chip.dataset.chip]: chip.dataset.chipReset ?? "" });
  });

  dom.list?.addEventListener("click", (event) => {
    if (event.target.closest("[data-chip='all']")) {
      event.preventDefault();
      return navigate(viewHref(defaultQuery(view.dataset), view.recordId));
    }
    if (event.target.closest("[data-retry='list']")) {
      event.preventDefault();
      return loadList("retry");
    }
    const row = event.target.closest("a[data-record-id]");
    // Modifier clicks and middle clicks stay ordinary link activations, so a
    // record can still be opened in a new tab from its own real address.
    if (!row || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    view.returnFocusId = row.id;
    navigate(row.getAttribute("href"));
  });

  dom.notices?.addEventListener("click", (event) => {
    if (event.target.closest("[data-retry='list']")) loadList("retry");
  });

  dom.pager?.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    navigate(link.getAttribute("href"));
  });

  dom.panelClose?.addEventListener("click", () => closeRecord());
  dom.panelBody?.addEventListener("click", (event) => {
    if (event.target.closest("[data-retry='record']") && view.recordId) loadRecord(view.recordId);
  });

  document.addEventListener("keydown", (event) => {
    // On a phone the panel is a dialog, so Tab stays inside it.
    containPanelFocus(event);
    if (event.key !== "Escape" || !view.recordId) return;
    // Escape leaves the panel, never the page, and never the reader's place.
    event.preventDefault();
    closeRecord();
  });

  // Rotating the phone or resizing the window changes which of the two panels
  // this is, so the modality is recomputed rather than fixed at open time.
  PHONE_PANEL?.addEventListener?.("change", () => applyPanelModality());

  window.addEventListener("popstate", (event) => {
    applyLocation({ reason: "history", restoreScroll: Number.isFinite(event.state?.scrollY) ? event.state.scrollY : null });
  });
}

/**
 * A local clock that has passed the freshness window must stop presenting rows
 * as current. The tick repaints only on a real change of that state; it never
 * refetches on its own, so nothing reshuffles under a reader mid-sentence.
 */
function watchExpiry() {
  setInterval(() => {
    if (!view.list.payload || view.list.status === "loading") return;
    const signature = freshnessSignature(view.list.payload, view.dataset);
    if (signature === view.freshnessKey) return;
    view.freshnessKey = signature;
    renderList();
  }, EXPIRY_TICK_MS);
}

function start() {
  if (!dom.list) return;
  if (!datasetForPath(window.location.pathname)) {
    // The asset is only served at the two view routes; anything else is a stale
    // bookmark and is sent to the canonical one.
    window.location.replace(DATASET_ROUTE.clients);
    return;
  }
  if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
  wireControls();
  watchExpiry();
  window.history.replaceState({ ...(window.history.state || {}), scrollY: 0 }, "", currentHref());
  applyLocation({ reason: "initial" });
}

start();
