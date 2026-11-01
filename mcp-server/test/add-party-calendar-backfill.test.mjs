import { test } from "node:test";
import assert from "node:assert/strict";
import { Pool } from "@neondatabase/serverless";

// Only synthetic credentials enter this isolated test process before server boot.
process.env.LOCAL_TOKENS = JSON.stringify({ "joe-local": "synthetic-loader-token" });
const { agentActorForToken } = await import("../src/identity.js");
const { executeRegisteredTool } = await import("../src/tools.js");
const { dispatch } = await import("./helpers/budgeted-dispatch.mjs");
const { CALENDAR_BACKFILL_SCOPE, CALENDAR_BACKFILL_DIGESTS } = await import("../src/calendar-backfill-admissions.js");
const partyId = "20000000-0000-0000-0000-000000000001";
const actorId = "10000000-0000-4000-8000-000000000002";
const key = "00000637-0000-4000-8000-000000000008";
const base = { idempotency_key: key, name: "Robin Vale", email: "robin.vale@firm-z.test" };
const local = () => Object.assign(agentActorForToken("Bearer synthetic-loader-token", process.env.LOCAL_TOKENS, "local-token"), { id: actorId });
const ordinary = () => ({ id: actorId, slug: "codex", human: false, via: "agent-token" });

class Fake {
  constructor(similar = []) { this.similar = similar; this.flags = []; this.events = []; this.partyInserts = []; this.receipt = null; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: this.receipt ? [this.receipt] : [] };
    if (sql.includes("similarity(name,$2)")) return { rows: this.similar };
    if (sql.startsWith("insert into party")) { this.partyInserts.push(params); return { rows: [{ id: partyId }] }; }
    if (sql.startsWith("insert into record_flag")) { this.flags.push({ sql, params }); return { rows: [] }; }
    if (sql.startsWith("insert into event")) { this.events.push(params); return { rows: [] }; }
    if (sql.startsWith("insert into tool_call")) { this.receipt = { request_hash: params[3], response: params[4] }; return { rows: [] }; }
    if (/^select id, address, city from building/.test(sql)) return { rows: [] };
    if (/^insert into (building|space|premises) /.test(sql)) return { rows: [{ id: partyId }] };
    if (/^insert into premises_space/.test(sql)) return { rows: [] };
    if (sql.includes("from v_ref_index")) return { rows: [{ subject_type: "deal", subject_id: partyId, ref: "SYNTHETIC" }] };
    if (sql.startsWith("select id from actor")) return { rows: [{ id: actorId }] };
    if (/select session_user/.test(sql)) return { rows: [{ session_principal: "carr_writer", current_principal: "carr_writer", privilege_bundle: "carr_writer", member_carr_writer: true, member_carr_jobs: false, member_carr_authority: false, backend_pid: 123 }] };
    if (/^(begin|commit|rollback|select set_config)/.test(sql)) return { rows: [] };
    throw new Error("unhandled fake query: " + sql);
  }
  release() {}
}
const add = (db, actor, extra = {}) => executeRegisteredTool(db, actor, "add-party", { ...base, ...extra });

// A synthetic tuple stands in for one private allowlist preimage. Real hashing
// remains in use for every other byte sequence, including modified identities.
function admitSynthetic(t) {
  const real = crypto.subtle.digest.bind(crypto.subtle);
  const bytes = JSON.stringify([CALENDAR_BACKFILL_SCOPE, base.name, base.email]);
  t.mock.method(crypto.subtle, "digest", (algorithm, data) =>
    new TextDecoder().decode(data) === bytes
      ? Promise.resolve(Uint8Array.from(Buffer.from(CALENDAR_BACKFILL_DIGESTS[0], "hex")).buffer)
      : real(algorithm, data));
}

test("public calendar_attestation no longer exists at registered execution", async () => {
  await assert.rejects(add(new Fake(), ordinary(), { calendar_attestation: "calendar_attendee" }),
    e => e.payload?.error === "unregistered_operation_fields");
});

for (const name of ["Robin", "R Vale", "R. Vale", "R V", "R. V.", "Robin V."])
  test(`registered research-free creation refuses partial name ${name}`, async () => {
    const db = new Fake();
    await assert.rejects(add(db, local(), { name }), e => e.payload?.error === "full_person_name_required");
    assert.equal(db.partyInserts.length, 0);
  });

test("only authenticated attended local authority can use a frozen admission", async t => {
  admitSynthetic(t);
  const db = new Fake();
  const out = await add(db, local());
  assert.equal(out.calendar_attested, true);
  assert.equal(db.partyInserts.length, 1);
  assert.match(db.flags[0].sql, /'calendar_attendee'/);
  assert.doesNotMatch(db.flags[0].sql, /'verified'/);
  assert.match(db.flags[0].params[1], /calendar_attested/);
  assert.ok(db.flags[0].params[2].includes(CALENDAR_BACKFILL_SCOPE));
  for (const actor of [ordinary(), { ...local() }, Object.assign(local(), { unattended: true }),
    Object.assign(local(), { via: "unattended-token" }), Object.assign(local(), { slug: "joe" }),
    Object.assign(local(), { operational_profile: "read" }), Object.assign(local(), { operational_profile: "unattended" })]) {
    await assert.rejects(add(new Fake(), actor), e => e.payload?.error === "research_evidence_required");
  }
});

