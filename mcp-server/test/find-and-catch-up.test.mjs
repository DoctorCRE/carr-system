import { test } from "node:test";
import assert from "node:assert/strict";

import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";

const JOE = { id: "actor-joe", slug: "joe", human: true };

class FindCatchUpFake {
  constructor({ parties = [], organizations = [], deals = [], dealResolutionRows, timeline = [] } = {}) {
    this.parties = parties;
    this.organizations = organizations;
    this.deals = deals;
    this.dealResolutionRows = dealResolutionRows;
    this.timeline = timeline;
    this.queries = [];
    this.calls = [];
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.queries.push(sql);
    this.calls.push({ sql, params });

    if (sql.includes("from v_ref_index") && sql.includes("subject_type in ('lead','client','vendor')") &&
        sql.includes("similarity(display_name,$1)")) return { rows: this.parties };
    if (sql.includes("count(*) filter") && sql.includes("group by display_name"))
      return { rows: this.organizations };
    if (sql.includes("from v_deal_board where name ilike")) return { rows: this.deals };
    if (sql.includes("from v_party_graph")) return { rows: [] };
    if (sql.includes("from v_lead_client_best")) return { rows: [] };
    if (sql.includes("from v_deal_board where client_ref = any")) return { rows: [] };

    if (sql.includes("select subject_id from v_ref_index where subject_type='lead'"))
      return { rows: [{ subject_id: "lead-uuid" }] };
    if (sql.includes("select subject_id from v_ref_index where subject_type='client'"))
      return { rows: [{ subject_id: "client-uuid" }] };
    if (sql.includes("select subject_id from v_ref_index where subject_type='vendor'"))
      return { rows: [{ subject_id: "vendor-uuid" }] };
    if (sql.includes("select subject_id from v_ref_index where subject_type='party'"))
      return { rows: [{ subject_id: "party-uuid" }] };
    if (sql.includes("subject_type='deal' and display_name ilike"))
      return { rows: this.dealResolutionRows ?? (this.deals.length === 1
        ? [{ subject_id: "deal-uuid", display_name: this.deals[0].name }]
        : []) };
    if (sql.includes("from v_subject_timeline")) return { rows: this.timeline };
    if (sql.includes("from tool_call t join activity a")) return { rows: [] };

    throw new Error(`unexpected query: ${sql}`);
  }

  timelineReads() {
    return this.queries.filter((sql) => sql.includes("from v_subject_timeline")).length;
  }
}

function party(ref, name, merged = false, kind = "lead") {
  return { ref, name, merged, kind, city: "Mobile", specialty: null, org_name: null };
}

function rejectedPayload(fn) {
  return assert.rejects(fn, (error) => {
    assert.ok(error instanceof ToolError);
    assert.ok(error.payload?.error);
    assert.doesNotMatch(JSON.stringify(error.payload), /CARR-SECRET-CANARY-7F4A/);
    return true;
  });
}

test("the pilot is one exact read-only tool contract", () => {
  const tool = TOOLS["find-and-catch-up"];
  assert.ok(tool);
  assert.equal(tool.write, false);
  assert.equal(Boolean(tool.fullOnly), false);
  assert.deepEqual(tool.inputSchema.required, ["query"]);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(), ["limit", "query"]);
});

test("one live find result proceeds to one bounded catch-up", async () => {
  const db = new FindCatchUpFake({
    parties: [party("L-204", "Dr. Example")],
    timeline: [{ entry_kind: "activity", summary: "Called practice" }],
  });

  const out = await executeRegisteredTool(db, JOE, "find-and-catch-up", {
    query: "Dr. Example",
    limit: "7",
  });

  assert.equal(out.state, "completed");
  assert.deepEqual(out.match, { kind: "lead", name: "Dr. Example", target: "L-204" });
  assert.deepEqual(out.catch_up.subject, { type: "lead", id: "lead-uuid" });
  assert.equal(out.catch_up.timeline.length, 1);
  assert.equal(db.timelineReads(), 1);
  const timelineCall = db.calls.find(({ sql }) => sql.includes("from v_subject_timeline"));
  assert.equal(timelineCall.sql.includes("limit $3"), true);
  assert.equal(timelineCall.params[2], 7);
});

test("multiple live refs stop for disambiguation and never read a timeline", async () => {
  const db = new FindCatchUpFake({
    parties: [party("L-204", "Alex Example"), party("C-127", "Alex Example", false, "client")],
  });

  const out = await executeRegisteredTool(db, JOE, "find-and-catch-up", { query: "Alex Example" });

  assert.equal(out.state, "needs_disambiguation");
  assert.deepEqual(out.candidates.map((row) => row.target), ["C-127", "L-204"]);
  assert.equal(Object.hasOwn(out, "catch_up"), false);
  assert.equal(db.timelineReads(), 0);
});

test("retired tombstones are never selected as a catch-up target", async () => {
  const db = new FindCatchUpFake({ parties: [party("L-099", "Retired Example", true)] });
  const out = await executeRegisteredTool(db, JOE, "find-and-catch-up", { query: "Retired Example" });
  assert.equal(out.state, "not_found");
  assert.deepEqual(out.candidates, []);
  assert.equal(db.timelineReads(), 0);
});

test("one deal-name match may proceed, while catch-me-up remains the final resolver", async () => {
  const db = new FindCatchUpFake({
    deals: [{ name: "Example Dental Expansion", phase: "tour", owner: "joe", client_ref: "C-127" }],
    timeline: [{ entry_kind: "event", summary: "Deal created" }],
  });
  const out = await executeRegisteredTool(db, JOE, "find-and-catch-up", {
    query: "Example Dental Expansion",
  });
  assert.equal(out.state, "completed");
  assert.deepEqual(out.match, {
    kind: "deal",
    name: "Example Dental Expansion",
    target: "Example Dental Expansion",
  });
  assert.equal(out.catch_up.subject.type, "deal");
  assert.equal(db.timelineReads(), 1);
});

test("catch-me-up may still refuse a deal name that resolves ambiguously", async () => {
  const db = new FindCatchUpFake({
    deals: [{ name: "Example Expansion", phase: "tour", owner: "joe", client_ref: "C-127" }],
    dealResolutionRows: [
      { subject_id: "deal-a", display_name: "Example Expansion - East" },
      { subject_id: "deal-b", display_name: "Example Expansion - West" },
    ],
  });
  await assert.rejects(
    () => executeRegisteredTool(db, JOE, "find-and-catch-up", { query: "Example Expansion" }),
    (error) => error instanceof ToolError && error.payload?.error === "needs_disambiguation",
  );
  assert.equal(db.timelineReads(), 0);
});

test("unknown, authority-bearing, empty, and over-budget inputs fail before any read", async () => {
  for (const args of [
    { query: "Example", provider: "CARR-SECRET-CANARY-7F4A" },
    { query: "Example", profile: "CARR-SECRET-CANARY-7F4A" },
    { query: ["Example"] },
    { query: "   " },
    { query: "Example", limit: 51 },
  ]) {
    const db = new FindCatchUpFake();
    await rejectedPayload(() => executeRegisteredTool(db, JOE, "find-and-catch-up", args));
    assert.equal(db.queries.length, 0);
  }
});
