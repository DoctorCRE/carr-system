// Tests research requirements for national account and market deal creation.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "test" };

class NationalFake {
  constructor() { this.partyInserts = 0; }
  async query(text) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select id,name from party where kind='org'")) return { rows: [] };
    if (sql.includes("where c.id=$1 and c.client_type='national_account'"))
      return { rows: [{ id: "account-client", party_id: "account-party", name: "Brand" }] };
    if (sql.startsWith("select id,name from deal where outcome is null")) return { rows: [] };
    if (sql.includes("where p.org_id=$1") && sql.includes("lower(p.name)=lower($2)")) return { rows: [] };
    if (sql.startsWith("insert into party")) { this.partyInserts++; return { rows: [{ id: "new-party" }] }; }
    throw new Error("unexpected query: " + sql);
  }
}

test("create-national-account rejects an unresearched synthetic org before insert", async () => {
  const db = new NationalFake();
  await assert.rejects(() => TOOLS["create-national-account"].handler(db, joe, {
    idempotency_key: "national-account-no-research", name: "Thin Brand", owner: "joe",
  }), e => e instanceof ToolError && e.payload.error === "research_evidence_required" && e.payload.gate === "create-national-account");
  assert.equal(db.partyInserts, 0);
});

test("create-national-market-deal rejects an unresearched franchisee before insert", async () => {
  const db = new NationalFake();
  await assert.rejects(() => TOOLS["create-national-market-deal"].handler(db, joe, {
    idempotency_key: "national-market-no-research", account_client_id: "account-client",
    client_name: "Thin Franchisee", deal_name: "Thin Franchisee lease", market: "Pensacola",
  }), e => e instanceof ToolError && e.payload.error === "research_evidence_required" && e.payload.gate === "create-national-market-deal");
  assert.equal(db.partyInserts, 0);
});
