// The intake rules are gates at the record-creating verbs, not reminders in a
// runbook.  A client/vendor cannot exist first and be researched "later".
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "test" };
const party = "20000000-0000-0000-0000-000000000001";

class Fake {
  constructor() { this.flags = []; this.inserts = []; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("insert into record_flag")) { this.flags.push(params); return { rows: [] }; }
    if (sql.includes("nextval('ref_client_seq')")) return { rows: [{ r: "C-999" }] };
    if (sql.includes("nextval('ref_vendor_seq')")) return { rows: [{ r: "V-CPA-999" }] };
    if (sql.startsWith("select slug from vendor_stage")) return { rows: [{ slug: params[0] }] };
    if (sql.startsWith("insert into client")) { this.inserts.push("client"); return { rows: [{ id: "client-id" }] }; }
    if (sql.startsWith("insert into vendor")) { this.inserts.push("vendor"); return { rows: [{ id: "vendor-id" }] }; }
    if (sql.startsWith("insert into event") || sql.startsWith("insert into tool_call")) return { rows: [] };
    throw new Error("unhandled fake query: " + sql);
  }
}

const evidenceFor = (fields, url = "https://practice.example/about") => ({
  sources: [{ url, observed_at: "2026-08-16T12:00:00Z" }],
  field_evidence: Object.fromEntries(fields.map(field => [field, [0]])), discrepancies: [],
});
const CLIENT_FIELDS = ["practice_name", "address", "phone", "specialty", "practitioners", "hours"];
const VENDOR_FIELDS = ["company", "title", "category", "market", "phone", "website", "deal_side"];
const clientEvidence = evidenceFor(CLIENT_FIELDS);
const vendorEvidence = evidenceFor(VENDOR_FIELDS, "https://firm.example/team");

test("new-client refuses before any write without an open-source research stamp", async () => {
  const db = new Fake();
  await assert.rejects(() => TOOLS["new-client"].handler(db, joe, {
    idempotency_key: "00000571-0000-4000-8000-000000000142", party_id: party, status: "active", acquisition_source: "referral",
  }), e => e instanceof ToolError && e.payload.error === "research_evidence_required");
  assert.deepEqual(db.inserts, []);
  assert.deepEqual(db.flags, []);
});

test("new-client records the verified research evidence before the client row", async () => {
  const db = new Fake();
  const out = await TOOLS["new-client"].handler(db, joe, {
    idempotency_key: "00000571-0000-4000-8000-000000000143", party_id: party, status: "active", acquisition_source: "referral", research_evidence: clientEvidence,
  });
  assert.equal(out.ok, true);
  assert.equal(db.flags.length, 1);
  assert.deepEqual(db.inserts, ["client"]);
  assert.match(JSON.stringify(db.flags[0]), /practice_name/);
});

test("new-vendor refuses incomplete evidence rather than minting a thin vendor", async () => {
  const db = new Fake();
  await assert.rejects(() => TOOLS["new-vendor"].handler(db, joe, {
    idempotency_key: "00000571-0000-4000-8000-000000000144", party_id: party, category: "cpa", ref_code: "CPA", stage: "prospect_uncontacted",
    research_evidence: evidenceFor(["company"], "https://firm.example/identity"),
  }), e => e instanceof ToolError && e.payload.error === "research_evidence_incomplete");
  assert.deepEqual(db.inserts, []);
  assert.deepEqual(db.flags, []);
});

test("new-vendor refuses self-attested source strings before any write", async () => {
  const db = new Fake();
  await assert.rejects(() => TOOLS["new-vendor"].handler(db, joe, {
    idempotency_key: "00000571-0000-4000-8000-000000000145", party_id: party, category: "cpa", ref_code: "CPA",
    stage: "prospect_uncontacted", research_evidence: {
      sources: ["trust me"], field_evidence: Object.fromEntries(VENDOR_FIELDS.map(f => [f, [0]])),
      discrepancies: [],
    },
  }), e => e instanceof ToolError && e.payload.error === "research_source_invalid");
  assert.deepEqual(db.inserts, []);
  assert.deepEqual(db.flags, []);
});

test("new-vendor writes research provenance before the vendor row", async () => {
  const db = new Fake();
  const out = await TOOLS["new-vendor"].handler(db, joe, {
    idempotency_key: "00000571-0000-4000-8000-000000000146", party_id: party, category: "cpa", ref_code: "CPA", stage: "prospect_uncontacted", research_evidence: vendorEvidence,
  });
  assert.equal(out.ok, true);
  assert.equal(db.flags.length, 1);
  assert.deepEqual(db.inserts, ["vendor"]);
});
