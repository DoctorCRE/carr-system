import { NOW } from "./helpers/fixed-clock.mjs";
// morning-brief.test.mjs — safe record-native morning delivery.
//
// This is deliberately a fake-client suite.  It proves the handler's source
// and authority contract without a Worker, credential, or database connection.

import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool } from "../src/tools.js";
import { readCallCountSQL } from "../src/mcp.js";

const JOE = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "fixture" };
const DELL = { slug: "dell", display: "Dell", human: true, via: "oauth-google", client_id: "fixture" };
const UNSPONSORED = { slug: "codex", display: "Codex", human: false, via: "agent-token" };

function costFixture() {
  const yesterday = new Date(NOW - 86400000).toISOString().slice(0, 10);
  return { schema: "carr-system-costs.v1", observed_at: new Date(NOW).toISOString(),
    month: yesterday.slice(0, 7), through: yesterday, state: "ready",
    providers: [{ provider: "github", label: "GitHub", plan: "Usage", mtd_usd: 2.5,
      projection_usd: 10, budget_usd: 50, state: "ready", reason: null,
      daily: [{ day: yesterday, usd: 2.5, drivers: { "actions": 2.5 } }] }],
    months: [{ month: yesterday.slice(0, 7), usd: 2.5, providers: { github: 2.5 } }],
    alerts: [], action: "Finance Ops: inspect provider and driver; verify the next complete day; auto-clear when thresholds pass." };
}

