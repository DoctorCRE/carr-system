// Deterministic guardrail coverage: untrusted authority fields must stop at the
// registered-tool choke point, and the human ownership handoff must be both
// human-gated and optimistic-concurrency guarded.

import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, assertNoCallerAuthorityFields, executeRegisteredTool } from "../src/tools.js";
import { callTool } from "../src/mcp.js";

const CANARY = "CARR-SECRET-CANARY-7F4A";
const DEAL_ID = "50000000-0000-0000-0000-000000000001";
const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "test" };
const AGENT = { id: "10000000-0000-0000-0000-000000000099", slug: "codex", display: "Codex", human: false, via: "test" };

function payload(extra = {}) {
  return { idempotency_key: "set-lead-guardrail", deal: "Synthetic Deal", new_lead: "dell", base_version: 1, ...extra };
}

async function rejected(fn) {
  try {
    await fn();
    assert.fail("expected ToolError");
  } catch (error) {
    assert.ok(error instanceof ToolError);
    return error.payload;
  }
}

class SetLeadFake {
  constructor(version = 1) {
    this.version = version;
    this.owner = "joe";
    this.handlerCalls = 0;
    this.participantWrites = 0;
    this.eventWrites = 0;
    this.lastEvent = null;
    this.envelopeWrites = 0;
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.includes("from v_ref_index where subject_type='deal' and display_name ilike"))
      return { rows: [{ subject_id: DEAL_ID }] };
    if (sql.includes("dealroom:field-lock")) return { rows: [{}] };
    if (sql === "select version from deal where id=$1 for update")
      return { rows: [{ version: this.version }] };
    if (sql === "select owner from deal where id=$1") return { rows: [{ owner: this.owner }] };
    if (sql === "select created_at from deal where id=$1") return { rows: [{ created_at: "2026-08-15T00:00:00Z" }] };
    if (sql.includes("from event e join actor a")) return { rows: [] };
    if (sql === "select id from actor where slug=$1") return { rows: [{ id: `actor-${params[0]}` }] };
    if (sql.startsWith("update deal_participant set to_at=now()")) {
      this.participantWrites += 1;
      return { rows: [{ actor_id: "actor-joe" }] };
    }
    if (sql.startsWith("insert into deal_participant")) {
      this.participantWrites += 1;
      return { rows: [] };
    }
    if (sql.startsWith("update deal set owner=")) {
      this.handlerCalls += 1;
      this.owner = params[0];
      this.version += 1;
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) {
      this.eventWrites += 1;
      this.lastEvent = { field: params[5], old: JSON.parse(params[6]),
        next: JSON.parse(params[7]), recordedAfterLock: params[18] };
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.envelopeWrites += 1;
      return { rows: [] };
    }
    throw new Error(`unexpected query: ${sql}`);
  }
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

class SharedSetLeadTransaction {
  constructor() {
    this.version = 1;
    this.holder = null;
    this.firstAtParticipant = deferred();
    this.allowFirstMutation = deferred();
    this.secondWaiting = deferred();
    this.committed = deferred();
    this.participantWrites = { first: 0, second: 0 };
    this.eventWrites = 0;
  }

  client(label) {
    const state = this;
    return {
      async query(text, params = []) {
        const sql = text.replace(/\s+/g, " ").trim();
        if (sql.startsWith("select request_hash, response")) return { rows: [] };
        if (sql.includes("from v_ref_index where subject_type='deal' and display_name ilike"))
          return { rows: [{ subject_id: DEAL_ID }] };
        if (sql.includes("dealroom:field-lock")) return { rows: [{}] };
        if (sql === "select version from deal where id=$1 for update") {
          if (state.holder === null) {
            state.holder = label;
            return { rows: [{ version: state.version }] };
          }
          assert.equal(state.holder, "first", "only the second transaction may wait for the first");
          state.secondWaiting.resolve();
          await state.committed.promise;
          return { rows: [{ version: state.version }] };
        }
        if (sql === "select created_at from deal where id=$1") return { rows: [{ created_at: "2026-08-15T00:00:00Z" }] };
        if (sql === "select owner from deal where id=$1") return { rows: [{ owner: "joe" }] };
        if (sql.includes("from event e join actor a")) return { rows: [] };
        if (sql === "select id from actor where slug=$1") return { rows: [{ id: `actor-${params[0]}` }] };
        if (sql.startsWith("update deal_participant set to_at=now()")) {
          if (label === "first") {
            state.firstAtParticipant.resolve();
            await state.allowFirstMutation.promise;
          }
          state.participantWrites[label] += 1;
          return { rows: [{ actor_id: "actor-joe" }] };
        }
        if (sql.startsWith("insert into deal_participant")) {
          state.participantWrites[label] += 1;
          return { rows: [] };
        }
        if (sql.startsWith("update deal set owner=")) {
          state.version += 1;
          return { rows: [] };
        }
        if (sql.startsWith("insert into event")) {
          state.eventWrites += 1;
          return { rows: [] };
        }
        if (sql.startsWith("insert into tool_call")) return { rows: [] };
        throw new Error(`unexpected query: ${sql}`);
      },
    };
  }
}

