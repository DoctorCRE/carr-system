export const DEAL_ROOM_DESTINATION = "/deals";
export const TEAM_ACTIVE_DESTINATION = "/deals?workspace=team";
export const TEAM_FLAGGED_DESTINATION = "/deals?workspace=team&filter=flagged";
export const MY_FLAGGED_DESTINATION = "/deals?workspace=team&filter=flagged&owner=me";
export const NEEDS_JOE_DESTINATION = "/system-work.html";
// Team is the confirmed default; My work is the secondary view of the same read.
export const SCOPES = ["team", "mine"];
export const DEFAULT_SCOPE = "team";

const VIEWERS = new Set(["joe", "dell"]);
// Only links the Deal Room boot parser already honors may leave Home.
const KNOWN_DESTINATIONS = new Set([DEAL_ROOM_DESTINATION, TEAM_ACTIVE_DESTINATION, TEAM_FLAGGED_DESTINATION, MY_FLAGGED_DESTINATION, NEEDS_JOE_DESTINATION]);
const NEED_DESTINATION = { team_flagged_deals: TEAM_FLAGGED_DESTINATION, my_flagged_deals: MY_FLAGGED_DESTINATION, needs_joe_work: NEEDS_JOE_DESTINATION };
const NEED_SCOPE = { team_flagged_deals: "team", my_flagged_deals: "mine", needs_joe_work: "mine" };
// "mine, active" has no Deal Room URL form, so that cell must stay null.
const SCOPE_DESTINATIONS = {
  team: { active: TEAM_ACTIVE_DESTINATION, flagged: TEAM_FLAGGED_DESTINATION },
  mine: { active: null, flagged: MY_FLAGGED_DESTINATION },
};
export const SCOPE_LABEL = { team: "Team book", mine: "My work" };
const CARD_SOURCES = new Set(["v_deal_room_board", "ops.work_request"]);
const ALL_SOURCES = new Set(["command_center", ...CARD_SOURCES]);

export function safeDestination(value) {
  return KNOWN_DESTINATIONS.has(value) ? value : DEAL_ROOM_DESTINATION;
}