function client({ unavailable = null, accounts = [], renewalState = "ready", renewalRows = null, costs = costFixture(), todayRows = null, dealRows = null, governanceQueue = null, localUpdatedAt = new Date(NOW).toISOString(), calendar = null, calendarContacts = [], calendarDeals = [], meetingPrep = [] } = {}) {
  const queries = [];
  return {
    queries,
    query: async (sql, params = []) => {
      queries.push({ sql, params });
      if (/^(savepoint|rollback to savepoint|release savepoint) calendar_contacts/.test(sql)) return { rows: [] };
      if (unavailable && sql.includes(unavailable)) throw new Error("fixture source unavailable");
      if (sql.includes("calendar_deal_matches")) return { rows: calendarDeals };
      if (sql.includes("/* calendar_meeting_prep */")) return { rows: meetingPrep };
      if (sql.includes("from v_today_triage")) return { rows: todayRows || [
        { item_kind: "next_action", owner: "joe", what: "Call the client", subject_name: "Example Practice" },
      ] };
      if (sql.includes("from v_claim_card") && sql.includes("count(*)")) return { rows: [
        { claimable: 3, needs_contact_count: 1 },
      ] };
      if (sql.includes("from v_claim_card")) return { rows: [
        { display_name: "Candidate Safe", city: "Mobile", has_channel: true, pool_id: "opaque-handler-id" },
      ] };
      if (sql.includes("dealroom:closed-deals")) return { rows: [] };
      if (sql.includes("from v_open_deal_room_board")) return { rows: dealRows || [
        { name: "Example Deal", owner: "joe", attention: true },
      ] };
      if (sql.includes("from v_deal_room_account")) return { rows: accounts };
      if (sql.includes("from v_deal_room_session")) return { rows: [] };
      if (sql.includes("from loop_item")) return { rows: [
        { number: 7, owner: "joe", label: "Resolve the lease item" },
        { number: 8, owner: "dell", label: "Chase the broker packet" },
      ].filter((row) => params.some((value) => String(value).toLowerCase() === row.owner)) };
      if (sql.includes("from v_renewal_decision_queue_status")) return { rows: [
        { t1_candidate_count: renewalState === "empty" ? 0 : 1, source_observed_at: "2026-08-21T06:00:00Z", freshness_state: renewalState },
      ] };
      if (sql.includes("from v_renewal_decision_queue")) return { rows: renewalRows || [
        { display_name: "Renewal Safe", city: "Pensacola", est_lease_event: "2027-01-01",
          tier_status: "t1", flag_status: "clear", has_channel: true },
      ] };
      if (sql.includes("from v_export_clients") && sql.includes("calendar_contacts")) return { rows: calendarContacts };
      if (sql.includes("from board_snapshot") && params[2] === "calendar-agenda")
        return { rows: calendar === null ? [] : [{ snapshot_json: calendar }] };
      if (sql.includes("from board_snapshot") && params[2] === "system-costs")
        return { rows: costs === null ? [] : [{ snapshot_json: { costs } }] };
      if (sql.includes("ops.v5_a05_assurance_cadence_batch")) return { rows: [{ batch: [] }] };
      if (sql.includes("read_governance_queue")) return { rows: [{ queue: governanceQueue || { pending_rule_approvals: [
        { rule_id: "rule-1", statement: "Name the deal in every question box.", admitted_at: "2026-10-01T00:00:00Z" },
      ] } }] };
      if (sql.includes("from ops.work_request")) return { rows: [] };
      if (sql.includes("from board_question")) return { rows: [] };
      if (sql.includes("from board_snapshot")) return { rows: [
        { snapshot_json: { schema: "needs-joe-local.v1", items: [] }, updated_at: localUpdatedAt },
      ] };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
}

test("morning-brief derives Joe's sponsor from authenticated context and composes only record views", async () => {
  const c = client();
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  assert.equal(result.state, "ready");
  assert.equal(result.sponsor, "joe");
  assert.deepEqual(Object.keys(result.sections).sort(),
    ["needs_joe", "claim_card", "deals", "renewals", "today", "loops", "assurance_cadence", "costs"].sort());
  assert.equal(Object.keys(result.sections)[0], "needs_joe", "the list of what waits on Joe leads the brief");
  assert.equal(result.sections.renewals.state, "ready");
  assert.equal(result.sections.renewals.items[0].display_name, "Renewal Safe");
  const sql = c.queries.map(({ sql }) => sql).join("\n");
  assert.match(sql, /v_today_triage/);
  assert.match(sql, /v_claim_card/);
  assert.match(sql, /v_open_deal_room_board/);
  assert.match(sql, /loop_item/);
  assert.match(sql, /v_renewal_decision_queue/);
  const renewalQueries = c.queries.filter(({ sql }) => sql.includes("v_renewal_decision_queue"));
  assert.ok(renewalQueries.length >= 2);
  assert.ok(renewalQueries.every(({ params }) => params[0] === "joe"));
  assert.doesNotMatch(sql, /candidate_pool|v_export_pool|source_row|\bemail\b|\bphone\b|\baddress\b/i);
});

test("morning-brief derives Dell separately; no caller field may select an audience or sponsor", async () => {
  const c = client();
  const dell = await executeRegisteredTool(c, DELL, "morning-brief", {});
  assert.equal(dell.sponsor, "dell");
  assert.equal(dell.sections.needs_joe, undefined, "Joe's desk is not Dell's brief");
  for (const args of [{ sponsor: "joe" }, { audience: "joe" }, { partner: "joe" }]) {
    await assert.rejects(
      executeRegisteredTool(client(), DELL, "morning-brief", args),
      (error) => error instanceof ToolError,
    );
  }
});

test("morning-brief filters accounts by authenticated sponsor rather than exposing a shared deal-room account list", async () => {
  const accounts = [
    { account_name: "Joe Account", account_owner: "joe" },
    { account_name: "Dell Account", account_owner: "dell" },
    { account_name: "Unowned Account", account_owner: null },
  ];
  const joe = await executeRegisteredTool(client({ accounts }), JOE, "morning-brief", {});
  const dell = await executeRegisteredTool(client({ accounts }), DELL, "morning-brief", {});
  assert.deepEqual(joe.sections.deals.accounts.map((row) => row.account_name), ["Joe Account"]);
  assert.deepEqual(dell.sections.deals.accounts.map((row) => row.account_name), ["Dell Account"]);
  assert.doesNotMatch(JSON.stringify(joe), /Dell Account/);
  assert.doesNotMatch(JSON.stringify(dell), /Joe Account/);
  assert.doesNotMatch(JSON.stringify(joe), /Unowned Account/);
  assert.doesNotMatch(JSON.stringify(dell), /Unowned Account/);
});

const BOTH_TODOS = [
  { item_kind: "next_action", owner: "joe", what: "Joe's own call" },
  { item_kind: "next_action", owner: "joe-local", what: "Reply set from Joe's machine seat" },
  { item_kind: "post_call_action", owner: "dell", what: "Dell's follow-up" },
  { item_kind: "next_action", owner: "dell-local", what: "Dell's machine seat ball" },
  { item_kind: "next_action", owner: "claude", what: "The drain queue's ball" },
  { item_kind: "next_action", owner: "hermes-pilot", what: "The pilot's ball" },
  { item_kind: "next_action", owner: "constructor", what: "An unknown seat ball" },
  { item_kind: "critical_date", owner: null, what: "msa: renewal notice" },
];
const view = (result) => result.sections.today.items.map((row) => [row.what, row.owner_partner]);

test("Joe sees Dell's to-dos and Dell sees Joe's, each row labeled with its partner", async () => {
  const expected = [
    ["Joe's own call", "joe"], ["Reply set from Joe's machine seat", "joe"],
    ["Dell's follow-up", "dell"], ["Dell's machine seat ball", "dell"],
    ["msa: renewal notice", null],
  ];
  const joe = await executeRegisteredTool(client({ todayRows: BOTH_TODOS }), JOE, "morning-brief", {});
  const dell = await executeRegisteredTool(client({ todayRows: BOTH_TODOS }), DELL, "morning-brief", {});
  assert.deepEqual(view(joe), expected);
  assert.deepEqual(view(dell), expected);
});

test("to-dos owned by claude or any other identity stay out of the brief", async () => {
  const joe = await executeRegisteredTool(client({ todayRows: BOTH_TODOS }), JOE, "morning-brief", {});
  assert.doesNotMatch(JSON.stringify(joe.sections.today), /drain queue|pilot/);
});

test("both partners' open loops show, the caller's first, each labeled", async () => {
  const joe = await executeRegisteredTool(client(), JOE, "morning-brief", {});
  const dell = await executeRegisteredTool(client(), DELL, "morning-brief", {});
  assert.deepEqual(joe.sections.loops.items.map((row) => [row.number, row.owner_partner]), [[7, "joe"], [8, "dell"]]);
  assert.deepEqual(dell.sections.loops.items.map((row) => [row.number, row.owner_partner]), [[8, "dell"], [7, "joe"]]);
});

test("machine-seat to-dos are shared across item kinds while deals stay sponsor-scoped", async () => {
  const todayRows = [
    { item_kind: "next_action", owner: "joe-local", what: "Reply about the suite tour" },
    { item_kind: "post_call_action", owner: "joe-local", what: "A post-call action from that seat" },
    { item_kind: "next_action", owner: "dell-local", what: "Dell's machine ball" },
    { item_kind: "next_action", owner: "claude", what: "An agent's own ball" },
  ];
  const dealRows = [
    { name: "Example Deal", owner: "joe", attention: true },
    { name: "Seat Deal", owner: "joe-local", attention: true },
  ];
  const joe = await executeRegisteredTool(client({ todayRows, dealRows }), JOE, "morning-brief", {});
  const dell = await executeRegisteredTool(client({ todayRows, dealRows }), DELL, "morning-brief", {});
  const expected = [
    ["Reply about the suite tour", "joe"],
    ["A post-call action from that seat", "joe"],
    ["Dell's machine ball", "dell"],
  ];
  assert.deepEqual(view(joe), expected);
  assert.deepEqual(view(dell), expected);
  assert.deepEqual(joe.sections.deals.items.map((row) => row.name), ["Example Deal"], "deals are not widened");
});

test("Joe's brief leads with the needs-Joe list, read through governance-queue", async () => {
  const result = await executeRegisteredTool(client(), JOE, "morning-brief", {});
  const section = result.sections.needs_joe;
  assert.equal(section.state, "ready");
  assert.equal(section.schema, "needs-joe.v1");
  assert.ok(section.items.some((item) => item.title.includes("Name the deal in every question box")));
  assert.ok(section.items.every((item) => item.why && item.action && item.blocks));
  assert.equal(typeof section.excluded.count, "number");
});

test("a needs-Joe list with a failed source and no items is unavailable, never empty", async () => {
  const result = await executeRegisteredTool(client({ unavailable: "read_governance_queue" }), JOE, "morning-brief", {});
  assert.equal(result.sections.needs_joe.state, "unavailable");
  assert.equal(result.state, "unavailable");
});

test("a surviving Needs Joe decision does not hide a failed source", async () => {
  const result = await executeRegisteredTool(client({ unavailable: "from ops.work_request" }), JOE, "morning-brief", {});
  assert.equal(result.sections.needs_joe.items.length, 1);
  assert.equal(result.sections.needs_joe.sources.work_requests.state, "unavailable");
  assert.equal(result.sections.needs_joe.state, "partial");
  assert.equal(result.state, "partial");
  assert.doesNotMatch(JSON.stringify(result), /fixture source unavailable/);
});

test("an empty partial Needs Joe list remains unavailable and names the failed source", async () => {
  const result = await executeRegisteredTool(client({
    unavailable: "from ops.work_request", governanceQueue: {},
  }), JOE, "morning-brief", {});
  assert.equal(result.sections.needs_joe.state, "unavailable");
  assert.deepEqual(result.sections.needs_joe.items, []);
  assert.equal(result.sections.needs_joe.sources.work_requests.state, "unavailable");
  assert.equal(result.state, "unavailable");
  assert.doesNotMatch(JSON.stringify(result), /fixture source unavailable/);
});

test("stale local Needs Joe evidence stays partial in the section and coverage", async () => {
  const result = await executeRegisteredTool(client({
    localUpdatedAt: new Date(NOW - 3 * 3600000).toISOString(),
  }), JOE, "morning-brief", {});
  assert.equal(result.sections.needs_joe.state, "partial");
  assert.equal(result.state, "partial");
  assert.deepEqual(result.coverage.required_sections[0].incomplete_sources, [{ key: "local", state: "stale" }]);
});

test("coverage names every required section and counts returned work beyond priority cards", async () => {
  const result = await executeRegisteredTool(client({ accounts: [
    { account_name: "Example Account", account_owner: "joe" },
  ] }), JOE, "morning-brief", {});
  assert.equal(result.coverage.schema, "morning-brief-coverage.v1");
  assert.equal(result.coverage.count_scope, "returned_rows_only");
  assert.deepEqual(result.coverage.required_sections.map(({ key }) => key), [
    "needs_joe", "today", "claim_card", "deals", "loops", "renewals", "assurance_cadence", "costs",
  ]);
  const entries = Object.fromEntries(result.coverage.required_sections.map(entry => [entry.key, entry]));
  assert.equal(entries.needs_joe.state, "ready");
  assert.equal(entries.needs_joe.counts.items, 1);
  assert.equal(entries.deals.counts.accounts, 1);
  assert.equal(entries.costs.counts.providers, 1);
  assert.equal(entries.assurance_cadence.state, "empty");
  assert.equal(entries.assurance_cadence.counts.items, 0);
  for (const entry of result.coverage.required_sections) {
    const section = entry.path.split("/").slice(1).reduce((value, key) => value[key], result);
    assert.equal(section, result.sections[entry.key], "coverage points to the returned full section");
  }
  const dell = await executeRegisteredTool(client(), DELL, "morning-brief", {});
  assert.equal(dell.coverage.required_sections.some(({ key }) => key === "needs_joe"), false);
});

test("an account-only deal section is ready rather than empty", async () => {
  const result = await executeRegisteredTool(client({ dealRows: [], accounts: [
    { account_name: "Example Account", account_owner: "joe" },
  ] }), JOE, "morning-brief", {});
  assert.equal(result.sections.deals.state, "ready");
  const entry = result.coverage.required_sections.find(({ key }) => key === "deals");
  assert.equal(entry.state, "ready");
  assert.deepEqual(entry.counts, { items: 0, accounts: 1 });
});

test("coverage discloses partial and unavailable sections without claiming zero work", async () => {
  const c = client({ unavailable: "from ops.work_request", renewalState: "stale" });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  const entries = Object.fromEntries(result.coverage.required_sections.map(entry => [entry.key, entry]));
  assert.equal(entries.needs_joe.state, "partial");
  assert.deepEqual(entries.needs_joe.incomplete_sources, [{ key: "work_requests", state: "unavailable" }]);
  assert.equal(entries.renewals.state, "unavailable");
  assert.equal(entries.renewals.counts.items, null);
  assert.equal(result.state, "unavailable");
  assert.doesNotMatch(JSON.stringify(result), /fixture source unavailable/);
});

test("every shared triage kind carries its own subject when Joe's deal map excludes Dell's deal", async () => {
  const todayRows = [
    { item_kind: "next_action", id: "action-1", owner: "dell", subject_type: "client", subject_id: "client-1", subject_ref: "client:demo", subject_name: "Demo Practice", what: "Call Demo Practice", due_on: "2026-10-23" },
    { item_kind: "post_call_action", id: "post-call-1", owner: "dell", subject_type: "deal", subject_id: "deal-1", subject_ref: "deal:demo", subject_name: "Demo Lease", what: "Confirm the tour", due_on: "2026-10-23" },
    { item_kind: "critical_date", id: "date-1", owner: null, subject_type: "deal", subject_id: "deal-1", subject_ref: "deal:demo", subject_name: "Demo Lease", what: "Notice deadline", due_on: "2026-11-01" },
    { item_kind: "ingest", id: "inbox-1", owner: null, subject_type: "inbox", subject_id: "inbox-1", subject_ref: null, subject_name: null, what: "Demo item awaiting triage", due_on: "2026-10-23" },
  ];
  const result = await executeRegisteredTool(client({ todayRows, dealRows: [
    { id: "deal-1", name: "Private Dell Deal", owner: "dell" },
  ] }), JOE, "morning-brief", {});
  assert.deepEqual(result.sections.deals.items, []);
  assert.deepEqual(result.sections.today.items, todayRows.map(row => ({ ...row, owner_partner: row.owner })));
  assert.deepEqual(result.sections.today.item_kinds, [
    { kind: "next_action", label: "Next actions", count: 1 },
    { kind: "post_call_action", label: "Post-call actions", count: 1 },
    { kind: "critical_date", label: "Critical dates including upcoming deadlines", count: 1 },
    { kind: "ingest", label: "Inbox awaiting triage", count: 1 },
  ]);
  assert.doesNotMatch(JSON.stringify(result), /Private Dell Deal/);
});

test("morning-brief refuses an unsponsored runtime rather than selecting a shared or caller-supplied brain", async () => {
  await assert.rejects(
    executeRegisteredTool(client(), UNSPONSORED, "morning-brief", {}),
    (error) => error instanceof ToolError && error.payload.error === "morning_brief_requires_partner_scope",
  );
});

test("a missing required source is unavailable, never silently rendered as an empty brief", async () => {
  const c = client({ unavailable: "v_claim_card" });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  assert.equal(result.state, "unavailable");
  assert.equal(result.sections.claim_card.state, "unavailable");
  assert.equal(result.sections.claim_card.reason, "source_unavailable");
  assert.notEqual(result.sections.claim_card.items, undefined);
  assert.equal(result.sections.today.state, "ready");
  assert.doesNotMatch(JSON.stringify(result), /fixture source unavailable/);
});

test("a sealed true-zero renewal source is explicit empty, never unavailable or fabricated rows", async () => {
  const result = await executeRegisteredTool(client({ renewalState: "empty", renewalRows: [] }), JOE, "morning-brief", {});
  assert.equal(result.state, "ready");
  assert.equal(result.sections.renewals.state, "empty");
  assert.equal(result.sections.renewals.t1_candidate_count, 0);
  assert.deepEqual(result.sections.renewals.items, []);
});

test("the read counter remains metadata-only for the composite response", () => {
  const output = { private_body: "Candidate Safe", sections: { renewals: { items: [{ display_name: "Renewal Safe" }] } } };
  const receipt = readCallCountSQL(JOE, "morning-brief", true);
  assert.doesNotMatch(receipt.text, /response|argument|body|\$6/i);
  assert.doesNotMatch(JSON.stringify(receipt.params), /Candidate Safe|Renewal Safe|private_body/);
  assert.equal(output.sections.renewals.items.length, 1); // Guard against a vacuous metadata-only test.
});


test("morning costs preserves provider and daily evidence using authenticated board scope", async () => {
  const costs = costFixture();
  costs.billing_response = { card_number: "never-return-fixture" };
  costs.providers[0].raw_response = { account_number: "never-return-fixture" };
  const c = client({ costs });
  const result = await executeRegisteredTool(c, DELL, "morning-brief", {});
  const value = result.sections.costs;
  assert.equal(value.state, "ready");
  assert.equal(value.providers[0].mtd_usd, 2.5);
  assert.equal(Object.hasOwn(value.providers[0], "call_sites"), false);
  assert.deepEqual(value.providers[0].daily, costs.providers[0].daily);
  assert.equal(value.months[0].usd, 2.5);
  assert.doesNotMatch(JSON.stringify(value), /never-return-fixture|billing_response|raw_response/);
  const query = c.queries.find(({ sql }) => sql.includes("from board_snapshot"));
  assert.match(query.sql, /organization_tenant_id=\$1.*sponsoring_human_slug=\$2.*board_id=\$3/s);
  assert.deepEqual(query.params, ["carr-internal", "dell", "system-costs"]);
});


test("partial cost coverage remains partial with unavailable providers visible", async () => {
  const costs = costFixture();
  costs.state = "partial";
  costs.providers.push({ provider: "neon", label: "Neon", plan: "Usage", state: "unavailable",
    reason: "missing NEON_API_KEY", mtd_usd: null, projection_usd: null, budget_usd: 50,
    daily: [] });
  const result = await executeRegisteredTool(client({ costs }), JOE, "morning-brief", {});
  assert.equal(result.state, "partial");
  assert.equal(result.sections.costs.state, "partial");
  assert.equal(result.sections.costs.providers[1].mtd_usd, null);
  assert.equal(result.sections.costs.providers[1].reason, "missing NEON_API_KEY");
});

test("missing, stale, future, and malformed cost evidence is unavailable with a bound recovery action", async () => {
  const stale = costFixture();
  stale.observed_at = new Date(NOW - 37 * 3600000).toISOString();
  const future = costFixture();
  future.observed_at = new Date(NOW + 3600000).toISOString();
  const malformed = costFixture();
  malformed.providers[0].mtd_usd = -1;
  const raw = { schema: "carr-system-costs.v1", card_number: "never-return-fixture" };
  for (const costs of [null, stale, future, malformed, raw]) {
    const result = await executeRegisteredTool(client({ costs }), JOE, "morning-brief", {});
    assert.equal(result.sections.costs.state, "unavailable");
    assert.deepEqual(result.sections.costs.providers, []);
    assert.match(result.sections.costs.action, /owner orchestrator.*verify.*auto-clear/);
    assert.doesNotMatch(JSON.stringify(result.sections.costs), /never-return-fixture/);
  }
});

test("a failed cost board read never exposes driver error details", async () => {
  const result = await executeRegisteredTool(client({ unavailable: "from board_snapshot" }), JOE, "morning-brief", {});
  assert.equal(result.sections.costs.state, "unavailable");
  assert.match(result.sections.costs.action, /owner orchestrator.*verify.*auto-clear/);
  assert.doesNotMatch(JSON.stringify(result), /fixture source unavailable/);
});

function calendarFixture(now = NOW, observedAt = new Date(now).toISOString(), warnings = []) {
  const at = (hours) => new Date(now + hours * 3600000).toISOString();
  return { schema: "carr-calendar-agenda.v1", source: "outlook_hxstore", lifecycle: "unverified",
    observed_at: observedAt, warnings, items: [
      { start: at(-30), end: at(-29), title: "Synthetic finished review", attendees: [] },
      { start: at(26), end: at(27), title: "Synthetic site tour", location: "Example Suite 100",
        organizer: { name: "Ada Example", email: "Ada@Example.test" },
        attendees: [{ name: "Bo Example", email: "bo@example.test" }], attendee_count: 1,
        time_changed: true, times: [at(26), at(30)] },
      { start: at(2), end: at(3), title: "Synthetic lender call", location: null, attendees: [], recurring: true },
      { start: at(24 * 8), end: at(24 * 8 + 1), title: "Synthetic next-week meeting", attendees: [] },
    ] };
}

test("morning-brief shows the partner's own published calendar for the next seven days, soonest first", async () => {
  const c = client({ calendar: calendarFixture() });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  const calendar = result.sections.calendar;
  assert.equal(result.state, "ready");
  assert.equal(calendar.state, "ready", "no known gap, fresh");
  assert.deepEqual(calendar.notes, []);
  assert.deepEqual(calendar.items.map((row) => row.title), ["Synthetic lender call", "Synthetic site tour"]);
  assert.equal(calendar.items[0].recurring, true);
  assert.deepEqual(calendar.items[1].organizer, { name: "Ada Example", email: "ada@example.test" });
  assert.deepEqual(calendar.items[1].attendees, [{ name: "Bo Example", email: "bo@example.test" }]);
  assert.equal(calendar.items[1].location, "Example Suite 100");
  assert.equal(calendar.items[1].time_changed, true);
  assert.equal(calendar.items[1].times.length, 2);
  const read = c.queries.find(({ params }) => params[2] === "calendar-agenda");
  assert.deepEqual(read.params.slice(1), ["joe", "calendar-agenda"], "sponsor comes from authentication");
  assert.ok(result.coverage.required_sections.some((row) => row.key === "calendar" && row.counts.items === 2));
});

test("morning-brief flags overlapping timed meetings without treating all-day entries as conflicts", async () => {
  const now = NOW;
  const at = hours => new Date(now + hours * 3600000).toISOString();
  const calendar = calendarFixture(now);
  calendar.items = [
    { start: at(1), end: at(3), title: "Synthetic first" },
    { start: at(2), end: at(4), title: "Synthetic overlapping" },
    { start: at(4), end: at(5), title: "Synthetic adjacent" },
    { start: at(0), end: at(24), title: "Synthetic all day", all_day: true },
  ];
  const result = await executeRegisteredTool(client({ calendar }), JOE, "morning-brief", {});
  const overlaps = Object.fromEntries(result.sections.calendar.items.map(item => [item.title, item.schedule_conflict]));
  assert.deepEqual(overlaps, { "Synthetic all day": false, "Synthetic first": true,
    "Synthetic overlapping": true, "Synthetic adjacent": false });
});

test("morning-brief offers recent meeting outcomes for review without creating records", async () => {
  const now = NOW;
  const at = hours => new Date(now + hours * 3600000).toISOString();
  const calendar = calendarFixture(now);
  calendar.items = [];
  calendar.follow_up_reviews = [{ key: "0000000000000000", title: "Synthetic outcome review",
    start: at(-2), end: at(-1), state: "needs_review",
    prompt: "Confirm whether this meeting happened. If it did, record the outcome and decide the next step." }];
  const c = client({ calendar });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  const [review] = result.sections.calendar.follow_up_reviews;
  assert.equal(review.title, "Synthetic outcome review");
  assert.equal(review.state, "needs_review");
  assert.match(review.prompt, /Confirm whether this meeting happened/);
  assert.equal(result.sections.calendar.state, "partial", "scheduled end does not verify attendance");
  assert.ok(result.sections.calendar.notes.some(note => /not confirmed attendance/.test(note)));
  assert.equal(result.coverage.required_sections.find(row => row.key === "calendar").counts.follow_up_reviews, 1);
  assert.ok(c.queries.every(({ sql }) => !/\b(insert|update|delete)\b/i.test(sql)));
});

test("meeting outcome reviews expire and stale or malformed snapshots never become confirmed attendance", async () => {
  const now = NOW;
  const at = hours => new Date(now + hours * 3600000).toISOString();
  const review = { key: "0000000000000000", title: "Synthetic review", start: at(-2), end: at(-1),
    state: "needs_review", prompt: "Confirm whether this meeting happened." };
  for (const observed of [at(-19), at(1)]) {
    const calendar = calendarFixture(now, observed);
    calendar.follow_up_reviews = [review];
    const result = await executeRegisteredTool(client({ calendar }), JOE, "morning-brief", {});
    assert.deepEqual(result.sections.calendar.follow_up_reviews, []);
    assert.equal(result.sections.calendar.state, "partial");
  }
  const calendar = calendarFixture(now);
  calendar.follow_up_reviews = [{ ...review, start: at(-26), end: at(-24) },
    { ...review, start: at(1), end: at(2) }, { ...review, end: "invalid" },
    { ...review, state: "completed" }, review, { ...review, key: 1234567890123456 }];
  const result = await executeRegisteredTool(client({ calendar }), JOE, "morning-brief", {});
  assert.equal(result.sections.calendar.follow_up_reviews.length, 1);
  assert.ok(result.sections.calendar.notes.some(note => /3 stored meeting outcome reviews could not be shown/.test(note)));
  calendar.follow_up_reviews = {};
  const malformed = await executeRegisteredTool(client({ calendar }), JOE, "morning-brief", {});
  assert.deepEqual(malformed.sections.calendar.follow_up_reviews, []);
  assert.equal(malformed.sections.calendar.state, "partial");
});

test("every known gap becomes a plain sentence and the section is partial, never ready", async () => {
  const warnings = [{ code: "responses_unverified" }, { code: "group_invites_not_shown" },
    { code: "undecoded", count: 3 }, { code: "time_changed", count: 1 }, { code: "recurring_unverified", count: 2 },
    { code: "follow_up_trimmed", count: 2 }, { code: "trimmed", count: 4 },
    { code: "canceled_hidden", count: 2 }, { code: "something_new" }];
  const result = await executeRegisteredTool(client({ calendar: calendarFixture(NOW, undefined, warnings) }), JOE, "morning-brief", {});
  const calendar = result.sections.calendar;
  assert.equal(calendar.state, "partial");
  assert.equal(result.state, "partial");
  assert.deepEqual(calendar.notes, [
    "May include meetings you declined or that were cancelled or deleted. Check Outlook.",
    "Invites sent only to a group you belong to are not shown.",
    "3 calendar items in Outlook couldn't be read, so meetings may be missing.",
    "1 meeting has more than one stored time. Check Outlook for the current time.",
    "Recurring meetings show only the occurrences Outlook stored; some may be missing.",
    "2 meeting outcome reviews were left out to keep the brief small.",
    "4 later meetings were left out to keep the brief small.",
    "2 cancelled meetings were left out.",
    "This calendar has a gap the brief cannot describe. Check Outlook.",
  ]);
  assert.ok(result.coverage.required_sections.some((row) => row.key === "calendar"
    && row.incomplete_sources.some((source) => source.key === "outlook_calendar")));
  const empty = calendarFixture(NOW, undefined, [{ code: "recurring_unverified" }]);
  empty.items = [];
  const none = await executeRegisteredTool(client({ calendar: empty }), JOE, "morning-brief", {});
  assert.equal(none.sections.calendar.state, "partial", "an empty day with unexpanded series is not empty");
});

test("morning-brief omits the calendar for a partner with no published calendar, and never calls it empty", async () => {
  const result = await executeRegisteredTool(client(), DELL, "morning-brief", {});
  assert.equal(result.sections.calendar, undefined);
  assert.equal(result.state, "ready");
});

test("a stale, malformed or unreadable calendar is partial or unavailable, never ready", async () => {
  const day = 86400000;
  const stale = await executeRegisteredTool(client({ calendar: calendarFixture(NOW, new Date(NOW - day).toISOString()) }), JOE, "morning-brief", {});
  assert.equal(stale.sections.calendar.state, "partial");
  assert.equal(stale.sections.calendar.sources.outlook_calendar.state, "stale");
  const broken = calendarFixture();
  broken.items.push({ start: "not a time", end: null, title: "" });
  const malformed = await executeRegisteredTool(client({ calendar: broken }), JOE, "morning-brief", {});
  assert.equal(malformed.sections.calendar.state, "partial");
  assert.equal(malformed.sections.calendar.malformed_items, 1);
  for (const calendar of [{ schema: "other" }, { ...calendarFixture(), observed_at: "never" }]) {
    const result = await executeRegisteredTool(client({ calendar }), JOE, "morning-brief", {});
    assert.equal(result.sections.calendar.state, "unavailable");
    assert.equal(result.state, "unavailable");
  }
  const failed = await executeRegisteredTool(client({ unavailable: "board_id=$3" }), JOE, "morning-brief", {});
  assert.equal(failed.sections.calendar.state, "unavailable");
});


test("morning-brief connects meetings to all exact matching record contacts", async () => {
  const c = client({ calendar: calendarFixture(), calendarContacts: [
    { email: "bo@example.test", kind: "client", ref: "C-101", name: "Bo Example" },
    { email: "bo@example.test", kind: "lead", ref: "L-102", name: "Bo Example" },
    { email: "ada@example.test", kind: "vendor", ref: "V-103", name: "Ada Example" },
  ] });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  assert.deepEqual(result.sections.calendar.items[1].contacts, [
    { kind: "client", ref: "C-101", name: "Bo Example" },
    { kind: "lead", ref: "L-102", name: "Bo Example" },
    { kind: "vendor", ref: "V-103", name: "Ada Example" },
  ]);
  assert.deepEqual(result.sections.calendar.items[0].contacts, []);
});

test("morning-brief keeps meetings and reports unavailable contact context", async () => {
  const c = client({ calendar: calendarFixture(), unavailable: "calendar_contacts" });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  assert.equal(result.sections.calendar.state, "partial");
  assert.equal(result.sections.calendar.items.length, 2);
  assert.equal(result.sections.calendar.sources.record_contacts.state, "unavailable");
});

test("morning-brief suggests open deals for meetings with an exact client email", async () => {
  const c = client({ calendar: calendarFixture() });
  const query = c.query;
  c.query = async (sql, params) => sql.includes('calendar_deal_matches')
    ? (c.queries.push({ sql, params }), { rows: [
      { email: 'bo@example.test', id: 'deal-1', name: 'Synthetic Deal', client_ref: 'C-101' },
      { email: 'bo@example.test', id: 'deal-2', name: 'Another Deal', client_ref: 'C-101' },
    ] }) : query(sql, params);
  const result = await executeRegisteredTool(c, JOE, 'morning-brief', {});
  assert.deepEqual(result.sections.calendar.items[1].possible_deals, [
    { id: 'deal-1', name: 'Synthetic Deal', client_ref: 'C-101', prep: { state: 'unavailable' } },
    { id: 'deal-2', name: 'Another Deal', client_ref: 'C-101', prep: { state: 'unavailable' } },
  ]);
  assert.deepEqual(result.sections.calendar.items[0].possible_deals, []);
  assert.equal(result.sections.calendar.sources.meeting_prep.state, 'partial');
  const lookup = c.queries.find(({ sql }) => sql.includes('calendar_deal_matches'));
  assert.equal(lookup.params[1], 'joe');
});

test("morning-brief keeps meetings visible when deal suggestions cannot be read", async () => {
  const result = await executeRegisteredTool(client({ calendar: calendarFixture(), unavailable: "calendar_deal_matches" }), JOE, "morning-brief", {});
  assert.equal(result.sections.calendar.items.length, 2);
  assert.equal(result.sections.calendar.state, "partial");
  assert.equal(result.sections.calendar.sources.deal_suggestions.state, "unavailable");
});


test("morning-brief prepares possible meeting deals in the authenticated partner's calendar", async () => {
  const deal = "11111111-1111-4111-8111-111111111111";
  const c = client({ calendar: calendarFixture(),
    calendarDeals: [{ email: "bo@example.test", id: deal, name: "Synthetic lease", client_ref: "C-TEST" }],
    meetingPrep: [{ id: deal, phase: "negotiation", next_step: "Review counteroffer",
      next_date: "2026-11-02", last_touch: "2026-10-25", market: "Test City" }] });
  const result = await executeRegisteredTool(c, { slug: "codex", human: false, via: "oauth-google",
    sponsoring_human_slug: "joe" }, "morning-brief", {});
  const calendar = result.sections.calendar;
  assert.deepEqual(calendar.items[1].possible_deals[0].prep, { state: "ready", phase: "negotiation",
    next_step: "Review counteroffer", next_date: "2026-11-02", last_touch: "2026-10-25", market: "Test City" });
  assert.equal(calendar.sources.meeting_prep.state, "ready");
  assert.equal(calendar.match_basis, "attendee_email_possible_match");
  assert.deepEqual(c.queries.find(({ sql }) => sql.includes("/* calendar_meeting_prep */")).params, [[deal], "joe"]);
});


test("morning-brief keeps meetings and reports failed preparation without losing source warnings", async () => {
  const c = client({ calendar: calendarFixture(NOW, undefined, [{ code: "responses_unverified" }]),
    calendarDeals: [{ email: "bo@example.test", id: "11111111-1111-4111-8111-111111111111",
      name: "Synthetic lease", client_ref: "C-TEST" }], unavailable: "/* calendar_meeting_prep */" });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  const calendar = result.sections.calendar;
  assert.equal(calendar.state, "partial");
  assert.equal(calendar.items.length, 2);
  assert.deepEqual(calendar.items[1].possible_deals[0].prep, { state: "unavailable" });
  assert.equal(calendar.sources.meeting_prep.state, "unavailable");
  assert.ok(calendar.notes.some(note => note.includes("cancelled")));
  assert.ok(calendar.notes.some(note => note.includes("preparation could not be checked")));
  assert.doesNotMatch(JSON.stringify(result), /fixture source unavailable/);
  assert.equal(result.coverage.required_sections.find(row => row.key === "calendar").state, "partial");
});

test("morning-brief skips preparation reads when no meeting has a possible deal", async () => {
  const c = client({ calendar: calendarFixture() });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  assert.equal(result.sections.calendar.state, "ready");
  assert.ok(result.sections.calendar.items.every(item => item.possible_deals.length === 0));
  assert.equal(c.queries.some(({ sql }) => sql.includes("/* calendar_meeting_prep */")), false);
});
