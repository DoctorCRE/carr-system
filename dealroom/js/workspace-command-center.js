import { escapeHtml } from "./esc.js";
const ENDPOINT = "/api/v1/command-center";
const EXPIRY_TICK_MS = 5_000;
import {
  DEAL_ROOM_DESTINATION, DEFAULT_SCOPE, SCOPES, SCOPE_LABEL, acceptsResponse, aggregateCardState, displayedFreshness, freshnessSignature, homeCardCopy,
  homeReadPhase, humanSourceLabel, needsJoeWork, primaryHomeAction, safeDestination, scopeNote, summarizeWorkspaceScope, validWorkspacePayload, viewerWorkspaceLabel,
} from "./workspace-command-center-model.js";

const card = document.querySelector("#dealAttention");
const observedAt = document.querySelector("#observedAt");
const healthOrb = document.querySelector("#healthOrb");
const healthLabel = document.querySelector("#healthLabel");
const viewerWorkspace = document.querySelector("#viewerWorkspace");
const scopeSwitch = document.querySelector("#scopeSwitch");
const scopeButtons = scopeSwitch ? [...scopeSwitch.querySelectorAll("[data-scope]")] : [];
const scopeNoteTarget = document.querySelector("#scopeNote");
const aggregateCards = {
  needs: document.querySelector("#needsYouNow"),
  doc: document.querySelector("#docAtWork"),
  activity: document.querySelector("#recentActivity"),
};

// One place holds what Home believes. Every render reads it; no renderer keeps its own copy.
const view = { scope: DEFAULT_SCOPE, payload: null, status: "loading", message: null, sequence: 0, freshnessKey: null };

/**
 * THE ORB SAYS ONLY WHAT IT MEASURES, WHICH IS ONE READ.
 *
 * These four strings are driven by exactly one thing: the state of the fetch to
 * /api/v1/command-center. "Workspace available" claimed more than that — it read as
 * a verdict on the workspace while the only evidence behind it was that a single
 * aggregate read came back. The slice's interfaces name an ops.doctorcre.com
 * read-only health projection; this surface does not consume one, and no producer
 * for one exists anywhere in the tree, so the honest label names the read.
 *
 * This is the discipline the static suite already applies to "System online": a
 * surface may not make a health claim it does not read. Wiring a real projection is
 * a different unit, and it starts by building the projection.
 */
const HEALTH_LABEL = {
  loading: "Checking the workspace read…",
  refreshing: "Refreshing the workspace read…",
  available: "Workspace read available",
  unavailable: "Workspace read unavailable",
};

function setHealth(state) {
  if (healthOrb) healthOrb.className = `status-orb ${state}`;
  if (healthLabel) healthLabel.textContent = HEALTH_LABEL[state] || HEALTH_LABEL.unavailable;
}


function formatObserved(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Observed time unavailable";
  return `Observed ${date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
}

function setAsOf(text) {
  if (observedAt) observedAt.textContent = text;
}

function sourceLabel(source) {
  if (!source) return "Source unavailable · freshness unknown";
  // The label states the freshness against the current clock; an expired stamp is never shown as fresh.
  return `Source: ${escapeHtml(humanSourceLabel(source.source))} · freshness: ${escapeHtml(displayedFreshness(source))} · ${escapeHtml(formatObserved(source.observed_at))}`;
}

/**
 * The card is replaced wholesale, so anything focused inside it is put back by id. When the
 * focused control is legitimately gone — Retry disappearing on a successful read, or during the
 * loading repaint it triggers — keyboard context stays in the same card on its primary action.
 */
function paintCard(html, className) {
  const restoreId = card.contains(document.activeElement) ? document.activeElement?.id : null;
  card.className = className;
  card.innerHTML = html;
  card.querySelector("#retryHome")?.addEventListener("click", () => load("retry"));
  if (!restoreId) return;
  const restored = card.querySelector(`#${CSS.escape(restoreId)}`) || card.querySelector("#homePrimaryAction");
  restored?.focus();
}

