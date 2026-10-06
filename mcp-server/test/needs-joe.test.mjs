// needs-joe.test.mjs — gap #10: ONE list of everything waiting on Joe.
//
// governance-queue carries the list as `needs_joe`; morning-brief leads with
// it; the progress board publishes it as its top lane. Every item is read live
// from the record that owns it, so it leaves the list when that record closes.
// Fake-client suite: one fixture per source, no Worker or database.

import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";
import { classifyHumanOnly, NEEDS_JOE_LOCAL_BOARD } from "../src/needs-joe.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth-google", client_id: "fixture" };
const NOW = Date.parse("2026-10-05T12:00:00Z");

const loops = [
  { number: "A17", kind: "action_required", owner: "joe", marker: "none", blocker_class: "human_only",
    blocker_detail: "only Joe can sign this at the bank", unblocks: "the Q4 invoice run",
    label: "Pay the CoStar renewal invoice", created_at: "2026-09-30T12:00:00Z" },
  { number: "412", kind: "open_loop", owner: "Joe", marker: "none", blocker_class: "capability",
    blocker_detail: "anthropic-admin credential-health probe", unblocks: null,
    label: "Anthropic admin key expires in 6 days", created_at: "2026-10-04T12:00:00Z" },
  { number: "413", kind: "open_loop", owner: "joe", marker: "decision", blocker_class: null,
    blocker_detail: null, unblocks: null,
    label: "Choose whether the lead board keeps the archived lane", created_at: "2026-10-01T12:00:00Z" },
  { number: "414", kind: "open_loop", owner: "joe", marker: "none", blocker_class: null,
    blocker_detail: null, unblocks: null,
    label: "Tidy the loop renderer column order", created_at: "2026-09-01T12:00:00Z" },
  { number: "415", kind: "open_loop", owner: "joe", marker: "none", blocker_class: "counterparty",
    blocker_detail: "landlord has not returned the redline", unblocks: null,
    label: "Hear back from the landlord on the redline", created_at: "2026-09-20T12:00:00Z" },
  { number: "417", kind: "open_loop", owner: "joe", marker: "none", blocker_class: null,
    blocker_detail: null, unblocks: null, domain: "system",
    label: "Nothing closes incidents; the credential health row mentions a client", created_at: "2026-08-21T12:00:00Z" },
  { number: "418", kind: "open_loop", owner: "joe", marker: "none", blocker_class: "capability",
    blocker_detail: "anthropic-admin credential-health probe", unblocks: null,
    label: "Anthropic admin key expires in 6 days", created_at: "2026-10-04T13:00:00Z" },
  { number: "416", kind: "open_loop", owner: "joe", marker: "dated", blocker_class: "human_only",
    blocker_detail: "Joe makes the first call himself", unblocks: null, domain: "prospecting",
    label: "Email the prospect about the clinic search", created_at: "2026-09-25T12:00:00Z" },
];
const workRequests = [
  { ref: "WR-000201", title: "Turn on Salesforce write-back", blocker_code: "needs_joe",
    blocker_detail: "Approve the first live Salesforce write", updated_at: "2026-10-03T12:00:00Z" },
];
const questions = [
  { board_id: "carr-v5", question_id: "q-merge-window", prompt: "Hold merges during Friday's demo?",
    asked_at: "2026-10-05T06:00:00Z" },
];
const governance = {
  pending_rule_approvals: [
    { rule_id: "7d9b1a2c-0000-0000-0000-000000000001", statement: "Name the deal in every question box.",
      admitted_at: "2026-10-02T12:00:00Z" },
  ],
  pending_guidance_import_batches: [],
  pending_retrieval_proposals: [
    { proposal_id: "p-1", proposal_type: "phrase", reason: "synonym", proposed_at: "2026-10-01T00:00:00Z" },
  ],
};
const localPage = {
  schema: "needs-joe-local.v1", observed_at: "2026-10-05T11:50:00Z",
  items: [
    { kind: "needs_joe", repo: "jbookout/carr-system", number: 1244, pr_state: "OPEN",
      pr_title: "Route typed calls through TypeSafe", text: "restore TypeSafe API credits",
      at: "2026-10-02T23:08:59" },
    { kind: "waiting", repo: "jbookout/doctorcre-app", number: 131, pr_state: "OPEN",
      pr_title: "Board lane", text: "WAITING ON jbookout/carr-system#1467", at: "2026-10-03T10:00:00" },
    { kind: "tabled", repo: null, number: null, pr_state: null, pr_title: null,
      text: "sign in to Codex and xAI on the Studio", at: "2026-10-04T08:00:00" },
  ],
};

