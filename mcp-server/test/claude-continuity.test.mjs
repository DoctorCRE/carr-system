import test from "node:test";
import assert from "node:assert/strict";

import { buildClaudeRecoveryCapsule, claudeContinuityTools } from "../src/claude-continuity.js";
import { continuityActorForTokenMaps } from "../src/identity.js";
import { allowedIn, profileForActor } from "../src/mcp.js";

class TestToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const actor = {
  id: "actor-claude", slug: "claude", human: false, native_agent_verified: true,
  continuity_surface: "claude", sponsoring_human_slug: "joe", via: "claude-continuity-token",
};
const base = {
  runtime: "claude", session_id: "session-1", transcript_path_digest: "a".repeat(64),
  project_affinity: "repo:origin", cwd: "/repo/worktree", parent_session_id: "parent-1",
  native_agent_id: "agent-1", model_id: "claude-fable-5-1",
};
const state = {
  objective: "Complete the native continuity implementation",
  latest_corrections: [{ text: "Keep Stop advisory", refs: ["turn:15648"] }],
  constraints: [{ text: "Never replay pending effects" }],
  pending_external_effects: [{ text: "Verify the prior deploy", refs: ["receipt:deploy"] }],
  next_action: "Run the isolated PostgreSQL proof",
  source_observed_at: "2026-09-06T12:00:00Z",
  source_cursor: { byte_offset: 1234, compaction_generation: 2 },
};

test("surface tokens are locked to their three continuity verbs", () => {
  const request = new Request("https://api.doctorcre.com/mcp?profile=full");
  assert.equal(profileForActor(actor, request), "claude-continuity");
  assert.equal(allowedIn("claude-continuity", "claude-checkpoint", { write: true }), true);
  assert.equal(allowedIn("claude-continuity", "log-activity", { write: true }), false);
  const codex = { continuity_surface: "codex", via: "codex-continuity-token" };
  assert.equal(profileForActor(codex, request), "codex-continuity");
  assert.equal(allowedIn("codex-continuity", "codex-record-event", { write: true }), true);
  assert.equal(allowedIn("codex-continuity", "claude-record-event", { write: true }), false);
});

function tools() {
  return claudeContinuityTools({
    ToolError: TestToolError,
    assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _actor, _verb, _args, operation) => operation(),
    writeEvent: async () => {},
  });
}

function memoryClient() {
  let leaf = null;
  let checkpoint = null;
  const events = [];
  const queries = [];
  return {
    queries,
    get leaf() { return leaf; },
    get checkpoint() { return checkpoint; },
    events,
    async query(sql, params = []) {
      queries.push({ sql, params });
      if (sql === "select id from actor where slug=$1")
        return { rows: params[0] === "claude" ? [{ id: "actor-claude" }] : [] };
      if (sql.startsWith("select pg_advisory")) return { rows: [] };
      if (sql.startsWith("insert into claude_continuity_leaf")) {
        if (!leaf) leaf = {
          id: "leaf-1", organization_tenant_id: params[0], surface_principal_actor_id: params[1],
          owner_actor_id: "actor-joe", session_id: params[3], transcript_path_digest: params[4],
          project_affinity: params[5], parent_session_id: params[6], native_agent_id: params[7],
          latest_cwd: params[8], latest_model_id: params[9],
        };
        return { rows: [] };
      }
      if (sql.startsWith("select l.* from claude_continuity_leaf"))
        return { rows: leaf && leaf.organization_tenant_id === params[0] &&
          leaf.surface_principal_actor_id === params[1] && leaf.session_id === params[3] &&
          leaf.transcript_path_digest === params[4] ? [leaf] : [] };
      if (sql.startsWith("update claude_continuity_leaf")) {
        leaf = { ...leaf, latest_cwd: params[1], latest_model_id: params[2] };
        return { rows: [] };
      }
      if (sql.startsWith("select c.id")) return { rows: checkpoint && leaf ? [{ ...checkpoint,
        session_id: leaf.session_id, transcript_path_digest: leaf.transcript_path_digest,
        project_affinity: leaf.project_affinity, parent_session_id: leaf.parent_session_id,
        native_agent_id: leaf.native_agent_id, cwd: leaf.latest_cwd, model_id: leaf.latest_model_id,
      }] : [] };
      if (sql.startsWith("insert into claude_continuity_checkpoint")) {
        checkpoint = { id: "checkpoint-1", leaf_id: params[0], state: JSON.parse(params[1]),
          cursor: JSON.parse(params[2]), transcript_digest: params[3], source_observed_at: params[4],
          compaction_generation: params[5], checkpoint_version: "1" };
        return { rows: [checkpoint] };
      }
      if (sql.startsWith("update claude_continuity_checkpoint")) {
        if (!checkpoint || Number(checkpoint.checkpoint_version) !== params[7]) return { rows: [] };
        checkpoint = { ...checkpoint, state: JSON.parse(params[2]), cursor: JSON.parse(params[3]),
          transcript_digest: params[4], source_observed_at: params[5], compaction_generation: params[6],
          checkpoint_version: String(Number(checkpoint.checkpoint_version) + 1) };
        return { rows: [checkpoint] };
      }
      if (sql.startsWith("insert into claude_continuity_revision")) return { rows: [] };
      if (sql.startsWith("insert into claude_continuity_event")) {
        const existing = events.find(event => event.organization_tenant_id === params[0] &&
          event.surface_principal_actor_id === params[1] && event.idempotency_key === params[9]);
        if (existing) return { rows: [] };
        const event = { id: `event-${events.length + 1}`, organization_tenant_id: params[0],
          surface_principal_actor_id: params[1], leaf_id: params[2], event_type: params[3],
          cursor: JSON.parse(params[4]), transcript_digest: params[5], observed_at: params[6],
          telemetry: params[7] == null ? null : JSON.parse(params[7]), checkpoint_version: params[8],
          idempotency_key: params[9] };
        events.push(event);
        return { rows: [event] };
      }
      if (sql.startsWith("select * from claude_continuity_event"))
        return { rows: events.filter(event => event.idempotency_key === params[2]) };
      throw new Error(`unexpected SQL: ${sql}`);
    },
  };
}