const WITHHELD_COPY = "This source is withheld until its tenant scope and freshness can be verified.";

function renderAggregate(target, title, detail, source, unavailable = false) {
  if (!target) return;
  target.className = `aggregate-card glass${unavailable ? " unavailable" : ""}`;
  target.innerHTML = `<p class="eyebrow">${escapeHtml(title)}</p><h2>${escapeHtml(detail.value)}</h2><p class="aggregate-detail">${escapeHtml(detail.copy)}</p><p class="source">${escapeHtml(detail.sourceText || "") || sourceLabel(source)}</p>`;
}

/** The Needs card's link belongs to the scope on screen now; a repaint never leaves the other scope's filter behind. */
function setNeedsHref(destination) {
  if (aggregateCards.needs) aggregateCards.needs.href = safeDestination(destination);
}

function setAggregates(title, value, copy, { source = null, unavailable = true, sourceText = "", destination = DEAL_ROOM_DESTINATION } = {}) {
  setNeedsHref(destination);
  Object.values(aggregateCards).forEach((target) => renderAggregate(target, title, { value, copy, sourceText }, source, unavailable));
}

function renderAggregates(payload, scope) {
  const now = () => Date.now();
  const cardStates = aggregateCardState(payload, scope, now);
  const summary = summarizeWorkspaceScope(payload, scope, now);
  if (cardStates.needs !== "fresh") {
    setAggregates("Stale read", "—", "Counts withheld until freshness is verified.", { source: payload.source, destination: summary.flaggedDestination });
    return;
  }
  // The flagged count and its link are the same filtered set, so the card can drill straight in.
  setNeedsHref(summary.flaggedDestination);
  renderAggregate(aggregateCards.needs, `Flagged · ${SCOPE_LABEL[scope]}`, {
    value: String(summary.flagged),
    copy: summary.flagged
      ? `Opens the same ${scope === "team" ? "team" : "owner-filtered"} flagged list in the Deal Room.`
      : `Nothing is flagged in ${scope === "team" ? "the team book" : "your own work"} right now.`,
  }, payload.metrics[SCOPES.indexOf(scope)].source);
  const doc = payload.doc_at_work[0];
  const docOut = cardStates.doc !== "fresh";
  renderAggregate(aggregateCards.doc, "Doc at work", docOut ? { value: "Unavailable", copy: WITHHELD_COPY }
    : { value: String(doc.count), copy: doc.count ? "Active nonhuman work is in progress." : "No active nonhuman work is reported." }, doc?.source, docOut);
  const activity = payload.recent_activity[0];
  const activityOut = cardStates.recent !== "fresh";
  renderAggregate(aggregateCards.activity, "Changed in 7 days", activityOut ? { value: "Unavailable", copy: WITHHELD_COPY }
    : { value: String(activity.count), copy: activity.count ? `Last ${formatObserved(activity.observed_at).toLowerCase()}.` : "No changed work is reported." }, activity?.source, activityOut);
}

function renderScopeControls() {
  scopeButtons.forEach((button) => {
    const selected = button.dataset.scope === view.scope;
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    button.classList.toggle("on", selected);
  });
  if (scopeNoteTarget) scopeNoteTarget.textContent = scopeNote(view.scope);
}

