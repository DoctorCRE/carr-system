// action-class-successor-registry.test.mjs — unit coverage for V5-D01's three
// verbs (register-action-class-successor, read-action-class-successors,
// read-action-class-gate) against a fake client. The migration's own
// guarantees (CHECK-locked status, immutability trigger, an unconditional
// gate) are proved separately on real PostgreSQL in
// action-class-successor-registry-postgres.test.mjs.
//
// Run with: node --test mcp-server/test/action-class-successor-registry.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { authorizationClassForActor } from "../src/identity.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude" };

const GOOD = {
  idempotency_key: "11111111-1111-1111-1111-111111111111",
  action_class: "salesforce_unattended_write",
  title: "Unattended Salesforce field writes",
  goal: "Let a future, activated automation write qualifying Salesforce fields without a human in the loop.",
  owner: "joe",
  activation_predicate: { requires: ["independent conformance receipt", "partner sign-off"] },
};

class Fake {
  constructor({ existing = false } = {}) {
    this.existing = existing;
    this.writes = [];
  }
  async query(text, params) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select 1 from action_class_successor"))
      return { rows: this.existing ? [{ "?column?": 1 }] : [] };
    if (sql.startsWith("select * from read_action_class_successors")) {
      return { rows: [{ id: "row-1", action_class: params[0] ?? GOOD.action_class,
        title: GOOD.title, goal: GOOD.goal, owner: GOOD.owner,
        policy_requirements: {}, data_requirements: {}, model_requirements: {},
        activation_predicate: GOOD.activation_predicate, status: "inactive",
        actor: "joe", created_at: new Date("2026-09-25T00:00:00Z") }] };
    }
    if (sql.startsWith("select * from action_class_successor_gate")) {
      return { rows: [{ action_class: params[0], allowed: false,
        reason: "no successor registered for this action class", registered: false }] };
    }
    this.writes.push({ sql, params });
    if (sql.startsWith("insert into action_class_successor"))
      return { rows: [{ id: "new-row-1", status: "inactive", created_at: new Date("2026-09-25T00:00:00Z") }] };
    if (sql.startsWith("insert into")) return { rows: [{ id: "event-1" }] };
    return { rows: [] };
  }
}

async function call(verb, args, fake = new Fake()) {
  try { return { fake, result: await TOOLS[verb].handler(fake, joe, args), error: null }; }
  catch (e) { return { fake, result: null, error: e instanceof ToolError ? e.payload : e }; }
}

test("all three verbs are registered with the documented write flags", () => {
  assert.equal(TOOLS["register-action-class-successor"].write, true);
  assert.equal(TOOLS["read-action-class-successors"].write, false);
  assert.equal(TOOLS["read-action-class-gate"].write, false);
});

// REGISTRATION IS A PARTNER ACT (independent review of PR #1290). A row is
// unique, append-only and immutable, and it fixes the accountable owner and
// the activation_predicate for good -- so the first caller must not be just
// any authenticated agent. These go through executeRegisteredTool, the
// deployed dispatch path, not the bare handler, because the humanOnly gate
// lives in the dispatcher.
test("register-action-class-successor is humanOnly; the two reads are not", () => {
  assert.equal(TOOLS["register-action-class-successor"].humanOnly, true);
  assert.notEqual(TOOLS["read-action-class-successors"].humanOnly, true);
  assert.notEqual(TOOLS["read-action-class-gate"].humanOnly, true);
});

const noDatabase = {
  query: async (text) => {
    throw new Error(`the partner-only gate let a call reach the database: ${String(text).slice(0, 80)}`);
  },
};

// Sponsored by joe, but the server has not verified it as a native agent bound
// to joe's authority connection -- an ordinary sponsored agent principal.
const sponsored = { id: "10000000-0000-0000-0000-000000000011", slug: "claude", display: "Claude",
  human: false, sponsoring_human_slug: "joe", native_agent_verified: false,
  via: "oauth-agent", client_id: "claude-client" };

test("register: a sponsored agent without server-verified partner authority is refused before any query", async () => {
  assert.equal(authorizationClassForActor(sponsored), "sponsored_agent");
  const error = await executeRegisteredTool(noDatabase, sponsored, "register-action-class-successor",
    { ...GOOD, idempotency_key: "22222222-2222-4222-8222-222222222222" }).then(() => null, (e) => e);
  assert.ok(error instanceof ToolError, `expected a ToolError, got ${error}`);
  assert.equal(error.payload.error, "human_only_verb_requires_verified_partner");
  assert.equal(error.payload.verb, "register-action-class-successor");
  assert.equal(error.payload.actor_class, "sponsored_agent");
});

