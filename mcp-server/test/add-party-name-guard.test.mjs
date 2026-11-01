import { test } from "node:test";
import assert from "node:assert/strict";
import { Pool } from "@neondatabase/serverless";
import { executeRegisteredTool } from "../src/tools.js";
import { dispatch } from "./helpers/budgeted-dispatch.mjs";

const partyId = "20000000-0000-0000-0000-000000000001";
const actor = { id: "10000000-0000-4000-8000-000000000002", slug: "codex", human: false, via: "agent-token" };
const base = { idempotency_key: "00000637-0000-4000-8000-000000000013", name: "Robin Vale", email: "robin.vale@firm-z.test" };
const research = {
  sources: [{ url: "https://synthetic.example/team", observed_at: "2026-01-01T00:00:00Z" }],
  field_evidence: Object.fromEntries(["name", "company", "phone", "specialty", "market"].map(field => [field, [0]])),
  discrepancies: [],
};

class Fake {
  constructor() { this.inserts = []; this.flags = []; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response") || sql.includes("similarity(name,$2)")) return { rows: [] };
    if (sql.startsWith("insert into party")) { this.inserts.push(params); return { rows: [{ id: partyId }] }; }
    if (sql.startsWith("insert into record_flag")) { this.flags.push({ sql, params }); return { rows: [] }; }
    if (/^insert into (event|tool_call) /.test(sql)) return { rows: [] };
    if (/^select id, address, city from building/.test(sql)) return { rows: [] };
    if (/^insert into (building|space|premises) /.test(sql)) return { rows: [{ id: partyId }] };
    if (/^insert into premises_space/.test(sql)) return { rows: [] };
    if (sql.includes("from v_ref_index")) return { rows: [{ subject_type: "deal", subject_id: partyId, ref: "SYNTHETIC" }] };
    if (sql.startsWith("select id from actor")) return { rows: [{ id: actor.id }] };
    if (/select session_user/.test(sql)) return { rows: [{ session_principal: "carr_writer", current_principal: "carr_writer", privilege_bundle: "carr_writer", member_carr_writer: true, member_carr_jobs: false, member_carr_authority: false, backend_pid: 123 }] };
    if (/^(begin|commit|rollback|savepoint|release savepoint|select set_config)/.test(sql)) return { rows: [] };
    throw new Error("unhandled fake query: " + sql);
  }
  release() {}
}

for (const researched of [false, true]) {
  for (const name of ["Robin", "R Vale", "R. Vale", "R V", "R. V.", "Robin V.", "R.V.", "Robin-Vale", "Robin.Vale", "robin@firm.test"]) {
    for (const force_new of [false, true]) test(`registered ${researched ? "researched" : "unresearched"} create refuses ${name}, force_new=${force_new}`, async () => {
      const db = new Fake();
      await assert.rejects(executeRegisteredTool(db, actor, "add-party", {
        ...base, name, force_new, ...(researched ? { research_evidence: research } : {}),
      }), e => e.payload?.error === "full_person_name_required");
      assert.deepEqual(db.inserts, []);
      assert.deepEqual(db.flags, []);
    });
  }
}

for (const name of ["Robin Vale", "Robin Q. Vale", "Élodie Dubois", "Anne-Marie Vale"]) test(`registered researched create accepts ${name}`, async () => {
  const db = new Fake();
  const out = await executeRegisteredTool(db, actor, "add-party", { ...base, name, research_evidence: research });
  assert.equal(out.ok, true);
  assert.equal(db.inserts.length, 1);
  assert.equal(db.flags.length, 1);
  assert.match(db.flags[0].sql, /'verified'/);
});

test("registered full name still requires research", async () => {
  const db = new Fake();
  await assert.rejects(executeRegisteredTool(db, actor, "add-party", base), e => e.payload?.error === "research_evidence_required");
  assert.deepEqual(db.inserts, []);
});

test("registered one-word organisation remains valid without person research", async () => {
  const db = new Fake();
  assert.equal((await executeRegisteredTool(db, actor, "add-party", { ...base, kind: "org", name: "Synthetic" })).ok, true);
  assert.equal(db.flags.length, 0);
});

async function transport(t, passthrough, verb, extra) {
  const db = new Fake();
  t.mock.method(console, "error", () => {});
  t.mock.method(Pool.prototype, "connect", async () => db);
  t.mock.method(Pool.prototype, "end", async () => {});
  const args = verb === "add-party" ? { ...base, ...extra } : { idempotency_key: base.idempotency_key, ...extra };
  const request = new Request("https://synthetic.example/mcp", { method: "POST", body: JSON.stringify({
    jsonrpc: "2.0", id: 1, method: "tools/call", params: passthrough
      ? { name: "call-verb", arguments: { verb, args } } : { name: verb, arguments: args },
  }) });
  const body = await (await dispatch(request, {}, {}, actor)).json();
  assert.equal(body.result.isError, true);
  const payload = JSON.parse(body.result.content[0].text);
  assert.deepEqual(Object.keys(payload).sort(), payload.error === "unregistered_operation_fields"
    ? ["correlation_id", "error", "fields", "hint"] : ["correlation_id", "error", "hint"]);
  if (payload.fields) assert.deepEqual(payload.fields, ["calendar_attestation"]);
  assert.match(payload.correlation_id, /^[a-f0-9-]{36}$/);
  assert.ok(payload.hint);
  assert.doesNotMatch(JSON.stringify(payload), /robin|firm-z|synthetic|stack|SELECT/i);
  assert.deepEqual(db.inserts, []);
  assert.deepEqual(db.flags, []);
  return payload;
}

for (const passthrough of [false, true]) {
  for (const [code, verb, extra] of [
    ["research_evidence_required", "add-party", {}],
    ["research_evidence_incomplete", "add-party", { research_evidence: { sources: [], field_evidence: {}, discrepancies: [] } }],
    ["research_source_invalid", "add-party", { research_evidence: { ...research, sources: [{ url: "invalid", observed_at: "2026-01-01" }] } }],
    ["full_person_name_required", "add-party", { name: "R. Vale", research_evidence: research }],
    ["unregistered_operation_fields", "add-party", { calendar_attestation: "calendar_attendee" }],
    ["field_required", "presence-lease", { deal: "SYNTHETIC", field: " " }],
    ["conflicting_party_inputs", "add-premises", {
      deal_ref: "SYNTHETIC", label: "Synthetic premises", building: { address: "Synthetic building" },
      spaces: [{ suite: "Synthetic suite" }],
      ownership: [{ kind: "owner", party_ref: "SYNTHETIC", new_party: { name: "Synthetic Person" } }],
    }],
  ]) test(`transport ${passthrough ? "call-verb" : "direct"} exposes ${code}`, async t => {
    assert.equal((await transport(t, passthrough, verb, extra)).error, code);
  });
}
