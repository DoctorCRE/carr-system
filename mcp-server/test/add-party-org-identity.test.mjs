// The savepoint discipline matters: after a violation the transaction is
// aborted (25P02) until rolled back, and this fake ENFORCES that, so any
// query issued between the violation and the rollback fails the test.

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  newParty: "20000000-0000-0000-0000-000000000001",
  employerOrg: "20000000-0000-0000-0000-000000000002",
  survivor: "20000000-0000-0000-0000-000000000003",
  deal: "30000000-0000-0000-0000-000000000001",
  building: "50000000-0000-0000-0000-000000000001",
  space: "50000000-0000-0000-0000-000000000002",
  premises: "50000000-0000-0000-0000-000000000003",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "mcp", client_id: "claude" };

function orgKey(s) {
  if (s == null) return null;
  const t = String(s).trim().replace(/\s+/g, " ").toLowerCase();
  return t === "" ? null : t;
}

class Fake {
  constructor({ similar = [], survivors = [], insertViolates = false } = {}) {
    this.similar = similar;
    this.survivors = survivors;
    this.insertViolates = insertViolates;
    this.aborted = false;          // 25P02 discipline: poisoned until rollback to savepoint
    this.partyInserts = [];
    this.orgPartyIdCalls = [];
    this.identityCompares = 0;
    this.savepoints = [];
    this.rollbacks = [];
    this.toolCalls = [];
    this.events = [];
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();

    if (this.aborted && !/^rollback to savepoint/i.test(sql)) {
      const e = new Error("current transaction is aborted, commands ignored until end of transaction block");
      e.code = "25P02";
      throw e;
    }

    if (sql.startsWith("select request_hash, response")) return { rows: [] };

    if (sql.includes("similarity(name,$2)")) return { rows: this.similar };
    if (sql.includes("similarity(name, $1)")) return { rows: this.similar };

    if (sql.startsWith("select org_identity_key($1) = org_identity_key($2)")) {
      this.identityCompares++;
      const [a, b] = [orgKey(params[0]), orgKey(params[1])];
      return { rows: [{ same_org: a !== null && a === b }] };
    }

    if (sql.startsWith("select org_party_id($1,$2)")) {
      this.orgPartyIdCalls.push(params);
      return { rows: [{ id: ids.employerOrg }] };
    }

    if (/^savepoint\s/i.test(sql)) { this.savepoints.push(sql); return { rows: [] }; }
    if (/^rollback to savepoint\s/i.test(sql)) {
      this.rollbacks.push(sql); this.aborted = false; return { rows: [] };
    }

    if (sql.startsWith("insert into party")) {
      this.partyInserts.push(params);
      if (this.insertViolates) {
        this.aborted = true;
        const e = new Error(`duplicate key value violates unique constraint "party_org_identity_uniq"`);
        e.code = "23505";
        e.constraint = "party_org_identity_uniq";
        e.detail = "Key (org_identity_key(name))=(wagtail lodge resort) already exists.";
        throw e;
      }
      return { rows: [{ id: ids.newParty }] };
    }
    if (sql.startsWith("insert into record_flag")) return { rows: [] };

    if (sql.includes("org_identity_key(name) = org_identity_key($1)")) return { rows: this.survivors };

    if (sql.includes("from v_ref_index where subject_id=$1"))
      return { rows: [{ subject_type: "deal", subject_id: ids.deal }] };
    if (sql.includes("from building where lower(address)")) return { rows: [] };
    if (sql.startsWith("insert into building")) return { rows: [{ id: ids.building }] };
    if (sql.startsWith("insert into space")) return { rows: [{ id: ids.space }] };
    if (sql.startsWith("insert into premises_space")) return { rows: [] };
    if (sql.startsWith("insert into premises")) return { rows: [{ id: ids.premises }] };
    if (sql.startsWith("insert into building_ownership")) return { rows: [] };
    if (sql.includes("from deal_participant")) return { rows: [] };
    if (sql.startsWith("insert into deal_participant")) return { rows: [] };

    if (sql.startsWith("insert into event")) { this.events.push(params); return { rows: [] }; }
    if (sql.startsWith("insert into tool_call")) { this.toolCalls.push(params); return { rows: [] }; }

    throw new Error("fake received unexpected SQL: " + sql);
  }
}

const evidenceFor = fields => ({
  sources: [{ url: "https://example.test/identity", observed_at: "2026-08-16T12:00:00Z" }],
  field_evidence: Object.fromEntries(fields.map(field => [field, [0]])), discrepancies: [],
});
const PARTY_FIELDS = ["name", "company", "phone", "specialty", "market"];

