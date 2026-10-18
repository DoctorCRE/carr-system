import test from "node:test";
import assert from "node:assert/strict";

import { agentProfileTools } from "../src/agent-profiles.js";
import { ToolError } from "../src/tool-error.js";

async function withEnvelope(_c, _actor, _verb, args, fn) {
  if (!args.idempotency_key) throw new ToolError({ error: "missing_idempotency_key" });
  return fn();
}
const writeEvents = [];
async function writeEvent(_c, _actor, verb, subjectType, subjectId, fields) {
  writeEvents.push({ verb, subjectType, subjectId, fields });
}

const TOOLS = agentProfileTools({ withEnvelope, writeEvent, ToolError });

const KEY = "0284aaaa-0000-4000-8000-000000000001";

function profileRow(overrides = {}) {
  return {
    id: "profile-uuid", profile_key: "builder", display_name: "Builder",
    charter: ["implementation"], current_model: null, current_desk: null,
    sponsor_scope: "shared", status: "unstaffed", version: "1", ...overrides,
  };
}

test("both verbs are registered, and only assignment writes", () => {
  assert.ok(TOOLS["read-profiles"]);
  assert.ok(TOOLS["assign-profile"]);
  assert.notEqual(TOOLS["read-profiles"].write, true, "reading the roster is not a write");
  assert.equal(TOOLS["assign-profile"].write, true);
  assert.notEqual(TOOLS["assign-profile"].humanOnly, true,
    "assignment is human-or-standing-delegation, gated in the handler, not humanOnly");
});

test("read-profiles returns the roster ordered by key, open to any caller", async () => {
  const client = { query: async (sql) => {
    assert.match(sql, /from agent_profile/);
    assert.match(sql, /order by profile_key/);
    return { rows: [profileRow(), profileRow({ profile_key: "doc", status: "parked" })] };
  } };
  const out = await TOOLS["read-profiles"].handler(client, { slug: "probe", human: false }, {});
  assert.equal(out.ok, true);
  assert.equal(out.total, 2);
  assert.equal(out.profiles[1].profile_key, "doc");
});

test("a human partner assigns directly, and the history row says a human ruled it", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/update agent_profile/.test(sql)) return { rows: [profileRow({ current_model: "opus", status: "active", version: "2" })] };
    if (/insert into agent_profile_assignment/.test(sql)) return { rows: [{ id: "assignment-uuid" }] };
    if (/insert into partner_room_turn/.test(sql)) return { rows: [{ id: "77", at: "now" }] };
    return { rows: [] };
  } };
  const out = await TOOLS["assign-profile"].handler(client,
    { id: "joe-id", slug: "joe", human: true },
    { idempotency_key: KEY, profile_key: "builder", model: "opus", status: "active", base_version: 1 });

  assert.equal(out.ok, true);
  assert.equal(out.profile.status, "active");
  const history = statements.find(s => /insert into agent_profile_assignment/.test(s.sql));
  assert.ok(history, "every assignment writes its history row");
  assert.equal(history.params[4], "joe-id", "ruled_by is the server-derived human actor");
  assert.equal(history.params[5], "human");
});

test("a sponsored delegated session assigns with standing_delegation, attributed to the sponsor", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/retrieval_visibility_actor_id/.test(sql)) return { rows: [{ id: "joe-human-id" }] };
    if (/update agent_profile/.test(sql)) return { rows: [profileRow({ current_model: "sonnet", status: "active", version: "2" })] };
    if (/insert into agent_profile_assignment/.test(sql)) return { rows: [{ id: "assignment-uuid" }] };
    if (/insert into partner_room_turn/.test(sql)) return { rows: [{ id: "78", at: "now" }] };
    return { rows: [] };
  } };
  const out = await TOOLS["assign-profile"].handler(client,
    { id: "runtime-id", slug: "joe-local", human: false, via: "local-token",
      sponsoring_human_slug: "joe", human_slug: "joe", sponsor_required: false },
    { idempotency_key: KEY, profile_key: "builder", model: "sonnet", status: "active", base_version: 1 });

  assert.equal(out.ok, true);
  const history = statements.find(s => /insert into agent_profile_assignment/.test(s.sql));
  assert.equal(history.params[4], "joe-human-id",
    "ruled_by resolves the SPONSOR's human row, never the runtime principal");
  assert.equal(history.params[5], "standing_delegation");
});

