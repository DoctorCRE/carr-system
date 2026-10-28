// A candidate-pool row is evidence of a prospect, not permission to mint a
// thin canonical contact.  Promotion must cross the same research boundary as
// direct contact creation.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "test" };

class PoolFake {
  constructor() { this.partyInserts = 0; }
  async query(text) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select version from candidate_pool")) return { rows: [{ version: 1 }] };
    if (sql.includes("from candidate_pool where id = $1")) return { rows: [{
      id: "pool-id", source: "registry", source_key: "row-1", status: "pool", dup_tier: "review", dup_ref: null,
      name: "Dr. Thin", org_name: null, vertical: null, city: null, county: null, state: null,
      email: null, phone: null, segment: null, est_lease_event: null, est_basis: null,
    }] };
    if (sql.startsWith("insert into party")) { this.partyInserts++; return { rows: [{ id: "party-id" }] }; }
    throw new Error("unexpected query: " + sql);
  }
}

test("promote-pool refuses a pool row without sourced contact research before party creation", async () => {
  const db = new PoolFake();
  await assert.rejects(() => TOOLS["promote-pool"].handler(db, joe, {
    idempotency_key: "00000571-0000-4000-8000-000000000217", pool_id: "pool-id", base_version: 1, stage: "new",
  }), e => e instanceof ToolError && e.payload.error === "research_evidence_required" && e.payload.gate === "promote-pool");
  assert.equal(db.partyInserts, 0);
});