const addParty = (c, args) => TOOLS["add-party"].handler(c, joe, {
  idempotency_key: "11111111-1111-1111-1111-111111111111",
  research_evidence: evidenceFor(PARTY_FIELDS),
  ...args });

const partyEvidence = evidenceFor(PARTY_FIELDS);


test("org with org_name restating itself creates exactly one row (the Ruff House defect)", async () => {
  const c = new Fake();
  const res = await addParty(c, { name: "Wagtail Lodge Resort", kind: "org",
    org_name: "Wagtail Lodge Resort", city: "Panama City Beach", state: "FL" });
  assert.equal(res.ok, true);
  assert.equal(res.party_id, ids.newParty);
  assert.equal(c.orgPartyIdCalls.length, 0, "org_party_id must not mint the org a second time");
  assert.equal(c.partyInserts.length, 1, "exactly one party row");
  assert.equal(c.partyInserts[0][2], null, "org_id stays null — an org is not its own employer");
  assert.ok(res.note, "the response says org_name was ignored, so the caller learns");
  assert.equal(c.toolCalls.length, 1, "the accepted write lands its tool_call row");
});

test("self-reference is caught by IDENTITY, not string equality", async () => {
  const c = new Fake();
  const res = await addParty(c, { name: "Wagtail Lodge Resort", kind: "org",
    org_name: "  WAGTAIL  lodge   RESORT " });
  assert.equal(res.ok, true);
  assert.equal(c.orgPartyIdCalls.length, 0);
  assert.equal(c.partyInserts.length, 1);
  assert.ok(c.identityCompares >= 1, "the comparison went through org_identity_key in SQL");
});


test("org with a genuinely different org_name still links the parent org", async () => {
  const c = new Fake();
  const res = await addParty(c, { name: "Wagtail Lodge Resort", kind: "org",
    org_name: "Ruff House Holdings LLC" });
  assert.equal(res.ok, true);
  assert.equal(c.orgPartyIdCalls.length, 1, "parent/sub-org structure stays expressible");
  assert.equal(c.partyInserts.length, 1);
  assert.equal(c.partyInserts[0][2], ids.employerOrg, "org_id carries the parent");
  assert.equal(res.note, undefined);
});

test("person with an org_name matching their own name is untouched by the guard", async () => {
  const c = new Fake();
  const res = await addParty(c, { name: "Joe Bookout", kind: "person", org_name: "Joe Bookout" });
  assert.equal(res.ok, true);
  assert.equal(c.orgPartyIdCalls.length, 1, "the person's employer org is still find-or-created");
  assert.equal(c.identityCompares, 0, "the self-reference guard is org-kind only");
  assert.equal(c.partyInserts[0][2], ids.employerOrg);
});

test("similarity guard still answers needs_confirm before any insert", async () => {
  const c = new Fake({ similar: [{ id: ids.survivor, name: "Wagtail Lodge Resort", email: null, city: "PCB" }] });
  const res = await addParty(c, { name: "Wagtail Lodge Resort", kind: "org" });
  assert.equal(res.needs_confirm, true);
  assert.equal(c.partyInserts.length, 0);
});


test("a genuine identity collision surfaces the surviving row, not a raw unique_violation", async () => {
  const survivor = { id: ids.survivor, name: "Wagtail Lodge Resort", email: null, city: "Panama City Beach" };
  const c = new Fake({ insertViolates: true, survivors: [survivor] });
  const res = await addParty(c, { name: "Wagtail Lodge Resort", kind: "org", force_new: true });
  assert.equal(res.needs_confirm, true);
  assert.deepEqual(res.candidates, [survivor]);
  assert.match(res.hint, /disambiguat/i, "the hint teaches the doctrine: fix the NAME, never the key");
  assert.equal(c.savepoints.length, 1, "the org insert ran under a savepoint");
  assert.equal(c.rollbacks.length, 1, "the aborted transaction was rolled back to it");
});

test("a person insert takes no savepoint — the index cannot bite persons", async () => {
  const c = new Fake();
  const res = await addParty(c, { name: "Dr. New Person", kind: "person" });
  assert.equal(res.ok, true);
  assert.equal(c.savepoints.length, 0);
});


