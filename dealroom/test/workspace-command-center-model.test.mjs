import test from "node:test";
import assert from "node:assert/strict";
import {
  MY_FLAGGED_DESTINATION, TEAM_ACTIVE_DESTINATION, TEAM_FLAGGED_DESTINATION, acceptsResponse, aggregateCardState, displayedFreshness, freshnessSignature,
  homeCardCopy, homeReadPhase, humanSourceLabel, needsJoeWork, primaryHomeAction, safeDestination, scopeNote, summarizeWorkspaceScope, validWorkspacePayload,
  viewerWorkspaceLabel,
} from "../js/workspace-command-center-model.js";
import { readCommandCenterSummary } from "../../mcp-server/src/workspace-command-center.js";

const OBSERVED = "2099-08-24T15:00:00.000Z";
const EXPIRES = "2099-08-24T15:01:00.000Z";
const INSIDE = () => Date.parse("2099-08-24T15:00:30.000Z");
const OUTSIDE = () => Date.parse("2099-08-24T15:02:00.000Z");
const cardSource = (overrides = {}) => ({ source: "v_deal_room_board", observed_at: OBSERVED, valid_until: EXPIRES, freshness: "fresh", correlation_id: "corr-test", ...overrides });
const workSource = (overrides = {}) => ({ ...cardSource(), source: "ops.work_request", ...overrides });

const payload = (overrides = {}) => ({
  viewer: "joe",
  needs_you_now: [
    { kind: "team_flagged_deals", scope: "team", count: 3, destination: TEAM_FLAGGED_DESTINATION },
    { kind: "my_flagged_deals", scope: "mine", count: 1, destination: MY_FLAGGED_DESTINATION },
  ],
  this_week: [],
  metrics: [
    { scope: "team", active_deals: 6, flagged_deals: 3, active_destination: TEAM_ACTIVE_DESTINATION, flagged_destination: TEAM_FLAGGED_DESTINATION, source: cardSource() },
    { scope: "mine", active_deals: 2, flagged_deals: 1, active_destination: null, flagged_destination: MY_FLAGGED_DESTINATION, source: cardSource() },
  ],
  recent_calls: [],
  doc_at_work: [{ kind: "active_nonhuman_work", count: 2, source: workSource() }],
  recent_activity: [{ kind: "changed_work", count: 3, observed_at: "2099-08-24T14:30:00.000Z", source: workSource() }],
  source: { source: "command_center", source_ref: "v_deal_room_board+ops.work_request", observed_at: OBSERVED, valid_until: EXPIRES, freshness: "fresh", correlation_id: "corr-test", safe_explanation: "Fresh because this is a no-store request-time canonical database aggregate; valid for 60 seconds." },
  ...overrides,
});

const clearBook = () => payload({
  needs_you_now: [
    { kind: "team_flagged_deals", scope: "team", count: 0, destination: TEAM_FLAGGED_DESTINATION },
    { kind: "my_flagged_deals", scope: "mine", count: 0, destination: MY_FLAGGED_DESTINATION },
  ],
  metrics: [
    { ...payload().metrics[0], flagged_deals: 0 },
    { ...payload().metrics[1], flagged_deals: 0 },
  ],
});

const myWorkClear = () => payload({
  needs_you_now: [
    { kind: "team_flagged_deals", scope: "team", count: 3, destination: TEAM_FLAGGED_DESTINATION },
    { kind: "my_flagged_deals", scope: "mine", count: 0, destination: MY_FLAGGED_DESTINATION },
  ],
  metrics: [payload().metrics[0], { ...payload().metrics[1], flagged_deals: 0 }],
});

test("malformed aggregate payload is unavailable and never becomes a zero", () => {
  assert.equal(validWorkspacePayload({}), false);
  assert.deepEqual(summarizeWorkspaceScope({}, "team"), { scope: "team", state: "unavailable", flagged: null, active: null, teamFlagged: null, flaggedDestination: null, activeDestination: null });
  assert.equal(homeCardCopy(summarizeWorkspaceScope({}, "team")).count, "—");
});

test("team is the default scope and mine reads the same payload separately", () => {
  assert.equal(validWorkspacePayload(payload()), true);
  const team = summarizeWorkspaceScope(payload(), undefined, INSIDE);
  assert.deepEqual(team, { scope: "team", state: "attention", flagged: 3, active: 6, teamFlagged: 3, flaggedDestination: TEAM_FLAGGED_DESTINATION, activeDestination: TEAM_ACTIVE_DESTINATION });
  const mine = summarizeWorkspaceScope(payload(), "mine", INSIDE);
  assert.deepEqual(mine, { scope: "mine", state: "attention", flagged: 1, active: 2, teamFlagged: 3, flaggedDestination: MY_FLAGGED_DESTINATION, activeDestination: null });
  assert.equal(summarizeWorkspaceScope(payload(), "everyone", INSIDE).state, "unavailable");
});