test("a dedicated Claude token actor can read the leaf written by its hydrated write call", async () => {
  const tokenActor = continuityActorForTokenMaps(
    "Bearer claude-secret", "{}", JSON.stringify({ joe: "claude-secret" }),
  );
  assert.equal(tokenActor.id, undefined);
  const client = memoryClient();
  const registered = tools();
  const invokeLikeDispatch = (verb, args) => {
    const tool = registered[verb];
    const callActor = tool.write ? { ...tokenActor, id: "actor-claude" } : tokenActor;
    return tool.handler(client, callActor, args);
  };
  await invokeLikeDispatch("claude-checkpoint", { ...base, idempotency_key: "checkpoint-token",
    expected_version: 0, state, compaction_generation: 1 });
  const recovered = await invokeLikeDispatch("claude-read-recovery", base);
  assert.equal(recovered.found, true);
  assert.equal(recovered.checkpoint.checkpoint_version, 1);
  assert.equal(tokenActor.id, "actor-claude");
});

test("Claude recovery fails closed when its authenticated actor row is unavailable", async () => {
  const tokenActor = continuityActorForTokenMaps(
    "Bearer claude-secret", "{}", JSON.stringify({ joe: "claude-secret" }),
  );
  const client = { query: async (sql, params) => {
    assert.equal(sql, "select id from actor where slug=$1");
    assert.deepEqual(params, ["claude"]);
    return { rows: [] };
  } };
  await assert.rejects(() => tools()["claude-read-recovery"].handler(client, tokenActor, base),
    error => error.payload?.error === "claude_continuity_actor_unavailable");
});

test("every Claude continuity verb refuses wrong-surface, shared, and unverified credentials before SQL", async () => {
  const client = { query: async () => { throw new Error("authority refusal must precede SQL"); } };
  const args = { ...base, idempotency_key: "event-1", expected_version: 0, state,
    event_type: "pre_compact", cursor: {}, observed_at: state.source_observed_at };
  for (const badActor of [
    { ...actor, continuity_surface: "codex" },
    { ...actor, continuity_surface: undefined, via: "local-token" },
    { ...actor, native_agent_verified: false },
    { ...actor, slug: "codex" },
  ]) {
    for (const verb of ["claude-checkpoint", "claude-read-recovery", "claude-record-event"])
      await assert.rejects(() => tools()[verb].handler(client, badActor, args),
        error => error.payload?.error === "claude_native_principal_required");
  }
});

test("the first lifecycle write establishes one immutable leaf binding for events and checkpoints", async () => {
  const client = memoryClient();
  await tools()["claude-record-event"].handler(client, actor, { ...base,
    idempotency_key: "event-1", event_type: "user_prompt_submit", cursor: { offset: 1 },
    observed_at: state.source_observed_at,
  });
  await assert.rejects(() => tools()["claude-checkpoint"].handler(client, actor, { ...base,
    project_affinity: "repo:other", idempotency_key: "checkpoint-1", expected_version: 0, state,
  }), error => error.payload?.error === "claude_continuity_binding_conflict");
  await assert.rejects(() => tools()["claude-record-event"].handler(client, actor, { ...base,
    native_agent_id: "agent-other", idempotency_key: "event-2", event_type: "pre_compact", cursor: {},
    observed_at: state.source_observed_at,
  }), error => error.payload?.error === "claude_continuity_binding_conflict");
  assert.equal(client.events.length, 1);
});