function homeCardHtml({ eyebrow, title, copy, action, retry, refreshing, source, count, countLabel, needsJoe, skeleton = false }) {
  const badge = refreshing ? '<span class="refresh-badge" id="refreshBadge">Refreshing…</span>' : "";
  const counted = skeleton ? '<div class="skeleton-line"></div><div class="skeleton-line short"></div>'
    : count === null ? "" : `<div class="count-line"><strong>${escapeHtml(count)}</strong><span>${escapeHtml(countLabel)}</span></div>`;
  const joeLink = needsJoe ? `<a class="action secondary-action" id="needsJoeLink" href="${escapeHtml(needsJoe.destination)}">${escapeHtml(`${needsJoe.count} system ${needsJoe.count === 1 ? "request needs" : "requests need"} Joe`)}</a>` : "";
  return `<div class="attention-icon" aria-hidden="true"><span></span></div><div class="attention-content"><p class="eyebrow">${escapeHtml(eyebrow)}${badge}</p><h2 id="attentionTitle">${escapeHtml(title)}</h2><p class="attention-copy">${escapeHtml(copy)}</p>${counted}<div class="home-actions"><a id="homePrimaryAction" class="action primary-action" data-primary-action data-state="${escapeHtml(action.state)}" href="${escapeHtml(action.href)}">${escapeHtml(action.label)}</a>${joeLink}${retry ? '<button class="action secondary-action" type="button" id="retryHome">Retry read</button>' : ""}</div>${source ? `<p class="source">${sourceLabel(source)}</p>` : ""}</div>`;
}

function renderLoading() {
  setHealth("loading");
  setAsOf("Reading canonical state…");
  card.setAttribute("aria-busy", "true");
  paintCard(homeCardHtml({
    eyebrow: `${SCOPE_LABEL[view.scope]} · loading`, title: "Loading your next place…", copy: "Checking verified workspace state.",
    action: { label: "Open Deal Room", href: DEAL_ROOM_DESTINATION, state: "loading" },
    retry: false, refreshing: false, source: null, count: null, countLabel: "", needsJoe: null, skeleton: true,
  }), "attention-card glass loading");
  setAggregates("Loading", "Loading…", "Reading safe aggregate state.", { unavailable: false, sourceText: "Source pending" });
}

function renderUnauthorized() {
  setHealth("unavailable");
  setAsOf("Session ended");
  card.setAttribute("aria-busy", "false");
  paintCard(homeCardHtml({
    eyebrow: "Private workspace", title: "Your session has ended", copy: "Sign in again to return Home.",
    action: primaryHomeAction(null, { unauthorized: true }), retry: false, refreshing: false, source: null, count: null, countLabel: "", needsJoe: null,
  }), "attention-card glass unavailable");
  setAggregates("Unavailable", "—", "Sign in to read this aggregate.");
}

function renderUnavailable(message) {
  setHealth("unavailable");
  setAsOf("No verified read");
  card.setAttribute("aria-busy", "false");
  const copy = message || "Home cannot verify the current read, so no stale count is shown as current.";
  paintCard(homeCardHtml({
    eyebrow: "Home read unavailable", title: "Progress could not be checked", copy,
    action: primaryHomeAction(null, { scope: view.scope }), retry: true, refreshing: false, source: null, count: null, countLabel: "", needsJoe: null,
  }), "attention-card glass unavailable");
  setAggregates("Unavailable", "—", "The overall read is unavailable.");
}

function renderSummary(refreshing) {
  const payload = view.payload;
  const now = () => Date.now();
  const summary = summarizeWorkspaceScope(payload, view.scope, now);
  const copy = homeCardCopy(summary);
  const stale = summary.state === "stale" || summary.state === "unavailable";
  card.setAttribute("aria-busy", refreshing ? "true" : "false");
  if (viewerWorkspace) viewerWorkspace.textContent = viewerWorkspaceLabel(payload.viewer);
  setHealth(refreshing ? "refreshing" : stale ? "unavailable" : "available");
  setAsOf(stale ? `${formatObserved(payload.source.observed_at)} · outside its freshness window` : formatObserved(payload.source.observed_at));
  if (stale) setAggregates("Stale read", "—", "Counts withheld until freshness is verified.", { source: payload.source, destination: summary.flaggedDestination });
  else renderAggregates(payload, view.scope);
  paintCard(homeCardHtml({
    eyebrow: copy.eyebrow, title: copy.title, copy: copy.copy,
    action: primaryHomeAction(payload, { scope: view.scope, now }),
    retry: stale, refreshing, source: payload.source, count: copy.count, countLabel: copy.countLabel,
    needsJoe: stale ? null : needsJoeWork(payload, now),
  }), `attention-card glass ${refreshing ? "refreshing " : ""}${stale ? "unavailable" : summary.state === "empty" ? "empty" : "ready"}`);
}