test("every emitted link is a Deal Room filter that already exists", () => {
  assert.equal(safeDestination(TEAM_FLAGGED_DESTINATION), TEAM_FLAGGED_DESTINATION);
  assert.equal(safeDestination(MY_FLAGGED_DESTINATION), MY_FLAGGED_DESTINATION);
  assert.equal(safeDestination("/deals?workspace=team&filter=mine"), "/deals");
  assert.equal(safeDestination("/deals?filter=waiting"), "/deals");
  assert.equal(safeDestination(null), "/deals");
  // A metric whose destination is not the one supported for its scope is refused outright.
  assert.equal(validWorkspacePayload(payload({ metrics: [{ ...payload().metrics[0], flagged_destination: MY_FLAGGED_DESTINATION }, payload().metrics[1]] })), false);
  assert.equal(validWorkspacePayload(payload({ metrics: [payload().metrics[0], { ...payload().metrics[1], active_destination: "/deals?workspace=team&filter=mine" }] })), false);
});

test("zero flagged in my work never claims the team book is clear", () => {
  const mine = summarizeWorkspaceScope(myWorkClear(), "mine", INSIDE);
  assert.equal(mine.state, "empty");
  assert.equal(mine.flagged, 0);
  assert.equal(mine.teamFlagged, 3);
  const copy = homeCardCopy(mine);
  assert.equal(copy.title, "None of your deals are flagged");
  assert.match(copy.copy, /team book still has 3 deals flagged/);
  assert.doesNotMatch(copy.copy, /clear/i);
  const team = homeCardCopy(summarizeWorkspaceScope(myWorkClear(), "team", INSIDE));
  assert.match(team.title, /3 flagged team deals need attention/);
});

test("an actually clear team book says so in both scopes", () => {
  const team = homeCardCopy(summarizeWorkspaceScope(clearBook(), "team", INSIDE));
  assert.equal(team.title, "No team deal is flagged");
  assert.match(team.copy, /none is flagged/);
  const mine = homeCardCopy(summarizeWorkspaceScope(clearBook(), "mine", INSIDE));
  assert.equal(mine.title, "None of your deals are flagged");
  assert.match(mine.copy, /no flagged deals either/);
});

test("empty and unavailable are different states with different copy", () => {
  const empty = homeCardCopy(summarizeWorkspaceScope(clearBook(), "team", INSIDE));
  const unavailable = homeCardCopy(summarizeWorkspaceScope(null, "team", INSIDE));
  assert.equal(empty.count, 0);
  assert.equal(unavailable.count, "—");
  assert.notEqual(empty.title, unavailable.title);
  assert.match(unavailable.copy, /cannot verify/);
});

test("stale aggregate payload withholds counts while preserving the owning destination", () => {
  const stale = summarizeWorkspaceScope(payload({ source: { ...payload().source, freshness: "stale" } }), "team", INSIDE);
  assert.deepEqual(stale, { scope: "team", state: "stale", flagged: null, active: null, teamFlagged: null, flaggedDestination: TEAM_FLAGGED_DESTINATION, activeDestination: TEAM_ACTIVE_DESTINATION });
  assert.equal(homeCardCopy(stale).countLabel, "count withheld");
});

test("expired, missing, malformed, or unsafe contract freshness withholds counts in both scopes", () => {
  for (const source of [
    { ...payload().source, valid_until: "2099-08-24T14:59:59.000Z" },
    { ...payload().source, valid_until: null },
    { ...payload().source, freshness: "unknown" },
    { ...payload().source, valid_until: "not-a-date" },
  ]) {
    for (const scope of ["team", "mine"]) {
      const result = summarizeWorkspaceScope(payload({ source }), scope, INSIDE);
      assert.equal(result.flagged, null);
      assert.equal(result.active, null);
    }
  }
});

test("a local clock past valid_until stops reporting available fresh counts", () => {
  const fresh = payload();
  assert.equal(summarizeWorkspaceScope(fresh, "team", INSIDE).state, "attention");
  assert.equal(summarizeWorkspaceScope(fresh, "team", OUTSIDE).state, "stale");
  assert.equal(summarizeWorkspaceScope(fresh, "mine", OUTSIDE).flagged, null);
  assert.deepEqual(aggregateCardState(fresh, "team", OUTSIDE), { needs: "stale", doc: "stale", recent: "stale" });
  assert.equal(primaryHomeAction(fresh, { scope: "team", now: OUTSIDE }).state, "stale");
});