test("cwd and model are telemetry while transcript digest and agent leaf separate identity", async () => {
  const client = memoryClient();
  await tools()["claude-checkpoint"].handler(client, actor, { ...base,
    idempotency_key: "checkpoint-1", expected_version: 0, state, compaction_generation: 1,
  });
  const moved = await tools()["claude-checkpoint"].handler(client, actor, { ...base,
    cwd: "/repo/reaped-worktree", model_id: "claude-opus-4-1", idempotency_key: "checkpoint-2",
    expected_version: 1, state: { ...state, next_action: "Resume after worktree cleanup" }, compaction_generation: 1,
  });
  assert.equal(moved.checkpoint.checkpoint_version, 2);
  assert.equal(client.leaf.latest_cwd, "/repo/reaped-worktree");
  assert.equal(client.leaf.latest_model_id, "claude-opus-4-1");

  const distinct = memoryClient();
  await tools()["claude-record-event"].handler(distinct, actor, { ...base,
    transcript_path_digest: "b".repeat(64), native_agent_id: "subagent-2", idempotency_key: "event-subagent",
    event_type: "post_tool_use", cursor: { offset: 3 }, observed_at: state.source_observed_at,
  });
  assert.equal(distinct.leaf.transcript_path_digest, "b".repeat(64));
  assert.equal(distinct.leaf.native_agent_id, "subagent-2");
});

test("checkpoint CAS is JSON-safe and compaction generation never regresses", async () => {
  const client = memoryClient();
  const checkpoint = tools()["claude-checkpoint"];
  await checkpoint.handler(client, actor, { ...base, idempotency_key: "checkpoint-1",
    expected_version: 0, state, compaction_generation: 4 });
  await assert.rejects(() => checkpoint.handler(client, actor, { ...base, idempotency_key: "checkpoint-2",
    expected_version: 1, state, compaction_generation: 3 }),
  error => error.payload?.error === "claude_compaction_generation_regressed");
  await assert.rejects(() => checkpoint.handler({ query: async () => { throw new Error("must not query"); } }, actor,
    { ...base, idempotency_key: "unsafe", expected_version: Number.MAX_SAFE_INTEGER + 1, state }),
  error => error.payload?.error === "claude_checkpoint_expected_version_invalid");
});

test("event replay is idempotent and a changed payload is refused", async () => {
  const client = memoryClient();
  const args = { ...base, idempotency_key: "event-replay", event_type: "post_tool_use",
    cursor: { b: 2, a: 1 }, observed_at: state.source_observed_at, telemetry: { duration_ms: 12 } };
  const first = await tools()["claude-record-event"].handler(client, actor, args);
  const replay = await tools()["claude-record-event"].handler(client, actor, { ...args, cursor: { a: 1, b: 2 } });
  assert.equal(first.event.id, replay.event.id);
  await assert.rejects(() => tools()["claude-record-event"].handler(client, actor,
    { ...args, event_type: "stop" }), error => error.payload?.error === "claude_event_key_conflict");
});

test("multibyte native leaf identifiers are refused before database access", async () => {
  const args = { ...base, native_agent_id: "🧭".repeat(200), idempotency_key: "invalid-leaf",
    event_type: "pre_compact", cursor: {}, observed_at: state.source_observed_at };
  await assert.rejects(() => tools()["claude-record-event"].handler(
    { query: async () => { throw new Error("must not query"); } }, actor, args),
  error => ["claude_continuity_field_invalid", "claude_native_identity_invalid"].includes(error.payload?.error) &&
    error.payload?.field === "native_agent_id");
});

test("worst-case capsule reserves all mandatory recovery sections before optional evidence", () => {
  const huge = "🧭".repeat(1000);
  const capsule = buildClaudeRecoveryCapsule({ checkpoint_version: 7, state: {
    ...state, objective: `objective sentinel ${huge}`,
    latest_corrections: Array.from({ length: 20 }, (_, index) => ({ text: `correction-${index} ${huge}`, refs: [`turn:${index}`] })),
    constraints: Array.from({ length: 20 }, (_, index) => ({ text: `constraint-${index} ${huge}` })),
    pending_external_effects: Array.from({ length: 20 }, (_, index) => ({ text: `pending-${index} ${huge}`, refs: [`receipt:${index}`] })),
    next_action: `next sentinel ${huge}`,
    decisions: Array.from({ length: 20 }, (_, index) => ({ text: `optional-${index} ${huge}`, why: huge, refs: [`decision:${index}`] })),
  } });
  const capsuleBytes = new TextEncoder().encode(capsule).byteLength;
  assert.ok(capsuleBytes <= 3200);
  // A deliberately conservative measured budget (3 UTF-8 bytes/token) keeps
  // the Worker capsule at or below 1,200 tokens before the native envelope.
  assert.ok(Math.ceil(capsuleBytes / 3) <= 1200);
  for (const required of ["Objective:", "objective sentinel", "Current corrections:", "correction-0",
    "Current constraints:", "constraint-0", "Pending external effects (verify; never replay):",
    "pending-0", "Next action:", "next sentinel"])
    assert.match(capsule, new RegExp(required.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});