function render() {
  if (!card) return;
  renderScopeControls();
  // Remember exactly which freshness state was painted, so the tick repaints on a real change only.
  view.freshnessKey = view.payload ? freshnessSignature(view.payload, view.scope) : null;
  const phase = homeReadPhase({ status: view.status, payload: view.payload, scope: view.scope });
  if (phase === "unauthorized") return renderUnauthorized();
  if (phase === "unavailable" && !view.payload) return renderUnavailable(view.message);
  if (phase === "loading") return renderLoading();
  renderSummary(view.status === "refreshing");
}

function selectScope(scope, { focus = false } = {}) {
  if (!SCOPES.includes(scope) || scope === view.scope) return;
  view.scope = scope;
  render();
  if (focus) scopeButtons.find((button) => button.dataset.scope === scope)?.focus();
}

function wireScopeSwitch() {
  scopeButtons.forEach((button) => {
    button.addEventListener("click", () => selectScope(button.dataset.scope));
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") return;
      event.preventDefault();
      const index = scopeButtons.indexOf(button);
      const next = event.key === "Home" ? 0 : event.key === "End" ? scopeButtons.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : scopeButtons.length - 1)) % scopeButtons.length;
      selectScope(scopeButtons[next]?.dataset.scope, { focus: true });
    });
  });
}

/**
 * A local clock that has passed valid_until must stop presenting the counts as current. The
 * signature covers the selected scope's metric and each work card, so a card that expires on its
 * own deadline repaints even while the overall read is still inside its window.
 */
function watchExpiry() {
  setInterval(() => {
    if (!view.payload || view.status === "loading") return;
    const signature = freshnessSignature(view.payload, view.scope);
    if (signature === view.freshnessKey) return;
    view.freshnessKey = signature;
    render();
  }, EXPIRY_TICK_MS);
}

async function load(reason = "initial") {
  if (!card) return;
  const sequence = ++view.sequence;
  view.status = view.payload ? "refreshing" : "loading";
  if (reason === "retry" && !view.payload) view.message = null;
  render();
  try {
    const response = await fetch(ENDPOINT, { headers: { accept: "application/json" }, cache: "no-store" });
    if (!acceptsResponse(view.sequence, sequence)) return;
    if (response.status === 401 || response.status === 403) return settle({ status: "unauthorized" }, sequence);
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      if (!acceptsResponse(view.sequence, sequence)) return;
      if (failure.error === "AUTHENTICATION_REQUIRED" || failure.error === "AUTHORIZATION_REFUSED") return settle({ status: "unauthorized" }, sequence);
      return settle({ status: "error", message: failure.error === "DEPENDENCY_UNAVAILABLE" ? "The canonical read is unavailable right now." : undefined }, sequence);
    }
    const payload = await response.json().catch(() => null);
    if (!acceptsResponse(view.sequence, sequence)) return;
    if (!validWorkspacePayload(payload)) return settle({ status: "error", message: "The canonical read returned an unexpected shape, so the count was withheld." }, sequence);
    settle({ status: "ready", payload }, sequence);
  } catch {
    if (!acceptsResponse(view.sequence, sequence)) return;
    settle({ status: "error", message: "The workspace could not reach the canonical read. Nothing here has been inferred." }, sequence);
  }
}

function settle({ status, payload = null, message = null }, sequence) {
  if (!acceptsResponse(view.sequence, sequence)) return;
  view.status = status;
  view.message = message;
  if (status === "ready") {
    view.payload = payload;
  } else {
    // A failed or refused read never keeps an older payload alive as if current.
    view.payload = null;
  }
  render();
}

wireScopeSwitch();
watchExpiry();
load();