test("admission binds exact name, email, and create fields", async t => {
  admitSynthetic(t);
  for (const extra of [{ name: "Robyn Vale" }, { email: "other@firm-z.test" }])
    await assert.rejects(add(new Fake(), local(), extra), e => e.payload?.error === "research_evidence_required");
  for (const extra of [{ force_new: true }, { org_name: "Synthetic Firm" }, { phone: "123" }])
    await assert.rejects(add(new Fake(), local(), extra), e => e.payload?.error === "calendar_backfill_inputs_refused");
});

test("a similar name still blocks an admitted calendar attendee", async t => {
  admitSynthetic(t);
  const db = new Fake([{ id: partyId, name: "Robin Vales", exact_email_match: false }]);
  assert.equal((await add(db, local())).needs_confirm, true);
  assert.equal(db.partyInserts.length, 0);
});

async function transport(t, actor, extra = {}, passthrough = false, verb = "add-party") {
  const db = new Fake();
  t.mock.method(console, "error", () => {});
  t.mock.method(Pool.prototype, "connect", async () => db);
  t.mock.method(Pool.prototype, "end", async () => {});
  const args = verb === "add-party" ? { ...base, ...extra } : { idempotency_key: key, ...extra };
  const request = new Request("https://synthetic.example/mcp", { method: "POST", body: JSON.stringify({
    jsonrpc: "2.0", id: 1, method: "tools/call", params: passthrough
      ? { name: "call-verb", arguments: { verb, args } }
      : { name: verb, arguments: args },
  }) });
  const body = await (await dispatch(request, {}, {}, actor)).json();
  return { payload: JSON.parse(body.result.content[0].text), isError: body.result.isError, db };
}

for (const passthrough of [false, true]) {
  for (const [code, extra] of [
    ["research_evidence_required", {}],
    ["research_evidence_incomplete", { research_evidence: { sources: [], field_evidence: {}, discrepancies: [] } }],
    ["research_source_invalid", { research_evidence: { sources: [{ url: "invalid", observed_at: "2026-01-01" }], field_evidence: {}, discrepancies: [] } }],
    ["full_person_name_required", { name: "R. Vale" }],
    ["unregistered_operation_fields", { calendar_attestation: "calendar_attendee" }],
  ]) test(`transport ${passthrough ? "call-verb" : "direct"} exposes safe ${code}`, async t => {
    const { payload, db } = await transport(t, ordinary(), extra, passthrough);
    assert.equal(payload.error, code);
    assert.ok(payload.correlation_id);
    assert.doesNotMatch(JSON.stringify(payload), /robin|firm-z|stack|SELECT/i);
    assert.equal(db.partyInserts.length, 0);
  });
  test(`transport ${passthrough ? "call-verb" : "direct"} enforces admission`, async t => {
    admitSynthetic(t);
    const accepted = await transport(t, local(), {}, passthrough);
    assert.equal(accepted.payload.calendar_attested, true);
    assert.equal(accepted.db.partyInserts.length, 1);
    const denied = await transport(t, ordinary(), {}, passthrough);
    assert.equal(denied.payload.error, "research_evidence_required");
    const inputs = await transport(t, local(), { force_new: true }, passthrough);
    assert.equal(inputs.payload.error, "calendar_backfill_inputs_refused");
  });
}


test("registered calendar admission replays without another insert and binds identity", async t => {
  admitSynthetic(t);
  const db = new Fake();
  await add(db, local());
  assert.equal((await add(db, local())).replayed, true);
  assert.equal(db.partyInserts.length, 1);
  await assert.rejects(add(db, local(), { email: "changed@firm-z.test" }), e => e.payload?.error === "key_reuse");
});

const research = {
  sources: [{ url: "https://synthetic.example/team", observed_at: "2026-01-01T00:00:00Z" }],
  field_evidence: Object.fromEntries(["name", "company", "phone", "specialty", "market"].map(field => [field, [0]])),
  discrepancies: [],
};
test("ordinary researched person creation retains its verified stamp through transport", async t => {
  const { payload, db } = await transport(t, ordinary(), { research_evidence: research });
  assert.equal(payload.ok, true);
  assert.equal(db.partyInserts.length, 1);
  assert.match(db.flags[0].sql, /'verified'/);
  assert.doesNotMatch(db.flags[0].sql, /'calendar_attendee'/);
});

test("transport preserves existing field_required without exposing supplied text", async t => {
  const { payload } = await transport(t, ordinary(), { deal: "SYNTHETIC", field: " " }, false, "presence-lease");
  assert.equal(payload.error, "field_required");
  assert.doesNotMatch(JSON.stringify(payload), /robin|firm-z|stack|SELECT/i);
});


test("transport preserves existing conflicting_party_inputs safely", async t => {
  const { payload, db } = await transport(t, ordinary(), {
    deal_ref: "SYNTHETIC", label: "Synthetic premises", building: { address: "Synthetic building" },
    spaces: [{ suite: "Synthetic suite" }],
    ownership: [{ kind: "owner", party_ref: "SYNTHETIC", new_party: { name: "Synthetic Person" } }],
  }, false, "add-premises");
  assert.equal(payload.error, "conflicting_party_inputs");
  assert.equal(db.partyInserts.length, 0);
  assert.doesNotMatch(JSON.stringify(payload), /robin|firm-z|stack|SELECT/i);
});
