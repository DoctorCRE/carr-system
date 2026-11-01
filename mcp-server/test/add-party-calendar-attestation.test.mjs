// A calendar attendee is a person the calendar named. That origin is not research
// verification, and a similar name still blocks the insert.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "mcp", client_id: "test" };
const partyId = "20000000-0000-0000-0000-000000000001";
const key = "00000637-0000-4000-8000-000000000008";

class Fake {
  constructor(similar = []) { this.similar = similar; this.flags = []; this.events = []; this.partyInserts = []; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.includes("similarity(name,$2)")) return { rows: this.similar };
    if (sql.startsWith("insert into party")) { this.partyInserts.push(params); return { rows: [{ id: partyId }] }; }
    if (sql.startsWith("insert into record_flag")) { this.flags.push({ sql, params }); return { rows: [] }; }
    if (sql.startsWith("insert into event")) { this.events.push(params); return { rows: [] }; }
    if (sql.startsWith("insert into tool_call")) return { rows: [] };
    throw new Error("unhandled fake query: " + sql);
  }
}

const add = (db, extra) => TOOLS["add-party"].handler(db, joe, {
  idempotency_key: key, name: "Robin Vale", email: "robin.vale@firm-z.test", ...extra });

test("a calendar attendee is a party with a calendar flag and no verified research stamp", async () => {
  const db = new Fake();
  const out = await add(db, { calendar_attestation: "calendar_attendee" });
  assert.equal(out.calendar_attested, true);
  assert.equal(out.party_id, partyId);
  assert.equal(db.partyInserts.length, 1);
  assert.equal(db.flags.length, 1);
  assert.match(db.flags[0].sql, /'calendar_attendee'/);
  assert.doesNotMatch(db.flags[0].sql, /'verified'/);
  assert.match(db.flags[0].params[1], /calendar_attested/);
  assert.doesNotMatch(db.flags[0].params[1], /source_backed/);
  assert.equal(db.flags[0].params[2], "calendar attendee record");
  assert.match(db.events[0][7], /calendar_attendee/);
});

test("a person with no calendar attestation and no research evidence is not inserted", async () => {
  const db = new Fake();
  await assert.rejects(() => add(db, {}), e => e instanceof ToolError && e.payload.error === "research_evidence_required");
  assert.equal(db.partyInserts.length, 0);
  assert.equal(db.flags.length, 0);
});

test("calendar attestation refuses force_new, research evidence, and an org", async () => {
  for (const extra of [
    { calendar_attestation: "calendar_attendee", force_new: true },
    { calendar_attestation: "calendar_attendee", research_evidence: { sources: [], field_evidence: {}, discrepancies: [] } },
    { calendar_attestation: "calendar_attendee", kind: "org" },
    { calendar_attestation: "researched" },
  ]) {
    const db = new Fake();
    await assert.rejects(() => add(db, extra), e => e instanceof ToolError && e.payload.error === "conflicting_party_inputs");
    assert.equal(db.partyInserts.length, 0);
  }
});

test("calendar attestation requires an email", async () => {
  const db = new Fake();
  await assert.rejects(() => add(db, { calendar_attestation: "calendar_attendee", email: " " }),
    e => e instanceof ToolError && e.payload.error === "field_required");
  assert.equal(db.partyInserts.length, 0);
});

test("a similar name still blocks a calendar attendee", async () => {
  const db = new Fake([{ id: partyId, name: "Robin Vales", email: null, city: null, exact_email_match: false }]);
  const out = await add(db, { calendar_attestation: "calendar_attendee" });
  assert.equal(out.needs_confirm, true);
  assert.equal(out.candidates.length, 1);
  assert.equal(db.partyInserts.length, 0);
  assert.equal(db.flags.length, 0);
});