test("register: an unsponsored agent token is refused too", async () => {
  const stranger = { id: "10000000-0000-0000-0000-000000000014", slug: "grok", display: "Grok",
    human: false, via: "agent-token" };
  const error = await executeRegisteredTool(noDatabase, stranger, "register-action-class-successor",
    { ...GOOD, idempotency_key: "33333333-3333-4333-8333-333333333333" }).then(() => null, (e) => e);
  assert.equal(error?.payload?.error, "human_only_verb_requires_verified_partner");
  assert.equal(error?.payload?.actor_class, "unsponsored_agent");
});

test("register: the verified human partner still registers through the dispatch path", async () => {
  const fake = new Fake();
  const out = await executeRegisteredTool(fake, { ...joe, via: "oauth" }, "register-action-class-successor",
    { ...GOOD, idempotency_key: "44444444-4444-4444-8444-444444444444" });
  assert.equal(out.ok, true);
  assert.equal(out.status, "inactive");
  assert.equal(out.capability_issued, false);
  const insert = fake.writes.find((w) => w.sql.startsWith("insert into action_class_successor"));
  assert.ok(insert, "the partner's call must reach the insert, not stop at the gate");
  assert.equal(insert.params[8], joe.id);
});

test("reads stay open to a sponsored agent: the gate grants nothing either way", async () => {
  const out = await executeRegisteredTool(new Fake(), sponsored, "read-action-class-gate",
    { action_class: "email_unattended_send" });
  assert.equal(out.allowed, false);
});

test("register: a well-formed entry is inserted with server-derived actor and returns capability_issued:false", async () => {
  const { fake, error, result } = await call("register-action-class-successor", { ...GOOD });
  assert.equal(error, null);
  assert.equal(result.ok, true);
  assert.equal(result.status, "inactive");
  assert.equal(result.capability_issued, false);
  const insert = fake.writes.find((w) => w.sql.startsWith("insert into action_class_successor"));
  assert.ok(insert, "the insert must run");
  assert.equal(insert.params[0], GOOD.action_class);
  assert.equal(insert.params[8], joe.id, "actor_id is always the server-authenticated actor");
});

test("register: action_class must match the slug pattern", async () => {
  for (const bad of ["Salesforce", "1abc", "ab", "has space", "has-dash"]) {
    const { error, fake } = await call("register-action-class-successor", { ...GOOD, action_class: bad });
    assert.equal(error?.error, "action_class_invalid");
    assert.equal(fake.writes.length, 0);
  }
});

test("register: title/goal/owner are required and non-blank", async () => {
  for (const field of ["title", "goal", "owner"]) {
    const { error } = await call("register-action-class-successor", { ...GOOD, [field]: "  " });
    assert.equal(error?.error, `${field}_required`);
  }
});

test("register: activation_predicate must be a plain object", async () => {
  for (const bad of [null, "a string", ["array"], 5]) {
    const { error } = await call("register-action-class-successor", { ...GOOD, activation_predicate: bad });
    assert.equal(error?.error, "invalid_shape");
  }
});

test("register: refused when the action_class is already registered, and nothing is written", async () => {
  const fake = new Fake({ existing: true });
  const { error } = await call("register-action-class-successor", { ...GOOD }, fake);
  assert.equal(error?.error, "action_class_already_registered");
  assert.equal(fake.writes.length, 0, "no insert on a duplicate action_class");
});

test("register: a caller-supplied status or actor field cannot reach the row -- the schema admits no such property", () => {
  const props = TOOLS["register-action-class-successor"].inputSchema.properties;
  assert.equal(props.status, undefined);
  assert.equal(props.actor_id, undefined);
  assert.equal(TOOLS["register-action-class-successor"].inputSchema.additionalProperties, false);
});

test("read-action-class-successors: lists entries, filtered when action_class is passed", async () => {
  const { error, result } = await call("read-action-class-successors", { action_class: GOOD.action_class });
  assert.equal(error, null);
  assert.equal(result.entries.length, 1);
  assert.equal(result.entries[0].status, "inactive");
});

test("read-action-class-successors: an invalid filter is refused before any query", async () => {
  const { error } = await call("read-action-class-successors", { action_class: "NOT VALID" });
  assert.equal(error?.error, "action_class_invalid");
});

test("read-action-class-gate: denies an unregistered class", async () => {
  const { error, result } = await call("read-action-class-gate", { action_class: "email_unattended_send" });
  assert.equal(error, null);
  assert.equal(result.allowed, false);
  assert.equal(result.registered, false);
});

test("read-action-class-gate: requires action_class", () => {
  assert.deepEqual(TOOLS["read-action-class-gate"].inputSchema.required, ["action_class"]);
});