test("the personal count label states two facts and never reads as a subset of the flagged total", () => {
  const mine = homeCardCopy(summarizeWorkspaceScope(payload(), "mine", INSIDE));
  assert.equal(mine.countLabel, "2 deals active and owned by you · 3 flagged team-wide");
  assert.doesNotMatch(mine.countLabel, /\bof \d+ flagged/);
  const mineEmpty = homeCardCopy(summarizeWorkspaceScope(myWorkClear(), "mine", INSIDE));
  assert.equal(mineEmpty.countLabel, "2 deals active and owned by you · 3 flagged team-wide");
  assert.doesNotMatch(mineEmpty.countLabel, /\bof \d+ flagged/);
  assert.equal(homeCardCopy(summarizeWorkspaceScope(payload(), "team", INSIDE)).countLabel, "6 deals active across the team");
});

test("team copy describes the team book and never attributes unassigned records to a partner", () => {
  for (const state of [payload(), clearBook(), myWorkClear()]) {
    const team = homeCardCopy(summarizeWorkspaceScope(state, "team", INSIDE));
    assert.doesNotMatch(team.copy, /Joe|Dell/);
    assert.doesNotMatch(team.countLabel, /Joe|Dell/);
    assert.match(team.copy, /team.book/i);
  }
  assert.match(homeCardCopy(summarizeWorkspaceScope(clearBook(), "team", INSIDE)).copy, /The active team book holds 6 deals/);
});

test("the displayed freshness is derived from the current clock, not the stamp", () => {
  const source = payload().source;
  assert.equal(displayedFreshness(source, INSIDE), "fresh");
  assert.equal(displayedFreshness(source, OUTSIDE), "expired");
  assert.equal(displayedFreshness({ ...source, freshness: "unknown" }, INSIDE), "unknown");
  assert.equal(displayedFreshness({ ...source, valid_until: "not-a-date" }, INSIDE), "unknown");
  assert.equal(displayedFreshness(null, INSIDE), "unknown");
  // The state and the label agree: a withheld count is never labelled fresh.
  assert.equal(summarizeWorkspaceScope(payload(), "team", OUTSIDE).state, "stale");
  assert.equal(displayedFreshness(payload().source, OUTSIDE), "expired");
});

test("the freshness signature follows the selected metric and each work card's own deadline", () => {
  const soon = "2099-08-24T15:00:20.000Z";
  const perScope = payload({
    metrics: [
      { ...payload().metrics[0], source: cardSource({ valid_until: soon }) },
      payload().metrics[1],
    ],
  });
  assert.equal(validWorkspacePayload(perScope), true);
  // The team metric expires on its own deadline while the read and the mine metric are still current.
  assert.equal(summarizeWorkspaceScope(perScope, "team", INSIDE).state, "stale");
  assert.equal(summarizeWorkspaceScope(perScope, "mine", INSIDE).state, "attention");
  assert.notEqual(freshnessSignature(perScope, "team", INSIDE), freshnessSignature(perScope, "mine", INSIDE));
  assert.notEqual(freshnessSignature(perScope, "team", () => Date.parse("2099-08-24T15:00:10.000Z")), freshnessSignature(perScope, "team", INSIDE));
  // A work card expiring alone still changes what is on screen.
  const docExpires = payload({ doc_at_work: [{ kind: "active_nonhuman_work", count: 2, source: workSource({ valid_until: soon }) }] });
  assert.notEqual(freshnessSignature(docExpires, "team", () => Date.parse("2099-08-24T15:00:10.000Z")), freshnessSignature(docExpires, "team", INSIDE));
  assert.equal(freshnessSignature(docExpires, "team", INSIDE).endsWith("fresh|unavailable|fresh"), true);
  assert.equal(freshnessSignature({}, "team", INSIDE), "invalid");
  assert.equal(freshnessSignature(payload(), "everyone", INSIDE), "invalid");
});

test("individual card expiry withholds only that card and empty work cards are invalid", () => {
  const expiredDoc = payload({ doc_at_work: [{ kind: "active_nonhuman_work", count: 2, source: workSource({ valid_until: "2099-08-24T15:00:10.000Z" }) }] });
  assert.deepEqual(aggregateCardState(expiredDoc, "team", INSIDE), { needs: "fresh", doc: "unavailable", recent: "fresh" });
  assert.deepEqual(aggregateCardState(expiredDoc, "mine", INSIDE), { needs: "fresh", doc: "unavailable", recent: "fresh" });
  assert.equal(validWorkspacePayload(payload({ doc_at_work: [] })), false);
  assert.equal(validWorkspacePayload(payload({ recent_activity: [] })), false);
});

