import test from "node:test";
import assert from "node:assert/strict";
import { readCommandCenterSummary } from "../src/workspace-command-center.js";

const DEALS = { team_active: "5", team_flagged: "2", mine_active: "2", mine_flagged: "1" };
const WORK = { needs_viewer: "1", doc_at_work: "2", changed_count: "3", changed_at: "2026-08-24T14:30:00.000Z", legacy_unscoped_held: "0", legacy_unscoped_recent: "0" };
const clientFor = (deals = {}, work = {}) => ({ query: async (sql) => ({ rows: [sql.includes("v_deal_room_board") ? { ...DEALS, ...deals } : { ...WORK, ...work }] }) });
const TEAM_FLAGGED = "/deals?workspace=team&filter=flagged";
const MY_FLAGGED = "/deals?workspace=team&filter=flagged&owner=me";

test("Command Center returns the contract-shaped team and mine aggregate read with canonical freshness", async () => {
  const queries = [];
  const client = { async query(sql, params) {
    queries.push({ sql, params });
    return { rows: [sql.includes("v_deal_room_board") ? DEALS : WORK] };
  } };
  const result = await readCommandCenterSummary({ client, actor: { slug: "joe" }, tenant: "carr-internal", correlationId: "corr-joe", now: () => new Date("2026-08-24T15:00:00.000Z") });
  assert.deepEqual(Object.keys(result).sort(), ["doc_at_work", "metrics", "needs_you_now", "this_week", "recent_calls", "recent_activity", "source", "viewer"].sort());
  assert.equal(result.viewer, "joe");
  assert.deepEqual(result.metrics.map((metric) => metric.scope), ["team", "mine"]);
  assert.equal(result.metrics[0].active_deals, 5);
  assert.equal(result.metrics[0].flagged_deals, 2);
  assert.equal(result.metrics[1].active_deals, 2);
  assert.equal(result.metrics[1].flagged_deals, 1);
  // Only links the Deal Room boot parser already honors; "mine, active" has no URL form.
  assert.equal(result.metrics[0].active_destination, "/deals?workspace=team");
  assert.equal(result.metrics[0].flagged_destination, TEAM_FLAGGED);
  assert.equal(result.metrics[1].active_destination, null);
  assert.equal(result.metrics[1].flagged_destination, MY_FLAGGED);
  assert.equal(result.source.source, "command_center");
  assert.equal(result.source.freshness, "fresh");
  assert.match(result.source.safe_explanation, /no-store request-time canonical/i);
  assert.equal(result.source.correlation_id, "corr-joe");
  assert.equal(result.source.valid_until, "2026-08-24T15:01:00.000Z");
  assert.equal(queries.length, 2);
  assert.equal(queries[0].params[0], "joe");
  assert.equal((queries[0].sql.match(/count\(\*\) filter/g) || []).length, 4);
  assert.match(queries[0].sql, /as team_active[\s\S]*as team_flagged[\s\S]*as mine_active[\s\S]*as mine_flagged/);
  assert.doesNotMatch(queries[0].sql, /select[\s\S]*\bnext_step\b|select[\s\S]*\bclient_name\b/i);
  assert.equal(queries[1].params[0], "carr-internal");
  assert.equal(queries[1].params[1], "needs_joe");
  assert.match(queries[1].sql, /awaiting_release/);
  assert.match(queries[1].sql, /released/);
  assert.match(queries[1].sql, /updated_at\s*>=\s*now\(\)\s*-\s*interval '7 days'/);
  assert.doesNotMatch(queries[1].sql, /select\s+.*title|select\s+.*notes/i);
});

test("team counts are owner-independent and mine counts are bound to the authenticated owner", async () => {
  const queries = [];
  const client = { async query(sql, params) {
    queries.push({ sql, params });
    return { rows: [sql.includes("v_deal_room_board") ? DEALS : WORK] };
  } };
  await readCommandCenterSummary({ client, actor: { slug: "dell" }, correlationId: "corr-dell" });
  const [teamActive, teamFlagged, mineActive, mineFlagged] = queries[0].sql.split("count(*) filter").slice(1);
  assert.doesNotMatch(teamActive, /owner = \$1/);
  assert.doesNotMatch(teamFlagged, /owner = \$1/);
  assert.match(mineActive, /owner = \$1::text/);
  assert.match(mineFlagged, /owner = \$1::text/);
  assert.equal(queries[0].params[0], "dell");
});

test("Command Center fails closed when the actor is outside the bound CARR tenant", async () => {
  await assert.rejects(() => readCommandCenterSummary({ client: { query: async () => ({ rows: [] }) }, actor: { slug: "joe" }, tenant: "other-tenant", correlationId: "corr" }), /TENANT_SCOPE_REFUSED/);
});