test("top-level reserved authority fields are rejected before a direct handler or database access", async () => {
  const reserved = [
    "tenant", "tenant_id", "organization_tenant_id", "sponsor", "sponsoring_human_id",
    "sponsoring_human_slug", "human_slug", "identity", "actor", "runtime_principal",
    "authorization", "authorization_class", "profile", "capability", "capabilities",
    "action", "actions", "action_authority", "action_authorities", "allowed_actions",
    "write", "writes_records", "calls_models", "call_models",
  ];
  for (const field of reserved) {
    const db = new SetLeadFake();
    const out = await rejected(() => executeRegisteredTool(db, JOE, "set-lead", payload({ [field]: CANARY })));
    assert.deepEqual(out, { error: "caller_authority_field_forbidden" });
    assert.equal(db.handlerCalls, 0, `${field} reached a handler`);
    assert.equal(db.participantWrites, 0, `${field} reached a write`);
    assert.ok(!JSON.stringify(out).includes(CANARY));
  }
});

test("the same top-level gate covers composite executeRegisteredTool dispatch", async () => {
  const db = { query: async () => { throw new Error("database must not be reached"); } };
  const out = await rejected(() => executeRegisteredTool(db, JOE, "set-next-step", {
    idempotency_key: "composite-guard", text: "synthetic", profile: CANARY,
  }));
  assert.deepEqual(out, { error: "caller_authority_field_forbidden" });
  assert.ok(!JSON.stringify(out).includes(CANARY));
});

test("direct MCP and call-verb recursion refuse authority claims before a writer pool", async () => {
  const direct = await rejected(() => callTool({}, JOE, "set-lead", payload({ profile: CANARY })));
  assert.deepEqual(direct, { error: "caller_authority_field_forbidden" });
  assert.ok(!JSON.stringify(direct).includes(CANARY));

  const recursive = await rejected(() => callTool({}, JOE, "call-verb", {
    verb: "set-lead", args: payload({ action_authority: CANARY }),
  }));
  assert.deepEqual(recursive, { error: "caller_authority_field_forbidden" });
  assert.ok(!JSON.stringify(recursive).includes(CANARY));
});

test("freeform nested business data may use reserved-like names without widening authority", () => {
  const businessData = {
    value: { action: "follow-up", profile: "client-summary" },
    proposes_correction: { authorization: "record-only" },
    field_map: { profile: "template-slot", options: { capability: "plain-text" } },
    scope: { tenant: "market-description" },
    metrics: { action: 1 },
    fields: { profile: "narrative" },
  };
  assert.equal(assertNoCallerAuthorityFields(businessData), businessData);
});

test("set-lead is a human-only optimistic-concurrency mutation", async () => {
  const tool = TOOLS["set-lead"];
  // humanOnly LABEL RETIRED (WR-000019 slice S1, 2026-08-27): dead since
  // executeRegisteredTool stopped reading it 2026-08-26 (decision dc57f62d);
  // this slice drops the stale declaration from tools.js. The behavioral
  // proof that a machine actor is admitted now lives in the assertion below.
  assert.equal(tool.humanOnly, undefined);
  assert.ok(tool.inputSchema.required.includes("base_version"));
  assert.ok(Object.hasOwn(tool.inputSchema.properties, "base_version"));

  // INVERTED (Joe's ruling 2026-08-26, decision dc57f62d): set-lead admits a
  // machine actor now. The optimistic-concurrency checks below are NOT
  // authority checks and must keep firing for every actor.
  let nonhuman;
  try { await executeRegisteredTool(new SetLeadFake(), AGENT, "set-lead", payload()); }
  catch (error) { nonhuman = error.payload; }
  assert.notEqual(nonhuman?.error, "human_only");

  const missing = await rejected(() => executeRegisteredTool(new SetLeadFake(), JOE, "set-lead", payload({ base_version: undefined })));
  assert.equal(missing.error, "missing_required");

  const invalid = await rejected(() => executeRegisteredTool(new SetLeadFake(), JOE, "set-lead", payload({ base_version: "one" })));
  assert.equal(invalid.error, "invalid_number");
});

test("two concurrent same-version set-lead transactions lock, then emit one event and one version_conflict", async () => {
  const shared = new SharedSetLeadTransaction();
  const first = executeRegisteredTool(shared.client("first"), JOE, "set-lead", payload({ idempotency_key: "race-one" }));
  await shared.firstAtParticipant.promise;

  const second = executeRegisteredTool(shared.client("second"), JOE, "set-lead", payload({ idempotency_key: "race-two" }));
  await shared.secondWaiting.promise;
  assert.equal(shared.participantWrites.second, 0, "the waiting transaction must not mutate participants");

  shared.allowFirstMutation.resolve();
  const firstResult = await first;
  assert.deepEqual(firstResult, { ok: true, new_lead: "dell" });
  assert.equal(shared.eventWrites, 1);
  assert.equal(shared.participantWrites.first, 2);

  // The caller's transaction commits only after its handler returns. Releasing
  // this controlled lock models that commit; the second then reads version 2.
  shared.committed.resolve();
  const stale = await rejected(() => second);
  assert.equal(stale.error, "version_conflict");
  assert.equal(shared.eventWrites, 1, "the stale second proposal must not record a second event");
  assert.equal(shared.participantWrites.second, 0, "the stale second proposal must not mutate participants");
});

test("a sequential stale proposal remains refused after the lock race", async () => {
  const db = new SetLeadFake(1);
  const first = await executeRegisteredTool(db, JOE, "set-lead", payload({ idempotency_key: "sequential-one" }));
  assert.deepEqual(first, { ok: true, new_lead: "dell" });
  assert.deepEqual([db.lastEvent.field, db.lastEvent.old.owner,
    db.lastEvent.next.owner, db.lastEvent.recordedAfterLock],
  ["owner", "joe", "dell", true]);
  const stale = await rejected(() => executeRegisteredTool(db, JOE, "set-lead", payload({ idempotency_key: "sequential-two" })));
  assert.equal(stale.error, "version_conflict");
});
