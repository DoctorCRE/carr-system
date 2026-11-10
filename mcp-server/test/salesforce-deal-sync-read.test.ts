// Test Salesforce deal synchronization reads and updates.

import assert from "node:assert/strict";
import test from "node:test";
import { ToolError } from "../src/tool-error.ts";

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Expected object");
  return Object.fromEntries(Object.entries(value));
}
const registry = record(await import(new URL("../src/tools.js", import.meta.url).href));
const TOOLS = record(registry.TOOLS);
async function handler(name: string, ...args: unknown[]) {
  const invoke = record(TOOLS[name]).handler;
  if (typeof invoke !== "function") throw new TypeError("Expected handler");
  return record(await invoke(...args));
}

test("sync snapshot includes closed deal identities, clients, owners and labeled close dates", async () => {
  const rows = [
    {
      id: "example-deal",
      phase: "closed",
      outcome: "won",
      base_version: 2,
      salesforce_id: "006000000000001",
      client_ref: "C-001",
      client_name: "Example Practice",
      owner: "dell",
      sf_close_date_placeholder: "2026-12-01",
    },
  ];
  const result = await handler(
    "read-deal-reconciliation",
    {
      query: async (sql: string) => {
        assert.match(sql, /v_deal_reconciliation_read/);
        assert.match(sql, /v_export_deals/);
        assert.doesNotMatch(sql, /source_row|commission|where .*outcome|where .*phase/i);
        return { rows };
      },
    },
    {},
    { all: true },
  );
  assert.deepEqual(result, { deals: rows });
});

test("bulk sync read cannot silently ignore a deal selector", async () => {
  await assert.rejects(
    handler(
      "read-deal-reconciliation",
      {
        query: async () => {
          assert.fail("invalid selection must not query");
        },
      },
      {},
      { all: true, deal: "example-deal" },
    ),
    (error) => error instanceof ToolError && error.payload.error === "choose_deal_or_all",
  );
});

const dealId = "00000000-0000-4000-8000-000000000001";
const actor = { id: "00000000-0000-4000-8000-000000000002", slug: "joe", human: true };
function updateDb() {
  const writes: { sql: string; args: unknown[] }[] = [];
  return {
    writes,
    query: async (sql: string, args: unknown[]) => {
      if (sql.includes("from tool_call")) return { rows: [] };
      if (sql.includes("from v_ref_index")) return { rows: [{ subject_type: "deal", subject_id: dealId }] };
      if (sql.startsWith("select version")) return { rows: [{ version: 1 }] };
      if (sql.startsWith("select sf_close_date_placeholder")) return { rows: [{ sf_close_date_placeholder: null }] };
      if (sql.startsWith("update deal") || sql.startsWith("insert into event")) {
        writes.push({ sql, args });
        return { rows: [] };
      }
      if (sql.startsWith("insert into tool_call")) return { rows: [] };
      throw new Error(sql);
    },
  };
}

test("Salesforce close date updates only its labeled placeholder and records an event", async () => {
  const db = updateDb();
  const result = await handler("update-deal", db, actor, {
    idempotency_key: "00000000-0000-4000-8000-000000000003",
    deal: dealId,
    base_version: 1,
    fields: { sf_close_date_placeholder: "2026-12-01" },
  });
  assert.deepEqual(result.updated, ["sf_close_date_placeholder"]);
  assert.equal(db.writes.length, 2);
  const write = db.writes.at(0);
  if (!write) throw new TypeError("Expected write");
  assert.match(write.sql, /sf_close_date_placeholder=/);
  assert.doesNotMatch(write.sql, /closed_on|won_value/);
});

test("invalid Salesforce close dates fail before a write", async () => {
  for (const value of ["2026-02-30", "2026-13-01", "tomorrow", null]) {
    const db = updateDb();
    await assert.rejects(
      handler("update-deal", db, actor, {
        idempotency_key: "00000000-0000-4000-8000-000000000003",
        deal: dealId,
        base_version: 1,
        fields: { sf_close_date_placeholder: value },
      }),
      (error) => error instanceof ToolError && error.payload.error === "invalid_sf_close_date_placeholder",
    );
    assert.equal(db.writes.length, 0);
  }
});