test("Command Center refuses an actor outside the two authenticated partners", async () => {
  for (const actor of [{ slug: "stranger" }, { slug: "" }, {}, null]) {
    await assert.rejects(() => readCommandCenterSummary({ client: clientFor(), actor, tenant: "carr-internal", correlationId: "corr" }), /TENANT_SCOPE_REFUSED|AUTHORIZATION_REFUSED/);
  }
});

test("needs_you_now separates team-flagged from my-flagged and stays bound to the viewer", async () => {
  const dell = await readCommandCenterSummary({ client: clientFor(), actor: { slug: "dell" }, correlationId: "corr-dell" });
  assert.deepEqual(dell.needs_you_now, [
    { kind: "team_flagged_deals", scope: "team", count: 2, destination: TEAM_FLAGGED },
    { kind: "my_flagged_deals", scope: "mine", count: 1, destination: MY_FLAGGED },
  ]);
  const joe = await readCommandCenterSummary({ client: clientFor(), actor: { slug: "joe" }, correlationId: "corr-joe" });
  assert.deepEqual(joe.needs_you_now[2], { kind: "needs_joe_work", scope: "mine", count: 1, destination: "/system-work.html" });
});

test("a viewer with nothing of their own still reports the team's flagged deals", async () => {
  const result = await readCommandCenterSummary({ client: clientFor({ mine_active: "0", mine_flagged: "0" }), actor: { slug: "dell" }, correlationId: "corr-empty-mine" });
  assert.equal(result.metrics[1].flagged_deals, 0);
  assert.equal(result.metrics[0].flagged_deals, 2);
  assert.equal(result.needs_you_now.find((item) => item.kind === "team_flagged_deals").count, 2);
});

test("an entirely clear team book reports zero in both scopes", async () => {
  const result = await readCommandCenterSummary({ client: clientFor({ team_flagged: "0", mine_flagged: "0" }), actor: { slug: "joe" }, correlationId: "corr-clear" });
  assert.equal(result.metrics[0].flagged_deals, 0);
  assert.equal(result.metrics[1].flagged_deals, 0);
  assert.equal(result.metrics[0].active_deals, 5);
});

test("malformed or impossible deal counts fail closed instead of publishing a fresh aggregate", async () => {
  const malformed = [
    { team_active: null }, { team_flagged: "not-a-number" }, { mine_active: "-1" }, { mine_flagged: "1.5" },
    { team_flagged: "6" }, // flagged above active
    { mine_active: "9" }, // mine above team
    { mine_flagged: "3", mine_active: "4" }, // mine flagged above team flagged
  ];
  for (const deals of malformed) {
    await assert.rejects(() => readCommandCenterSummary({ client: clientFor(deals), actor: { slug: "joe" }, correlationId: "corr-malformed" }), /FRESHNESS_UNKNOWN/);
  }
});

test("a relevant tenant-null row withholds work cards but keeps deal metrics available", async () => {
  const result = await readCommandCenterSummary({ client: clientFor({}, { legacy_unscoped_held: "1" }), actor: { slug: "joe" }, correlationId: "corr-legacy" });
  assert.equal(result.metrics[0].active_deals, 5);
  assert.equal(result.doc_at_work[0].state, "unavailable");
  assert.equal(result.recent_activity[0].state, "unavailable");
  assert.deepEqual(result.needs_you_now.map((item) => item.kind), ["team_flagged_deals", "my_flagged_deals"]);
});

test("ordinary historical unscoped work outside the held and seven-day windows does not make the aggregate stale", async () => {
  const result = await readCommandCenterSummary({ client: clientFor(), actor: { slug: "joe" }, correlationId: "corr-ordinary" });
  assert.equal(result.doc_at_work[0].source.freshness, "fresh");
  assert.equal(result.recent_activity[0].source.freshness, "fresh");
});

test("correlation id is required and read failures stay typed", async () => {
  await assert.rejects(() => readCommandCenterSummary({ client: clientFor(), actor: { slug: "joe" } }), /INTERNAL_ERROR/);
  await assert.rejects(() => readCommandCenterSummary({ client: { query: async () => { throw Object.assign(new Error("db down"), { code: "DEPENDENCY_UNAVAILABLE" }); } }, actor: { slug: "joe" }, correlationId: "corr" }), /DEPENDENCY_UNAVAILABLE/);
  await assert.rejects(() => readCommandCenterSummary({ client: { query: async () => { throw new Error("unexpected"); } }, actor: { slug: "joe" }, correlationId: "corr" }), /INTERNAL_ERROR/);
});