test("an unsponsored machine credential is refused", async () => {
  let queried = false;
  const client = { query: async () => { queried = true; return { rows: [] }; } };
  await assert.rejects(
    TOOLS["assign-profile"].handler(client,
      { slug: "unknown-bot", human: false, via: "agent-token", sponsoring_human_slug: null },
      { idempotency_key: KEY, profile_key: "builder", model: "opus", status: "active", base_version: 1 }),
    error => {
      assert.equal(error.payload?.error, "profile_assignment_refused");
      return true;
    });
  assert.equal(queried, false, "refused before any database write");
});

test("a caller-claimed profile can never ride as authority: the verb accepts no actor or authority fields", () => {
  const properties = Object.keys(TOOLS["assign-profile"].inputSchema.properties);
  for (const banned of ["actor", "actor_id", "sponsor", "profile", "authority"])
    assert.equal(properties.includes(banned), false, `schema must not accept ${banned}`);
});

test("a stale base_version is a version_conflict carrying the current version", async () => {
  const client = { query: async (sql) => {
    if (/update agent_profile/.test(sql)) return { rows: [] };
    if (/select .* from agent_profile/.test(sql)) return { rows: [profileRow({ version: "5" })] };
    return { rows: [] };
  } };
  await assert.rejects(
    TOOLS["assign-profile"].handler(client, { id: "joe-id", slug: "joe", human: true },
      { idempotency_key: KEY, profile_key: "builder", model: "opus", status: "active", base_version: 1 }),
    error => {
      assert.equal(error.payload?.error, "version_conflict");
      assert.equal(Number(error.payload?.current_version), 5);
      return true;
    });
});

test("an unknown profile key names itself", async () => {
  const client = { query: async (sql) => {
    if (/update agent_profile/.test(sql)) return { rows: [] };
    if (/select .* from agent_profile/.test(sql)) return { rows: [] };
    return { rows: [] };
  } };
  await assert.rejects(
    TOOLS["assign-profile"].handler(client, { id: "joe-id", slug: "joe", human: true },
      { idempotency_key: KEY, profile_key: "conductor", model: "opus", status: "active", base_version: 1 }),
    error => {
      assert.equal(error.payload?.error, "profile_not_found");
      return true;
    });
});

test("every assignment change posts the wire receipt in the same transaction", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/update agent_profile/.test(sql)) return { rows: [profileRow({ current_model: "opus", status: "active", version: "2" })] };
    if (/insert into agent_profile_assignment/.test(sql)) return { rows: [{ id: "assignment-uuid" }] };
    if (/insert into partner_room_turn/.test(sql)) return { rows: [{ id: "79", at: "now" }] };
    return { rows: [] };
  } };
  const out = await TOOLS["assign-profile"].handler(client, { id: "joe-id", slug: "joe", human: true },
    { idempotency_key: KEY, profile_key: "builder", model: "opus", status: "active", base_version: 1 });

  const receipt = statements.find(s => /insert into partner_room_turn/.test(s.sql));
  assert.ok(receipt, "the wire receipt rides the same transaction as the assignment");
  const body = JSON.parse(receipt.params[4]);
  assert.deepEqual(Object.keys(body), ["agent_profile"]);
  assert.equal(body.agent_profile.key, "builder");
  assert.equal(body.agent_profile.model, "opus");
  assert.equal(body.agent_profile.status, "active");
  assert.equal(receipt.params[6], "mcp");
  assert.equal(receipt.params[7], "joe");
  assert.equal(out.receipt_seq, "79");
});
