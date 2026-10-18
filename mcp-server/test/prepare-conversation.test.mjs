import { test } from "node:test";
import assert from "node:assert/strict";

import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";

const JOE = { id: "actor-joe", slug: "joe", human: true };

class ConversationFake {
  constructor({ parties = [], deals = [], timeline = [], graphNodes = [], paths = [] } = {}) {
    this.parties = parties;
    this.deals = deals;
    this.timeline = timeline;
    this.graphNodes = graphNodes;
    this.paths = paths;
    this.calls = [];
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });

    if (sql.includes("from v_ref_index") && sql.includes("subject_type in ('lead','client','vendor')") &&
        sql.includes("similarity(display_name,$1)")) return { rows: this.parties };
    if (sql.includes("count(*) filter") && sql.includes("group by display_name")) return { rows: [] };
    if (sql.includes("from v_deal_board where name ilike")) return { rows: this.deals };
    if (sql.includes("from v_party_graph") && sql.includes("where from_name ilike $1 or to_name ilike $1"))
      return { rows: [] };
    if (sql.includes("from v_lead_client_best")) return { rows: [] };
    if (sql.includes("from v_deal_board where client_ref = any")) return { rows: [] };

    if (sql.includes("select subject_id from v_ref_index where subject_type='lead'"))
      return { rows: [{ subject_id: "lead-uuid" }] };
    if (sql.includes("select subject_id from v_ref_index where subject_type='client'"))
      return { rows: [{ subject_id: "client-uuid" }] };
    if (sql.includes("subject_type='deal' and display_name ilike"))
      return { rows: this.deals.length === 1
        ? [{ subject_id: "deal-uuid", display_name: this.deals[0].name }]
        : [] };
    if (sql.includes("from v_subject_timeline")) return { rows: this.timeline };
    if (sql.includes("from tool_call t join activity a")) return { rows: [] };

    if (sql.startsWith("with n as (")) return { rows: this.graphNodes };
    if (sql.includes("where (from_ref is null or to_ref is null)")) return { rows: [] };
    if (sql.startsWith("with recursive e as")) return { rows: this.paths };
    if (sql.includes("select count(*)::int as n from v_party_graph")) return { rows: [{ n: 0 }] };

    throw new Error(`unexpected query: ${sql}`);
  }

  count(fragment) {
    return this.calls.filter(({ sql }) => sql.includes(fragment)).length;
  }
}

const party = (ref, name, kind = "client") => ({
  ref, name, kind, merged: false, city: "Mobile", specialty: null, org_name: null,
});

test("prepare-conversation is an exact bounded read-only contract", () => {
  const tool = TOOLS["prepare-conversation"];
  assert.ok(tool);
  assert.equal(tool.write, false);
  assert.equal(Boolean(tool.fullOnly), false);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(tool.inputSchema.required, ["query"]);
  assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(),
    ["max_depth", "path_limit", "query", "timeline_limit"]);
});

test("one live record returns its timeline and exact-ref introduction paths", async () => {
  const db = new ConversationFake({
    parties: [party("C-127", "Dr. Example")],
    timeline: [{ entry_kind: "activity", summary: "Called practice" }],
    graphNodes: [{ ref: "C-127", name: "Dr. Example", merged: false }],
    paths: [{
      hops: 2,
      ask_ref: "V-CPA-006",
      ask_name: "A. Connector",
      ref_path: ["V-CPA-006", "L-100", "C-127"],
      chain: "A. Connector -knows-> B. Referrer -intro-> Dr. Example",
      first_note: "synthetic relationship evidence",
    }],
  });

  const out = await executeRegisteredTool(db, JOE, "prepare-conversation", {
    query: "Dr. Example",
    timeline_limit: "4",
    path_limit: "6",
    max_depth: "2",
  });

  assert.equal(out.state, "completed");
  assert.deepEqual(out.match, { kind: "client", name: "Dr. Example", target: "C-127" });
  assert.equal(out.catch_up.timeline.length, 1);
  assert.equal(out.introduction.status, "evaluated");
  assert.equal(out.introduction.target, "C-127");
  assert.equal(out.introduction.paths[0].ask_ref, "V-CPA-006");

  const timeline = db.calls.find(({ sql }) => sql.includes("from v_subject_timeline"));
  const graph = db.calls.find(({ sql }) => sql.startsWith("with recursive e as"));
  assert.equal(timeline.params[2], 4);
  assert.deepEqual(graph.params, ["C-127", 2, 6]);
});

test("ambiguous identity stops before timeline and graph reads", async () => {
  const db = new ConversationFake({
    parties: [party("C-127", "Alex Example"), party("L-204", "Alex Example", "lead")],
  });
  const out = await executeRegisteredTool(db, JOE, "prepare-conversation", {
    query: "Alex Example",
  });
  assert.equal(out.state, "needs_disambiguation");
  assert.equal(Object.hasOwn(out, "catch_up"), false);
  assert.equal(Object.hasOwn(out, "introduction"), false);
  assert.equal(db.count("from v_subject_timeline"), 0);
  assert.equal(db.count("with recursive e as"), 0);
});

test("a deal brief returns the timeline without pretending a deal is an intro-graph person", async () => {
  const db = new ConversationFake({
    deals: [{ name: "Example Dental Expansion", phase: "tour", owner: "joe", client_ref: "C-127" }],
    timeline: [{ entry_kind: "event", summary: "Deal created" }],
  });
  const out = await executeRegisteredTool(db, JOE, "prepare-conversation", {
    query: "Example Dental Expansion",
  });
  assert.equal(out.state, "completed");
  assert.equal(out.match.kind, "deal");
  assert.deepEqual(out.introduction, {
    status: "not_applicable",
    reason: "Deals do not represent people or organizations in the introduction graph.",
  });
  assert.equal(db.count("with recursive e as"), 0);
});

test("unknown, authority-bearing, malformed, and over-budget inputs fail before any read", async () => {
  const attacks = [
    { query: "Example", provider: "CARR-SECRET-CANARY-7F4A" },
    { query: "Example", profile: "CARR-SECRET-CANARY-7F4A" },
    { query: [] },
    { query: "   " },
    { query: "Example", timeline_limit: 21 },
    { query: "Example", path_limit: 11 },
    { query: "Example", max_depth: 4 },
  ];
  for (const args of attacks) {
    const db = new ConversationFake();
    await assert.rejects(
      () => executeRegisteredTool(db, JOE, "prepare-conversation", args),
      (error) => {
        assert.ok(error instanceof ToolError);
        assert.doesNotMatch(JSON.stringify(error.payload), /CARR-SECRET-CANARY-7F4A/);
        return true;
      },
    );
    assert.equal(db.calls.length, 0);
  }
});
