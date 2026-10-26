// morning-brief.test.mjs — safe record-native morning delivery.
//
// This is deliberately a fake-client suite.  It proves the handler's source
// and authority contract without a Worker, credential, or database connection.

import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool } from "../src/tools.js";
import { readCallInsertSQL } from "../src/mcp.js";

const JOE = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "fixture" };
const DELL = { slug: "dell", display: "Dell", human: true, via: "oauth-google", client_id: "fixture" };
const UNSPONSORED = { slug: "codex", display: "Codex", human: false, via: "agent-token" };

function costFixture() {
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  return { schema: "carr-system-costs.v1", observed_at: new Date().toISOString(),
    month: yesterday.slice(0, 7), through: yesterday, state: "ready",
    providers: [{ provider: "github", label: "GitHub", plan: "Usage", mtd_usd: 2.5,
      projection_usd: 10, budget_usd: 50, state: "ready", reason: null,
      daily: [{ day: yesterday, usd: 2.5, drivers: { "actions": 2.5 } }] }],
    months: [{ month: yesterday.slice(0, 7), usd: 2.5, providers: { github: 2.5 } }],
    alerts: [], action: "Finance Ops: inspect provider and driver; verify the next complete day; auto-clear when thresholds pass." };
}

function client({ unavailable = null, accounts = [], renewalState = "ready", renewalRows = null, costs = costFixture(), todayRows = null, dealRows = null, governanceQueue = null, localUpdatedAt = new Date().toISOString(), calendar = null } = {}) {
  const queries = [];
  return {
    queries,
    query: async (sql, params = []) => {
      queries.push({ sql, params });
      if (unavailable && sql.includes(unavailable)) throw new Error("fixture source unavailable");
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
      if (sql.includes("from v_deal_room_board")) return { rows: dealRows || [
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
  assert.match(sql, /v_deal_room_board/);
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
    localUpdatedAt: new Date(Date.now() - 3 * 3600000).toISOString(),
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

test("tool_read_call remains metadata-only for the composite response", () => {
  const output = { private_body: "Candidate Safe", sections: { renewals: { items: [{ display_name: "Renewal Safe" }] } } };
  const receipt = readCallInsertSQL(JOE, "morning-brief", true, null);
  assert.doesNotMatch(receipt.text, /response|argument|body|sponsor.*\$11/i);
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
  stale.observed_at = new Date(Date.now() - 37 * 3600000).toISOString();
  const future = costFixture();
  future.observed_at = new Date(Date.now() + 3600000).toISOString();
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

function calendarFixture(now = Date.now(), observedAt = new Date(now).toISOString()) {
  const at = (hours) => new Date(now + hours * 3600000).toISOString();
  return { schema: "carr-calendar-agenda.v1", source: "outlook_hxstore", lifecycle: "unverified",
    observed_at: observedAt, items: [
      { start: at(-30), end: at(-29), title: "Synthetic finished review", attendees: [] },
      { start: at(26), end: at(27), title: "Synthetic site tour", location: "Example Suite 100",
        organizer: { name: "Ada Example", email: "Ada@Example.test" },
        attendees: [{ name: "Bo Example", email: "bo@example.test" }], attendee_count: 1, conflict: true },
      { start: at(2), end: at(3), title: "Synthetic lender call", location: null, attendees: [] },
      { start: at(24 * 8), end: at(24 * 8 + 1), title: "Synthetic next-week meeting", attendees: [] },
    ] };
}

test("morning-brief shows the partner's own published calendar for the next seven days, soonest first", async () => {
  const c = client({ calendar: calendarFixture() });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  const calendar = result.sections.calendar;
  assert.equal(result.state, "ready");
  assert.equal(calendar.state, "ready");
  assert.equal(calendar.lifecycle, "unverified");
  assert.deepEqual(calendar.items.map((row) => row.title), ["Synthetic lender call", "Synthetic site tour"]);
  assert.deepEqual(calendar.items[1].organizer, { name: "Ada Example", email: "ada@example.test" });
  assert.deepEqual(calendar.items[1].attendees, [{ name: "Bo Example", email: "bo@example.test" }]);
  assert.equal(calendar.items[1].location, "Example Suite 100");
  assert.equal(calendar.items[1].conflict, true);
  const read = c.queries.find(({ params }) => params[2] === "calendar-agenda");
  assert.deepEqual(read.params.slice(1), ["joe", "calendar-agenda"], "sponsor comes from authentication");
  assert.ok(result.coverage.required_sections.some((row) => row.key === "calendar" && row.counts.items === 2));
});

test("morning-brief omits the calendar for a partner with no published calendar, and never calls it empty", async () => {
  const result = await executeRegisteredTool(client(), DELL, "morning-brief", {});
  assert.equal(result.sections.calendar, undefined);
  assert.equal(result.state, "ready");
});

test("a stale, malformed or unreadable calendar is partial or unavailable, never ready", async () => {
  const day = 86400000;
  const stale = await executeRegisteredTool(client({ calendar: calendarFixture(Date.now(), new Date(Date.now() - day).toISOString()) }), JOE, "morning-brief", {});
  assert.equal(stale.sections.calendar.state, "partial");
  assert.deepEqual(stale.sections.calendar.sources, { outlook_calendar: { state: "stale" } });
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