function client({ fail = null, local = localPage, localUpdatedAt = "2026-10-05T11:50:00Z" } = {}) {
  const calls = [];
  return {
    calls,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (fail && sql.includes(fail)) throw new Error("fixture source unavailable");
      if (/read_governance_queue/.test(sql)) return { rows: [{ queue: governance }] };
      if (sql.includes("from loop_item")) return { rows: loops };
      if (sql.includes("from ops.work_request")) return { rows: workRequests };
      if (sql.includes("from board_question")) return { rows: questions };
      if (sql.includes("from board_snapshot"))
        return { rows: local ? [{ snapshot_json: local, updated_at: localUpdatedAt }] : [] };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
}

async function read(options, actor = joe) {
  const c = client(options);
  const result = await TOOLS["governance-queue"].handler(c, actor, {}, { now: NOW });
  return { result, list: result.needs_joe, calls: c.calls };
}

test("governance-queue keeps its sealed contract: read-only, no arguments", () => {
  const tool = TOOLS["governance-queue"];
  assert.equal(tool.write, false);
  assert.equal(tool.humanOnly, undefined);
  assert.deepEqual(tool.inputSchema, { type: "object", additionalProperties: false, properties: {} });
});

test("every source contributes, each item carries the six fields Joe needs", async () => {
  const { list } = await read();
  assert.equal(list.schema, "needs-joe.v1");
  assert.equal(list.state, "ready");
  const sources = new Set(list.items.map(item => item.source));
  for (const source of ["action_required", "loop", "work_request", "board_question",
    "rule_approval", "pull_request", "tabled"])
    assert.ok(sources.has(source), `missing source ${source}`);
  for (const item of list.items) {
    for (const field of ["title", "why", "action", "age_days", "blocks"])
      assert.ok(item[field] !== undefined && item[field] !== "", `${item.key} lacks ${field}`);
    assert.ok(item.why.class && item.why.text, item.key);
    assert.ok("link" in item, item.key);
    // Rule 3a9dbafd: a title leads with what the thing is, never a bare id.
    assert.doesNotMatch(item.title, /^(#?\d+|A\d+|WR-\d+|[0-9a-f]{8})\b/i, item.title);
  }
  assert.equal(list.count, list.items.length);
});

test("human-only classes come from the existing gates, most specific first", async () => {
  const { list } = await read();
  const byKey = Object.fromEntries(list.items.map(item => [item.key, item]));
  assert.equal(byKey["loop:A17"].why.class, "human_only");
  assert.equal(byKey["loop:412"].why.class, "credential");
  assert.equal(byKey["loop:413"].why.class, "ruling");
  assert.equal(byKey["pr:jbookout/carr-system#1244"].why.class, "money");
  assert.equal(byKey["tabled:sign in to Codex and xAI on the Studio"].why.class, "credential");
  assert.equal(byKey["work-request:WR-000201"].why.class, "ruling");
  assert.equal(byKey["rule:7d9b1a2c-0000-0000-0000-000000000001"].why.class, "ruling");
  assert.equal(classifyHumanOnly("Log in to the Neon console"), "credential");
  assert.equal(classifyHumanOnly("Confirm with Face ID"), "credential");
  assert.equal(classifyHumanOnly("Force-push the release branch"), "irreversible");
  assert.equal(classifyHumanOnly("Email the LOI to the client"), "outbound");
  assert.equal(classifyHumanOnly("Rename the export column"), null);
});

test("what the system can decide is excluded, counted, and explained", async () => {
  const { list } = await read();
  const keys = list.items.map(item => item.key);
  assert.ok(!keys.includes("loop:414"), "internal tidy-up is the system's to do");
  assert.ok(!keys.includes("loop:415"), "a landlord reply waits on the landlord, not Joe");
  assert.ok(!keys.some(key => key.startsWith("retrieval")), "retrieval approval carries no human-only flag");
  assert.ok(!keys.includes("pr:jbookout/doctorcre-app#131"), "waiting on another PR, not on Joe");
  assert.ok(!keys.includes("loop:416"), "lead outreach lives on the Lead Board, never this list");
  assert.ok(!keys.includes("loop:417"), "an unflagged loop is internal work even when its text mentions a credential");
  assert.equal(list.excluded.count, 6);
  assert.deepEqual(list.excluded.by_reason, {
    internal: 2, waiting_on_others: 2, system_decidable: 1, lead_outreach: 1,
  });
  for (const reason of Object.keys(list.excluded.by_reason))
    assert.ok(list.excluded.reasons[reason], `reason ${reason} is explained`);
});

test("records with the same title collapse into one item that names every record", async () => {
  const { list } = await read();
  const expiry = list.items.filter(item => item.title === "Anthropic admin key expires in 6 days");
  assert.equal(expiry.length, 1);
  assert.deepEqual(expiry[0].records, ["loop:412", "loop:418"]);
  assert.equal(expiry[0].key, "loop:412", "the oldest record leads");
});

test("ordered by what it blocks: live work, then capabilities, then pending approvals; oldest first", async () => {
  const { list } = await read();
  const tiers = list.items.map(item => item.blocks.tier);
  const rank = { work: 0, capability: 1, pending: 2 };
  assert.deepEqual(tiers, [...tiers].sort((a, b) => rank[a] - rank[b]));
  const work = list.items.filter(item => item.blocks.tier === "work");
  assert.deepEqual(work.map(item => item.age_days), [...work.map(item => item.age_days)].sort((a, b) => b - a));
  assert.equal(list.items.at(-1).key, "rule:7d9b1a2c-0000-0000-0000-000000000001");
  const pr = list.items.find(item => item.source === "pull_request");
  assert.equal(pr.link, "https://github.com/jbookout/carr-system/pull/1244");
  assert.match(pr.blocks.text, /Route typed calls through TypeSafe/);
});

test("an item leaves the list when its source record closes; no copy is kept", async () => {
  const { calls } = await read();
  const loopSql = calls.find(call => call.sql.includes("from loop_item")).sql;
  assert.match(loopSql, /status\s*=\s*'open'/);
  const wrSql = calls.find(call => call.sql.includes("from ops.work_request")).sql;
  assert.match(wrSql, /state\s*=\s*'needs_joe'/);
  const qSql = calls.find(call => call.sql.includes("from board_question")).sql;
  assert.match(qSql, /a\.id is null/);
  assert.match(qSql, /q\.current\s*=\s*true/);
  const localSql = calls.find(call => call.sql.includes("from board_snapshot"));
  assert.ok(localSql.params.includes(NEEDS_JOE_LOCAL_BOARD));
  assert.ok(!calls.some(call => /\b(insert|update|delete)\b/i.test(call.sql)), "read-only");
});

test("a failed source is unavailable, never an empty list", async () => {
  const { list } = await read({ fail: "from ops.work_request" });
  assert.equal(list.state, "partial");
  assert.equal(list.sources.work_requests.state, "unavailable");
  assert.ok(!list.items.some(item => item.source === "work_request"));
  assert.ok(list.items.length > 0);
});

test("a stale local publication is flagged, and a missing one is unavailable", async () => {
  const stale = await read({ localUpdatedAt: "2026-10-05T06:00:00Z" });
  assert.equal(stale.list.sources.local.state, "stale");
  assert.equal(stale.list.state, "partial");
  assert.ok(stale.list.items.filter(item => item.source === "pull_request").every(item => item.stale === true));
  const missing = await read({ local: null });
  assert.equal(missing.list.sources.local.state, "unavailable");
  assert.ok(!missing.list.items.some(item => item.source === "pull_request"));
});

test("the existing governance lanes are unchanged", async () => {
  const { result } = await read();
  assert.deepEqual(result.pending_rule_approvals, governance.pending_rule_approvals);
  assert.deepEqual(result.counts, {
    pending_rule_approvals: 1, pending_guidance_import_batches: 0,
    pending_retrieval_proposals: 1, total: 2,
  });
});


test("NULL-tenant general and program Work Requests are included without including other tenants", async () => {
  const c = client();
  const query = c.query;
  const rows = [
    { ...workRequests[0], organization_tenant_id: null, kind: "general" },
    { ...workRequests[0], ref: "WR-000202", title: "Second request", organization_tenant_id: "carr-internal" },
    { ...workRequests[0], ref: "WR-000203", title: "Other tenant", organization_tenant_id: "other-tenant" },
    { ...workRequests[0], ref: "WR-000204", title: "Program request", organization_tenant_id: null, kind: "program" },
  ];
  c.query = async (sql, params) => {
    if (!sql.includes("from ops.work_request")) return query(sql, params);
    const admitsNull = /organization_tenant_id is null/i.test(sql);
    return { rows: rows.filter(row => row.organization_tenant_id === params[0]
      || (admitsNull && row.organization_tenant_id === null)) };
  };
  const result = await TOOLS["governance-queue"].handler(c, joe, {}, { now: NOW });
  assert.deepEqual(result.needs_joe.items.filter(item => item.source === "work_request")
    .map(item => item.key).sort(), ["work-request:WR-000201", "work-request:WR-000202", "work-request:WR-000204"]);
});

test("Joe's list is not applicable for readers without Joe partner authority", async () => {
  for (const actor of [
    { slug: "grok-reviewer", human: false },
    { slug: "hermes", human: false, sponsoring_human_slug: "joe" },
    { slug: "dell", human: true },
    { slug: "codex", human: false, native_agent_verified: true, sponsoring_human_slug: "dell" },
    { slug: "codex", human: false, native_agent_verified: false, sponsoring_human_slug: "joe" },
  ]) {
    const { list, calls } = await read({}, actor);
    assert.equal(list.state, "not_applicable", actor.slug);
    assert.deepEqual(list.items, []);
    assert.ok(calls.every(call => call.sql.includes("read_governance_queue")));
  }
  const { list } = await read({}, { slug: "codex", human: false,
    native_agent_verified: true, sponsoring_human_slug: "joe" });
  assert.ok(list.items.some(item => item.source === "board_question"));
});

test("flagged loops always appear and explicit classifications win over keywords", async () => {
  const c = client();
  const query = c.query;
  c.query = async (sql, params) => sql.includes("from loop_item") ? { rows: [
    { number: "1", kind: "action_required", label: "Review the Q4 roadmap and pick the order" },
    { number: "2", kind: "open_loop", blocker_class: "capability", label: "needs the Mac Studio GPU upgrade installed" },
    { number: "3", kind: "open_loop", marker: "decision", label: "Decide whether the lease abstraction schema keeps tenant_id" },
    { number: "4", kind: "open_loop", blocker_class: "ruling", label: "Rule on the post-merge CI order" },
    { number: "5", kind: "open_loop", blocker_class: "human_only", label: "Rotate the secret scanning baseline" },
  ] } : query(sql, params);
  const result = await TOOLS["governance-queue"].handler(c, joe, {}, { now: NOW });
  assert.deepEqual(Object.fromEntries(result.needs_joe.items.filter(item => item.source === "loop"
    || item.source === "action_required").map(item => [item.key, item.why.class])), {
    "loop:1": "human_only", "loop:2": "human_only", "loop:3": "ruling",
    "loop:4": "ruling", "loop:5": "human_only",
  });
});

test("display classes cover the conduct gate vocabulary and watchdog credential phrases", () => {
  const conduct = readFileSync(new URL("../../hooks/conduct_patterns.py", import.meta.url), "utf8");
  const protectedBlock = conduct.split("PROTECTED = re.compile(")[1].split("re.I)")[0];
  const literals = [...protectedBlock.matchAll(/r"([^"\n]*)"/g)].map(match => match[1]);
  const words = literals.join("").match(/client\|(.*?)\)\\b/)[0]
    .replace(/\)\\b$/, "").split("|");
  for (const word of words) {
    const sample = word.replace("[- ]", "-").replace("s?", "s");
    assert.ok(classifyHumanOnly(sample), `conduct gate: ${sample}`);
  }
  for (const text of ["$240", "£20", "€30", "10 usd", "10 dollars", "per month", "a year", "per seat", "a user"])
    assert.equal(classifyHumanOnly(text), "money", text);
  const watchdog = JSON.parse(readFileSync(new URL("../../ops/config/job-watchdog.json", import.meta.url)));
  for (const phrase of watchdog.needs_joe_patterns.credentials)
    assert.equal(classifyHumanOnly(phrase), "credential", phrase);
});