function iso(value) {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function sourceValid(source, allowed = CARD_SOURCES) {
  const sourceKeys = new Set(["source", "source_ref", "observed_at", "valid_until", "freshness", "correlation_id", "safe_explanation"]);
  return source && Object.keys(source).every((key) => sourceKeys.has(key)) && allowed.has(source.source) && ["fresh", "stale", "missing", "unknown"].includes(source.freshness) &&
    iso(source.observed_at) && iso(source.valid_until) && Date.parse(source.valid_until) > Date.parse(source.observed_at) &&
    typeof source.correlation_id === "string" && source.correlation_id.length > 0;
}

function cardSourceValid(source) {
  return sourceValid(source) && source.freshness === "fresh";
}

function count(value) {
  return Number.isInteger(value) && value >= 0;
}

function exactKeys(value, keys) {
  return value && Object.keys(value).sort().join(",") === keys.slice().sort().join(",");
}

/**
 * `this_week` and `recent_calls` are named in the exact-key contract and NO producer
 * fills either: mcp-server/src/workspace-command-center.js emits [] for both. They
 * were checked with `Array.isArray` alone, which let a future producer put any shape
 * at all into either while both this validator and the page stayed silent — a dead
 * field with an open door.
 *
 * There is no honest element shape to declare instead of the door. A `this_week` row
 * would have to carry a Deal Room destination, and the board has no URL form for a
 * week or a deadline list — this suite's own "Home only links to Deal Room filters
 * the board already honors" test pins that. A `recent_calls` row would be a Calls
 * affordance, and Calls is inert in this release. So the declared shape is the shape
 * the data actually has: EMPTY. A producer that fills either is refused here and has
 * to come back and declare the element together with the renderer that shows it.
 */
function emptyContractList(value) {
  return Array.isArray(value) && value.length === 0;
}

export function sourceIsFresh(source, now = () => Date.now()) {
  return sourceValid(source, ALL_SOURCES) && source.freshness === "fresh" && Date.parse(source.valid_until) > now();
}

/** What the source is against the current clock, not the freshness it claimed when it was stamped. */
export function displayedFreshness(source, now = () => Date.now()) {
  if (!sourceValid(source, ALL_SOURCES)) return "unknown";
  if (source.freshness !== "fresh") return source.freshness;
  return Date.parse(source.valid_until) > now() ? "fresh" : "expired";
}

function metricValid(metric, scope) {
  const destinations = SCOPE_DESTINATIONS[scope];
  return exactKeys(metric, ["scope", "active_deals", "flagged_deals", "active_destination", "flagged_destination", "source"]) &&
    metric.scope === scope && count(metric.active_deals) && count(metric.flagged_deals) && metric.flagged_deals <= metric.active_deals &&
    metric.active_destination === destinations.active && metric.flagged_destination === destinations.flagged && cardSourceValid(metric.source);
}

function needsValid(payload) {
  const needs = payload.needs_you_now;
  if (!Array.isArray(needs)) return false;
  if (!needs.every((item) => exactKeys(item, ["kind", "scope", "count", "destination"]) && NEED_SCOPE[item.kind] &&
    item.scope === NEED_SCOPE[item.kind] && item.destination === NEED_DESTINATION[item.kind] && count(item.count))) return false;
  for (const kind of Object.keys(NEED_DESTINATION)) {
    if (needs.filter((item) => item.kind === kind).length > 1) return false;
  }
  // The flagged rows are the same facts as the metrics; a disagreement is the
  // exact defect that let Home report a clear book while deals were flagged.
  const [team, mine] = payload.metrics;
  const teamNeed = needs.find((item) => item.kind === "team_flagged_deals");
  const myNeed = needs.find((item) => item.kind === "my_flagged_deals");
  return Boolean(teamNeed && myNeed) && teamNeed.count === team.flagged_deals && myNeed.count === mine.flagged_deals;
}

export function validWorkspacePayload(payload) {
  if (!payload || Object.keys(payload).sort().join(",") !== "doc_at_work,metrics,needs_you_now,recent_activity,recent_calls,source,this_week,viewer") return false;
  if (!VIEWERS.has(payload.viewer) || !sourceValid(payload.source, new Set(["command_center"]))) return false;
  if (!iso(payload.source.valid_until) || Date.parse(payload.source.valid_until) <= Date.parse(payload.source.observed_at)) return false;
  if (!Array.isArray(payload.metrics) || payload.metrics.length !== SCOPES.length) return false;
  if (!SCOPES.every((scope, index) => metricValid(payload.metrics[index], scope))) return false;
  const [team, mine] = payload.metrics;
  if (mine.active_deals > team.active_deals || mine.flagged_deals > team.flagged_deals) return false;
  if (!needsValid(payload)) return false;
  if (!emptyContractList(payload.this_week) || !emptyContractList(payload.recent_calls)) return false;
  if (!Array.isArray(payload.doc_at_work) || payload.doc_at_work.length !== 1 || !payload.doc_at_work.every((item) => (exactKeys(item, ["state"]) && item.state === "unavailable") || (exactKeys(item, ["kind", "count", "source"]) && item.kind === "active_nonhuman_work" && count(item.count) && cardSourceValid(item.source)))) return false;
  if (!Array.isArray(payload.recent_activity) || payload.recent_activity.length !== 1 || !payload.recent_activity.every((item) => (exactKeys(item, ["state"]) && item.state === "unavailable") || (exactKeys(item, ["kind", "count", "observed_at", "source"]) && item.kind === "changed_work" && count(item.count) && iso(item.observed_at) && cardSourceValid(item.source)))) return false;
  return true;
}

/** Per-scope view of the one aggregate read. Counts are withheld unless both the read and the deal card are fresh right now. */
export function summarizeWorkspaceScope(payload, scope = DEFAULT_SCOPE, now = () => Date.now()) {
  const unavailable = { scope, state: "unavailable", flagged: null, active: null, teamFlagged: null, flaggedDestination: null, activeDestination: null };
  if (!SCOPES.includes(scope) || !validWorkspacePayload(payload)) return unavailable;
  const metric = payload.metrics[SCOPES.indexOf(scope)];
  const destinations = { flaggedDestination: metric.flagged_destination, activeDestination: metric.active_destination };
  if (!sourceIsFresh(payload.source, now) || !sourceIsFresh(metric.source, now)) return { ...unavailable, state: "stale", ...destinations };
  return {
    scope, state: metric.flagged_deals === 0 ? "empty" : "attention",
    flagged: metric.flagged_deals, active: metric.active_deals, teamFlagged: payload.metrics[0].flagged_deals, ...destinations,
  };
}

export function needsJoeWork(payload, now = () => Date.now()) {
  if (!validWorkspacePayload(payload) || !sourceIsFresh(payload.source, now)) return null;
  return payload.needs_you_now.find((item) => item.kind === "needs_joe_work") || null;
}

/** Home headline text. Never claims a scope with zero flagged deals means the team book is clear. */
export function homeCardCopy(summary) {
  const label = SCOPE_LABEL[summary.scope] || "Workspace";
  const deals = (value) => `${value} ${value === 1 ? "deal" : "deals"}`;
  if (summary.state === "stale") {
    return { eyebrow: `${label} · stale read`, title: "Read needs verification", count: "—", countLabel: "count withheld",
      copy: "This canonical read is older than its freshness window. Retry the read or open the owning Deal Room view before acting." };
  }
  if (summary.state === "unavailable") {
    return { eyebrow: `${label} · unavailable`, title: "Progress could not be checked", count: "—", countLabel: "count withheld",
      copy: "Home cannot verify the current read, so no count is shown as current." };
  }
  // Two independent clauses: the personal active count is not a subset of the team flagged total.
  const mineCountLabel = `${deals(summary.active)} active and owned by you · ${summary.teamFlagged} flagged team-wide`;
  if (summary.state === "empty") {
    return summary.scope === "team"
      ? { eyebrow: "Team book · no flagged work", title: "No team deal is flagged", count: 0, countLabel: `${deals(summary.active)} active across the team`,
        copy: `The active team book holds ${deals(summary.active)}, and none is flagged for attention.` }
      : { eyebrow: "My work · no flagged work", title: "None of your deals are flagged", count: 0, countLabel: mineCountLabel,
        copy: `You own ${deals(summary.active)} in the active team book. ${summary.teamFlagged === 0 ? "The team book has no flagged deals either." : `The team book still has ${deals(summary.teamFlagged)} flagged — switch to Team to review ${summary.teamFlagged === 1 ? "it" : "them"}.`}` };
  }
  return summary.scope === "team"
    ? { eyebrow: "Team book · attention", title: `${summary.flagged} flagged team ${summary.flagged === 1 ? "deal needs" : "deals need"} attention`, count: summary.flagged, countLabel: `${deals(summary.active)} active across the team`,
      copy: "These team-book records are flagged for partner attention. Review the owning Deal Room view before deciding what changes." }
    : { eyebrow: "My work · attention", title: `${summary.flagged} of your ${summary.flagged === 1 ? "deals is" : "deals are"} flagged`, count: summary.flagged, countLabel: mineCountLabel,
      copy: "These flagged records are owned by you. Review the owning Deal Room view before deciding what changes." };
}

export function aggregateCardState(payload, scope = DEFAULT_SCOPE, now = () => Date.now()) {
  if (!SCOPES.includes(scope) || !validWorkspacePayload(payload)) return { needs: "unavailable", doc: "unavailable", recent: "unavailable" };
  const metric = payload.metrics[SCOPES.indexOf(scope)];
  if (!sourceIsFresh(payload.source, now) || !sourceIsFresh(metric.source, now)) return { needs: "stale", doc: "stale", recent: "stale" };
  const doc = payload.doc_at_work[0];
  const recent = payload.recent_activity[0];
  return {
    needs: "fresh",
    doc: doc.state === "unavailable" ? "unavailable" : sourceIsFresh(doc.source, now) ? "fresh" : "unavailable",
    recent: recent.state === "unavailable" ? "unavailable" : sourceIsFresh(recent.source, now) ? "fresh" : "unavailable",
  };
}

/**
 * Everything whose displayed state can change purely because the clock moved: the read, the
 * SELECTED scope's metric, and each work card, since the contract lets every source carry its
 * own valid_until. A changed signature is the only reason to repaint on a tick.
 */
export function freshnessSignature(payload, scope = DEFAULT_SCOPE, now = () => Date.now()) {
  if (!SCOPES.includes(scope) || !validWorkspacePayload(payload)) return "invalid";
  const cards = aggregateCardState(payload, scope, now);
  const metric = payload.metrics[SCOPES.indexOf(scope)];
  return [scope, displayedFreshness(payload.source, now), displayedFreshness(metric.source, now), cards.needs, cards.doc, cards.recent].join("|");
}

export function viewerWorkspaceLabel(viewer) {
  return viewer === "joe" ? "Joe’s workspace" : viewer === "dell" ? "Dell’s workspace" : "Partner workspace";
}

export function scopeNote(scope) {
  return scope === "mine"
    ? "Showing your own work. Team is the default view."
    : "Showing the combined team book for Joe and Dell. My work is the secondary view.";
}

export function humanSourceLabel(source) {
  return ({
    command_center: "Command Center read",
    v_deal_room_board: "Deal Room board",
    "ops.work_request": "System work",
  })[source] || "Source unavailable";
}

export function primaryHomeAction(payload, { scope = DEFAULT_SCOPE, now = () => Date.now(), unauthorized = false } = {}) {
  if (unauthorized) return { label: "Sign in", href: "/auth/login?return_to=%2F", state: "unauthorized" };
  const summary = summarizeWorkspaceScope(payload, scope, now);
  if (summary.state === "attention") {
    return { label: scope === "team" ? "Review flagged team deals" : "Review my flagged deals", href: safeDestination(summary.flaggedDestination), state: "attention" };
  }
  if (summary.state === "empty") return { label: "Open the team book", href: TEAM_ACTIVE_DESTINATION, state: "empty" };
  return { label: "Open Deal Room", href: DEAL_ROOM_DESTINATION, state: summary.state === "stale" ? "stale" : "unavailable" };
}

/** Loading, refreshing, stale and unavailable are four different things and must render as four different things. */
export function homeReadPhase({ status, payload, scope = DEFAULT_SCOPE, now = () => Date.now() }) {
  if (status === "unauthorized") return "unauthorized";
  if (status === "error") return "unavailable";
  if (!payload) return "loading";
  const summary = summarizeWorkspaceScope(payload, scope, now);
  if (status === "refreshing") return summary.state === "unavailable" ? "refreshing-unverified" : "refreshing";
  return summary.state;
}

/** A response is only allowed to paint if no newer read has been started since it left. */
export function acceptsResponse(currentSequence, responseSequence) {
  return Number.isInteger(currentSequence) && Number.isInteger(responseSequence) && responseSequence === currentSequence;
}