test("add-premises new_party org restating itself in org_name creates one row", async () => {
  const c = new Fake();
  const res = await TOOLS["add-premises"].handler(c, joe, {
    idempotency_key: "22222222-2222-2222-2222-222222222222",
    deal_ref: ids.deal, label: "123 Front Beach Rd",
    building: { address: "123 Front Beach Rd" },
    spaces: [{ suite: "A" }],
    ownership: [{ kind: "owner",
      new_party: { name: "Wagtail Lodge Resort", kind: "org", org_name: "Wagtail Lodge Resort",
        research_evidence: partyEvidence } }],
  });
  assert.equal(res.ok, true);
  assert.equal(c.orgPartyIdCalls.length, 0, "no second minting of the same org");
  assert.equal(c.partyInserts.length, 1);
  assert.equal(c.partyInserts[0][2], null);
});

test("add-premises new_party org collision surfaces the survivor as needs_confirm", async () => {
  const survivor = { id: ids.survivor, name: "Wagtail Lodge Resort", email: null, city: "PCB" };
  const c = new Fake({ insertViolates: true, survivors: [survivor] });
  await assert.rejects(
    TOOLS["add-premises"].handler(c, joe, {
      idempotency_key: "33333333-3333-3333-3333-333333333333",
      deal_ref: ids.deal, label: "123 Front Beach Rd",
      building: { address: "123 Front Beach Rd" },
      spaces: [{ suite: "A" }],
      ownership: [{ kind: "owner",
        new_party: { name: "Wagtail Lodge Resort", kind: "org", force_new: true,
          research_evidence: partyEvidence } }],
    }),
    (e) => e.payload?.error === "needs_confirm" &&
           Array.isArray(e.payload.candidates) && e.payload.candidates[0].id === ids.survivor);
  assert.equal(c.rollbacks.length, 1, "add-premises' insert is savepoint-guarded too");
});


test("a single exact-email candidate auto-resolves without creating a duplicate", async () => {
  const existing = { id: ids.survivor, name: "Dana Alt", email: "dana@example.test", city: "Mobile",
    exact_email_match: true };
  const c = new Fake({ similar: [existing] });
  const res = await addParty(c, { name: "Dana Altman", email: "Dana@Example.test" });
  assert.equal(res.ok, true);
  assert.equal(res.party_id, ids.survivor);
  assert.equal(res.auto_resolved, true);
  assert.equal(res.needs_confirm, undefined);
  assert.equal(c.partyInserts.length, 0, "no duplicate party is created");
});

test("force_new:true still creates a separate party even over an exact email match", async () => {
  const existing = { id: ids.survivor, name: "Dana Alt", email: "dana@example.test", city: "Mobile",
    exact_email_match: true };
  const c = new Fake({ similar: [existing] });
  const res = await addParty(c, { name: "Dana Altman", email: "dana@example.test", force_new: true });
  assert.equal(res.ok, true);
  assert.equal(res.auto_resolved, undefined);
  assert.equal(c.partyInserts.length, 1, "force_new bypasses the candidate lookup entirely, as before");
});

test("multiple candidates never auto-resolve even when one is an exact email match", async () => {
  const first = { id: ids.survivor, name: "Dana Alt", email: "dana@example.test", city: "Mobile",
    exact_email_match: true };
  const second = { id: ids.employerOrg, name: "Dana Altmann", email: null, city: "Mobile",
    exact_email_match: false };
  const c = new Fake({ similar: [first, second] });
  const res = await addParty(c, { name: "Dana Altman", email: "dana@example.test" });
  assert.equal(res.needs_confirm, true);
  assert.deepEqual(res.candidates, [first, second]);
  assert.equal(c.partyInserts.length, 0);
});

test("a single fuzzy-name-only candidate (no exact email match) still asks — ambiguity is never auto-resolved", async () => {
  const nameOnly = { id: ids.survivor, name: "Wagtail Lodge Resort", email: null, city: "PCB",
    exact_email_match: false };
  const c = new Fake({ similar: [nameOnly] });
  const res = await addParty(c, { name: "Wagtail Lodge Resort", kind: "org" });
  assert.equal(res.needs_confirm, true);
  assert.deepEqual(res.candidates, [nameOnly]);
  assert.equal(c.partyInserts.length, 0);
});

test("add-premises refuses a thin new ownership party before its party insert", async () => {
  const c = new Fake();
  await assert.rejects(
    TOOLS["add-premises"].handler(c, joe, {
      idempotency_key: "44444444-4444-4444-4444-444444444444",
      deal_ref: ids.deal, label: "123 Front Beach Rd",
      building: { address: "123 Front Beach Rd" }, spaces: [{ suite: "A" }],
      ownership: [{ kind: "owner", new_party: { name: "Unresearched Owner", kind: "person" } }],
    }),
    e => e.payload?.error === "research_evidence_required" && e.payload.gate === "add-premises.new_party");
  assert.equal(c.partyInserts.length, 0);
});