test("impossible or disagreeing counts are refused rather than rendered", () => {
  const [team, mine] = payload().metrics;
  // flagged above active within a scope
  assert.equal(validWorkspacePayload(payload({ metrics: [{ ...team, flagged_deals: 9 }, mine] })), false);
  // mine is a subset of team, so it can never exceed it
  assert.equal(validWorkspacePayload(payload({ metrics: [team, { ...mine, active_deals: 9 }] })), false);
  assert.equal(validWorkspacePayload(payload({ metrics: [team, { ...mine, flagged_deals: 5, active_deals: 6 }] })), false);
  // needs_you_now must agree with the metric it summarizes
  assert.equal(validWorkspacePayload(payload({ needs_you_now: [{ kind: "team_flagged_deals", scope: "team", count: 0, destination: TEAM_FLAGGED_DESTINATION }, payload().needs_you_now[1]] })), false);
  assert.equal(validWorkspacePayload(payload({ needs_you_now: [payload().needs_you_now[0]] })), false);
  assert.equal(validWorkspacePayload(payload({ needs_you_now: [...payload().needs_you_now, payload().needs_you_now[0]] })), false);
  // scope order, unknown scopes and non-integer counts
  assert.equal(validWorkspacePayload(payload({ metrics: [mine, team] })), false);
  assert.equal(validWorkspacePayload(payload({ metrics: [{ ...team, scope: "everyone" }, mine] })), false);
  assert.equal(validWorkspacePayload(payload({ metrics: [{ ...team, flagged_deals: 1.5 }, mine] })), false);
  assert.equal(validWorkspacePayload(payload({ metrics: [{ ...team, active_deals: -1 }, mine] })), false);
  assert.equal(validWorkspacePayload(payload({ metrics: [team] })), false);
  assert.equal(validWorkspacePayload(payload({ viewer: "stranger" })), false);
  assert.equal(validWorkspacePayload(payload({ extra_key: true })), false);
});

test("arbitrary array-shaped payloads are not accepted", () => {
  assert.equal(validWorkspacePayload({ viewer: "joe", needs_you_now: [], this_week: [], metrics: [], recent_calls: [], doc_at_work: [], recent_activity: [], source: { source: "command_center", observed_at: "now", correlation_id: "x", freshness: "fresh" } }), false);
});

test("actual server response is accepted by the browser model and separates the two scopes", async () => {
  const rows = { team_active: "6", team_flagged: "3", mine_active: "2", mine_flagged: "1" };
  const client = { query: async (sql) => ({ rows: [sql.includes("v_deal_room_board") ? rows : { needs_viewer: "0", doc_at_work: "1", changed_count: "2", changed_at: "2099-08-24T14:30:00.000Z", legacy_unscoped_held: "0", legacy_unscoped_recent: "0" }] }) };
  const serverPayload = await readCommandCenterSummary({ client, actor: { slug: "joe" }, correlationId: "corr-integration", now: () => new Date(OBSERVED) });
  assert.equal(validWorkspacePayload(serverPayload), true);
  assert.deepEqual(summarizeWorkspaceScope(serverPayload, "team", INSIDE), { scope: "team", state: "attention", flagged: 3, active: 6, teamFlagged: 3, flaggedDestination: TEAM_FLAGGED_DESTINATION, activeDestination: TEAM_ACTIVE_DESTINATION });
  assert.deepEqual(summarizeWorkspaceScope(serverPayload, "mine", INSIDE), { scope: "mine", state: "attention", flagged: 1, active: 2, teamFlagged: 3, flaggedDestination: MY_FLAGGED_DESTINATION, activeDestination: null });
  assert.equal(primaryHomeAction(serverPayload, { scope: "mine", now: INSIDE }).href, MY_FLAGGED_DESTINATION);
});

test("a server response whose viewer owns nothing still shows the team's flagged work", async () => {
  const rows = { team_active: "6", team_flagged: "3", mine_active: "0", mine_flagged: "0" };
  const client = { query: async (sql) => ({ rows: [sql.includes("v_deal_room_board") ? rows : { needs_viewer: "0", doc_at_work: "0", changed_count: "0", changed_at: null, legacy_unscoped_held: "0", legacy_unscoped_recent: "0" }] }) };
  const serverPayload = await readCommandCenterSummary({ client, actor: { slug: "dell" }, correlationId: "corr-empty", now: () => new Date(OBSERVED) });
  assert.equal(validWorkspacePayload(serverPayload), true);
  assert.equal(summarizeWorkspaceScope(serverPayload, "team", INSIDE).flagged, 3);
  const mine = summarizeWorkspaceScope(serverPayload, "mine", INSIDE);
  assert.equal(mine.state, "empty");
  assert.match(homeCardCopy(mine).copy, /team book still has 3 deals flagged/);
});

test("Home identity comes only from the verified viewer", () => {
  assert.equal(viewerWorkspaceLabel("joe"), "Joe’s workspace");
  assert.equal(viewerWorkspaceLabel("dell"), "Dell’s workspace");
  assert.equal(viewerWorkspaceLabel("other"), "Partner workspace");
  assert.match(scopeNote("team"), /combined team book/i);
  assert.match(scopeNote("mine"), /Team is the default/i);
});

test("Home primary action resolves from the selected scope's verified freshness and count", () => {
  assert.deepEqual(primaryHomeAction(payload(), { scope: "team", now: INSIDE }), { label: "Review flagged team deals", href: TEAM_FLAGGED_DESTINATION, state: "attention" });
  assert.deepEqual(primaryHomeAction(payload(), { scope: "mine", now: INSIDE }), { label: "Review my flagged deals", href: MY_FLAGGED_DESTINATION, state: "attention" });
  assert.deepEqual(primaryHomeAction(myWorkClear(), { scope: "mine", now: INSIDE }), { label: "Open the team book", href: TEAM_ACTIVE_DESTINATION, state: "empty" });
  assert.deepEqual(primaryHomeAction(payload(), { now: OUTSIDE }), { label: "Open Deal Room", href: "/deals", state: "stale" });
  assert.deepEqual(primaryHomeAction({}, {}), { label: "Open Deal Room", href: "/deals", state: "unavailable" });
  assert.deepEqual(primaryHomeAction(null, { unauthorized: true }), { label: "Sign in", href: "/auth/login?return_to=%2F", state: "unauthorized" });
});

test("the needs-Joe row is exposed only while the read is fresh", () => {
  const withJoeWork = payload({ needs_you_now: [...payload().needs_you_now, { kind: "needs_joe_work", scope: "mine", count: 2, destination: "/system-work.html" }] });
  assert.equal(validWorkspacePayload(withJoeWork), true);
  assert.equal(needsJoeWork(withJoeWork, INSIDE).count, 2);
  assert.equal(needsJoeWork(withJoeWork, OUTSIDE), null);
  assert.equal(needsJoeWork(payload(), INSIDE), null);
  assert.equal(needsJoeWork({}, INSIDE), null);
});

test("loading, refreshing, stale and unavailable are four distinct rendered phases", () => {
  assert.equal(homeReadPhase({ status: "loading", payload: null }), "loading");
  assert.equal(homeReadPhase({ status: "refreshing", payload: null }), "loading");
  assert.equal(homeReadPhase({ status: "refreshing", payload: payload(), now: INSIDE }), "refreshing");
  assert.equal(homeReadPhase({ status: "refreshing", payload: {}, now: INSIDE }), "refreshing-unverified");
  assert.equal(homeReadPhase({ status: "ready", payload: payload(), now: INSIDE }), "attention");
  assert.equal(homeReadPhase({ status: "ready", payload: clearBook(), now: INSIDE }), "empty");
  assert.equal(homeReadPhase({ status: "ready", payload: payload(), now: OUTSIDE }), "stale");
  assert.equal(homeReadPhase({ status: "error", payload: null }), "unavailable");
  assert.equal(homeReadPhase({ status: "unauthorized", payload: null }), "unauthorized");
});

test("a late response cannot repaint over a newer read", () => {
  assert.equal(acceptsResponse(3, 3), true);
  assert.equal(acceptsResponse(3, 2), false);
  assert.equal(acceptsResponse(3, 4), false);
  assert.equal(acceptsResponse(3, undefined), false);
  assert.equal(acceptsResponse(undefined, 3), false);
});

test("Home uses human source labels", () => {
  assert.equal(humanSourceLabel("command_center"), "Command Center read");
  assert.equal(humanSourceLabel("v_deal_room_board"), "Deal Room board");
  assert.equal(humanSourceLabel("ops.work_request"), "System work");
  assert.equal(humanSourceLabel("unknown_machine_name"), "Source unavailable");
});
