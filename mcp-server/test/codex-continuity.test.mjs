import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import { codexContinuityTools } from "../src/codex-continuity.js";
import { TOOLS } from "../src/tools.js";
import { digest } from "../src/artifact-trust.js";
import { splitReferenceManifest, storedJsonBytes } from "../continuity-reference-manifest.mjs";
import { agentActorForToken, authenticatedIdentity, continuityActorForTokenMaps } from "../src/identity.js";
// `actorFromProps` is module-private under amendment 8 (PR 1013). The exported
// grant door is `authenticatedIdentity.connectionForGrant`; called without the
// server's witness it returns exactly the same actor, unbranded, which is what
// every case in this file is about.
const actorFromProps = (props, bindings = null) =>
  authenticatedIdentity.connectionForGrant(props, bindings);


class TestToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}
const actor = { id: "actor-codex", slug: "codex", human: false, native_agent_verified: true,
  sponsoring_human_slug: "joe", via: "oauth-google", continuity_surface: "codex" };
const key = "00000000-0000-4000-8000-000000000001";
const state = { objective: "keep working", next_action: "verify result", progress: [{ text: "started", refs: [] }] };

function validStateAt(bytes) {
  const value = {
    objective: "x".repeat(4000), next_action: "continue",
    constraints: Array.from({ length: 6 }, (_item, index) => ({ text: index < 5 ? "x".repeat(3900) : "" })),
  };
  value.constraints[5].text = "x".repeat(bytes - storedJsonBytes(value));
  assert.ok(value.constraints[5].text.length <= 4000);
  assert.equal(storedJsonBytes(value), bytes);
  return value;
}

function tools() {
  return codexContinuityTools({
    ToolError: TestToolError,
    assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    writeEvent: async () => {},
  });
}

test("list-my-codex-sessions reads only first-hand checkpoints for the authenticated sponsor", async () => {
  const seen = [];
  const nativeId = "01a0eca8-f7f3-7153-a0a9-7f53dffb25b0";
  const client = { query: async (sql, params) => {
    seen.push({ sql, params });
    assert.match(sql, /from codex_continuity_checkpoint/);
    assert.match(sql, /owner_actor_id=\(select id from actor where slug=\$2\)/);
    assert.doesNotMatch(sql, /codex_continuity_event|session_identity/);
    return { rows: params[1] === "joe" ? [{ native_task_id: nativeId,
      checkpoint_version: "3", updated_at: "2026-09-29T12:00:00Z" }] : [] };
  } };
  const verb = tools()["list-my-codex-sessions"];
  assert.deepEqual(verb.inputSchema.required ?? [], []);
  assert.deepEqual(Object.keys(verb.inputSchema.properties), []);
  const joe = await verb.handler(client, { id: "joe-id", slug: "joe", human: true }, {});
  assert.deepEqual(joe.sessions, [{ native_session_id: nativeId, host: "codex_desktop",
    availability: "checkpoint_recorded", checkpoint_version: 3,
    updated_at: "2026-09-29T12:00:00Z" }]);
  const sponsoredApp = await verb.handler(client, { id: "app-id", slug: "codex", human: false,
    via: "oauth-google", sponsoring_human_slug: "joe", sponsor_required: true }, {});
  assert.deepEqual(sponsoredApp.sessions, joe.sessions,
    "the server-derived sponsor, not the app actor's own slug, owns the checkpoint");
  const dell = await verb.handler(client, { id: "dell-id", slug: "dell", human: true }, {});
  assert.deepEqual(dell.sessions, [], "Dell cannot see Joe's checkpoint");
  assert.deepEqual(seen.map(call => call.params), [["carr-internal", "joe"],
    ["carr-internal", "joe"], ["carr-internal", "dell"]]);
  await assert.rejects(() => verb.handler(client, { id: "joe-id", slug: "joe", human: true },
    { sponsoring_human_slug: "dell" }), error =>
    error.payload?.error === "codex_session_list_arguments_invalid");
  assert.equal(seen.length, 3, "caller supplied identity must be refused before any database query");
  await assert.rejects(() => verb.handler(client, { id: "unbound", slug: "codex", human: false,
    via: "oauth-google" }, {}), error => error.payload?.error === "codex_sponsor_required");
});

test("checkpoint normalizes the database bigint version and uses it in the revision", async () => {
  const statements = [];
  const events = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [] };
    if (sql.startsWith("insert into codex_continuity_checkpoint")) return { rows: [{ id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", state, cursor: null, checkpoint_version: "1" }] };
    if (sql.startsWith("insert into codex_continuity_revision")) return { rows: [] };
    return { rows: [] };
  } };
  const checkpointTools = codexContinuityTools({
    ToolError: TestToolError,
    assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    writeEvent: async (...args) => { events.push(args); },
  });
  const out = await checkpointTools["codex-checkpoint"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
    expected_version: 0, state,
  });
  assert.equal(out.checkpoint.checkpoint_version, 1);
  assert.match(statements.find(x => x.sql.startsWith("select pg_advisory")).sql, /hashtextextended/);
  assert.match(statements.find(x => x.sql.startsWith("insert into codex_continuity_revision")).sql, /state/);
  assert.equal(statements.find(x => x.sql.startsWith("insert into codex_continuity_checkpoint")).params[6], null);
  assert.equal(statements.find(x => x.sql.startsWith("insert into codex_continuity_revision")).params[3], null);
  assert.equal(statements.find(x => x.sql.startsWith("insert into codex_continuity_revision")).params[1], 1);
  assert.equal(events[0][5].new.version, 1);
});

test("checkpoint rejects a valid 24KB-plus-one semantic state before database access", async () => {
  const oversized = validStateAt(24_001);
  await assert.rejects(() => tools()["codex-checkpoint"].handler({ query: async () => {
    throw new Error("oversized semantic state must not query");
  } }, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-over-budget", project_id: "p", cwd: "/repo",
    expected_version: 0, state: oversized,
  }), error => error.payload?.error === "codex_continuity_payload_too_large" &&
    error.payload?.stored_bytes === 24_001);
});

test("checkpoint replay confirms an accepted write without repeating its effects", async () => {
  const effects = [];
  let version = 0;
  const client = { query: async (sql, params) => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id"))
      return version ? { rows: [{ id: "cp-replay", project_id: "p", cwd: "/repo",
        state, cursor: { source_window_id: "window-1", source_window_number: 1,
          turn_id: "turn-1" }, checkpoint_version: String(version) }] } : { rows: [] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.startsWith("select cursor from codex_continuity_event")) return { rows: [] };
    if (sql.includes("with prompts as"))
      return { rows: [{ turns: [], omitted: 0, coverage_known: true }] };
    if (sql.startsWith("insert into codex_continuity_checkpoint")) {
      effects.push("checkpoint");
      version = 1;
      return { rows: [{ id: "cp-replay", native_task_id: "task-replay", project_id: "p",
        cwd: "/repo", state: JSON.parse(params[5]), cursor: JSON.parse(params[6]),
        reference_manifest: JSON.parse(params[7]), checkpoint_version: "1" }] };
    }
    if (sql.startsWith("insert into codex_continuity_revision")) {
      effects.push("revision");
      return { rows: [] };
    }
    return { rows: [] };
  } };
  const stored = new Map();
  const replaying = codexContinuityTools({
    ToolError: TestToolError,
    assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, verb, args, fn) => {
      const { idempotency_key: idempotencyKey, ...payload } = args;
      const requestHash = JSON.stringify({ verb, payload });
      const prior = stored.get(idempotencyKey);
      if (prior) {
        if (prior.requestHash !== requestHash) throw new TestToolError({ error: "key_reuse" });
        return { replayed: true, ...prior.response };
      }
      const response = await fn();
      stored.set(idempotencyKey, { requestHash, response });
      return response;
    },
    writeEvent: async () => {},
  });
  const args = {
    idempotency_key: key, runtime: "codex", native_task_id: "task-replay",
    project_id: "p", cwd: "/repo", expected_version: 0, state,
    cursor: { source_window_id: "window-1", source_window_number: 1, turn_id: "turn-1" },
  };
  const first = await replaying["codex-checkpoint"].handler(client, actor, args);
  const retry = await replaying["codex-checkpoint"].handler(client, actor, args);
  assert.equal(first.checkpoint.checkpoint_version, 1);
  assert.equal(retry.replayed, true);
  assert.equal(retry.checkpoint.checkpoint_version, 1);
  assert.deepEqual(effects, ["checkpoint", "revision"]);
  await assert.rejects(() => replaying["codex-checkpoint"].handler(client, actor, {
    ...args, cursor: { ...args.cursor, turn_id: "turn-newer" },
  }), error => error.payload?.error === "key_reuse");
  assert.deepEqual(effects, ["checkpoint", "revision"]);
  await assert.rejects(() => replaying["codex-checkpoint"].handler(client, actor, {
    ...args, idempotency_key: "00000000-0000-4000-8000-000000000002",
    cursor: { ...args.cursor, turn_id: "turn-newer" },
  }), error => error.payload?.error === "codex_checkpoint_version_conflict");
  const recovered = await replaying["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-replay", project_id: "p", cwd: "/repo",
  });
  assert.equal(recovered.checkpoint.checkpoint_version, 1);
  assert.equal(recovered.checkpoint.cursor.turn_id, "turn-1",
    "a stale competing write must not claim the newer turn was incorporated");
  assert.deepEqual(effects, ["checkpoint", "revision"]);
});

test("local v12 capture saves physically and reads back the full logical state", { skip: !fs.existsSync("/tmp/codex-capacity-live-v12.json") }, async () => {
  const capturedState = JSON.parse(fs.readFileSync("/tmp/codex-capacity-live-v12.json", "utf8"));
  capturedState.progress.push({ text: "x".repeat(450) });
  assert.ok(storedJsonBytes(capturedState) > 24_000,
    "the v12 reproduction must exceed the former single 24KB state budget");
  const expectedState = structuredClone(capturedState);
  let stored;
  const client = { query: async (sql, params = []) => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: stored ? [stored] : [] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.startsWith("insert into codex_continuity_checkpoint")) {
      stored = { id: "cp-local-v12", native_task_id: params[2], project_id: params[3], cwd: params[4],
        state: JSON.parse(params[5]), cursor: params[6] === null ? null : JSON.parse(params[6]),
        reference_manifest: JSON.parse(params[7]), checkpoint_version: "1" };
      return { rows: [stored] };
    }
    if (sql.startsWith("insert into codex_continuity_revision")) return { rows: [] };
    if (sql.startsWith("select cursor from codex_continuity_event")) return { rows: [] };
    if (sql.includes("with prompts as")) return { rows: [{ turns: [], omitted: 0, coverage_known: true }] };
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const args = { idempotency_key: "00000000-0000-4000-8000-000000000099", runtime: "codex",
    native_task_id: "task-local-v12", project_id: "p", cwd: "/repo", expected_version: 0,
    state: capturedState, cursor: { source_window_id: "window-v12", source_window_number: 12 } };
  const saved = await tools()["codex-checkpoint"].handler(client, actor, args);
  assert.ok(storedJsonBytes(stored.state) <= 24_000);
  assert.ok(storedJsonBytes(stored.reference_manifest) <= 128_000);
  assert.notStrictEqual(stored.state, capturedState);
  assert.deepEqual(saved.checkpoint.state, expectedState);
  capturedState.objective = "mutation after save must not affect recovery";
  const recovered = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: args.native_task_id, project_id: args.project_id, cwd: args.cwd,
  });
  assert.deepEqual(recovered.checkpoint.state, expectedState);
  assert.equal(recovered.checkpoint.reference_manifest_summary.reference_count, 143);
});

test("1000 refs save and recover exactly through current and historical handlers", async () => {
  const refs = Array.from({ length: 1000 }, (_item, index) =>
    `current:${String(index).padStart(4, "0")}:${"x".repeat(90)}`);
  const logical = {
    objective: "retain every current reference",
    latest_corrections: [{ text: "current evidence", refs }],
    progress: [{ text: "x".repeat(3567) }, { text: "x".repeat(3567) },
      { text: "x".repeat(3566) }],
    next_action: "read each exact reference",
  };
  let stored;
  let revision;
  const client = { query: async (sql, params = []) => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: stored ? [stored] : [] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.startsWith("insert into codex_continuity_checkpoint")) {
      stored = { id: "cp-1000", native_task_id: params[2], project_id: params[3], cwd: params[4],
        state: JSON.parse(params[5]), cursor: params[6] === null ? null : JSON.parse(params[6]),
        reference_manifest: JSON.parse(params[7]), checkpoint_version: "1" };
      return { rows: [stored] };
    }
    if (sql.startsWith("insert into codex_continuity_revision")) {
      revision = { revision_id: "rev-1000", checkpoint_id: params[0], checkpoint_version: params[1],
        state: JSON.parse(params[2]), cursor: params[3] === null ? null : JSON.parse(params[3]),
        reference_manifest: JSON.parse(params[4]), created_at: "2026-09-10T00:00:00Z" };
      return { rows: [] };
    }
    if (sql.includes("from codex_continuity_revision r")) return { rows: [revision] };
    if (sql.startsWith("select cursor from codex_continuity_event")) return { rows: [] };
    if (sql.includes("with prompts as")) return { rows: [{ turns: [], omitted: 0, coverage_known: true }] };
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const args = { idempotency_key: "00000000-0000-4000-8000-000000000100", runtime: "codex",
    native_task_id: "task-1000", project_id: "p", cwd: "/repo", expected_version: 0,
    state: logical, cursor: { source_window_id: "window-1000", source_window_number: 1 } };
  const saved = await tools()["codex-checkpoint"].handler(client, actor, args);
  assert.ok(storedJsonBytes(stored.state) <= 24_000);
  assert.ok(storedJsonBytes(stored.reference_manifest) <= 128_000);
  assert.deepEqual(saved.checkpoint.state, logical);
  const current = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: args.native_task_id, project_id: args.project_id, cwd: args.cwd,
  });
  assert.deepEqual(current.checkpoint.state, logical);
  assert.equal(current.checkpoint.reference_manifest_summary.reference_count, 1000);
  const historical = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: args.native_task_id, project_id: args.project_id, cwd: args.cwd,
    checkpoint_version: 1,
  });
  assert.deepEqual(historical.revision.state, logical);
  assert.equal(historical.revision.digest, digest({
    schema_version: "codex-continuity-revision.v1", checkpoint_id: "cp-1000",
    checkpoint_version: 1, state: logical, cursor: args.cursor,
  }));
});

test("production checkpoint handler replays through the real envelope and rejects changed or stale retries", async () => {
  const calls = [];
  const toolCalls = new Map();
  let checkpoint = null;
  const client = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response")) {
      const prior = toolCalls.get(params[0]);
      return { rows: prior ? [{ request_hash: prior.request_hash, response: prior.response }] : [] };
    }
    if (sql.startsWith("select id,native_task_id,project_id"))
      return { rows: checkpoint ? [checkpoint] : [] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.startsWith("insert into codex_continuity_checkpoint")) {
      checkpoint = {
        id: "cp-production-envelope", native_task_id: params[2], project_id: params[3], cwd: params[4],
        state: JSON.parse(params[5]), cursor: params[6] === null ? null : JSON.parse(params[6]),
        reference_manifest: JSON.parse(params[7]),
        checkpoint_version: "1", updated_at: "2026-09-08T12:00:00Z",
      };
      return { rows: [checkpoint] };
    }
    if (sql.startsWith("insert into codex_continuity_revision")) return { rows: [] };
    if (sql.startsWith("insert into event (")) return { rows: [] };
    if (sql.startsWith("insert into tool_call (")) {
      toolCalls.set(params[0], {
        request_hash: params[3], response: JSON.parse(params[4]), actor_id: params[2],
        organization_tenant_id: params[7], application_session_id: params[12] ?? null,
      });
      return { rows: [] };
    }
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const verb = TOOLS["codex-checkpoint"];
  const base = {
    idempotency_key: "00000000-0000-4000-8000-000000000021", runtime: "codex",
    native_task_id: "task-production-envelope", project_id: "p", cwd: "/repo",
    expected_version: 0, state,
    cursor: { source_window_id: "window-1", source_window_number: 1, turn_id: "turn-1" },
  };
  const first = await verb.handler(client, actor, base);
  const exactRetry = await verb.handler(client, actor, base);
  assert.equal(first.ok, true);
  assert.equal(exactRetry.replayed, true);
  assert.equal(exactRetry.checkpoint.checkpoint_version, 1);
  assert.equal(calls.filter(call => call.sql.startsWith("insert into codex_continuity_checkpoint")).length, 1);
  assert.equal(calls.filter(call => call.sql.startsWith("insert into codex_continuity_revision")).length, 1);
  assert.equal(calls.filter(call => call.sql.startsWith("insert into event (")).length, 1);
  assert.equal(calls.filter(call => call.sql.startsWith("insert into tool_call (")).length, 1);
  assert.equal(toolCalls.get(base.idempotency_key).actor_id, actor.id);
  assert.equal(toolCalls.get(base.idempotency_key).organization_tenant_id, "carr-internal");
  assert.equal(toolCalls.get(base.idempotency_key).application_session_id, null);

  await assert.rejects(() => verb.handler(client, actor, {
    ...base, cursor: { ...base.cursor, turn_id: "turn-2" },
  }), error => error.payload?.error === "key_reuse");
  assert.equal(calls.filter(call => call.sql.startsWith("insert into codex_continuity_checkpoint")).length, 1);

  await assert.rejects(() => verb.handler(client, actor, {
    ...base, idempotency_key: "00000000-0000-4000-8000-000000000022",
  }), error => error.payload?.error === "codex_checkpoint_version_conflict");
  assert.equal(calls.filter(call => call.sql.startsWith("insert into codex_continuity_checkpoint")).length, 1);
  assert.equal(calls.filter(call => call.sql.startsWith("insert into codex_continuity_revision")).length, 1);
  assert.equal(calls.filter(call => call.sql.startsWith("insert into tool_call (")).length, 1);
});

test("recovery scopes owner through actor slug lookup, never a raw slug-to-uuid comparison", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (sql.includes("with prompts as")) return { rows: [{ turns: [], omitted: 0, coverage_known: true }] };
    return { rows: [] };
  } };
  const out = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
  });
  assert.equal(out.found, false);
  assert.ok(statements.every(statement => /owner_actor_id=\(select id from actor where slug=\$2\)/.test(statement.sql)));
  assert.ok(statements.every(statement => statement.params[1] === "joe"));
  assert.deepEqual(out.unincorporated_user_turns, []);
  assert.equal(out.unincorporated_user_turns_omitted, 0);
  assert.equal(out.source_coverage, "known");
});

test("historical recovery returns one explicitly archived revision with an anchorable pointer", async () => {
  const historicalState = { ...state, next_action: "historical next action" };
  const physicalRevision = splitReferenceManifest(historicalState);
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-history", native_task_id: "task-history", project_id: "p", cwd: "/repo",
      state, cursor: { source_window_number: 3 }, checkpoint_version: "7",
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.includes("from codex_continuity_revision r")) return { rows: [{
      revision_id: "rev-history", checkpoint_id: "cp-history", checkpoint_version: "6",
      state: physicalRevision.state, reference_manifest: physicalRevision.reference_manifest,
      cursor: { source_window_number: 2 },
      created_at: "2026-09-05T12:00:00Z", native_task_id: "task-history",
      project_id: "p", cwd: "/repo",
    }] };
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const out = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-history", project_id: "p", cwd: "/repo",
    checkpoint_version: 6,
  });
  assert.equal(out.historical_archive, true);
  assert.equal(out.historical, true);
  assert.equal(out.found, true);
  assert.equal(out.checkpoint, undefined);
  assert.equal(out.revision.revision_id, "rev-history");
  assert.equal(out.revision.checkpoint_version, 6);
  assert.deepEqual(out.revision.state, historicalState);
  assert.equal(out.revision.reference_manifest_summary.manifest_present, true);
  assert.equal(out.revision.digest, digest({
    schema_version: "codex-continuity-revision.v1", checkpoint_id: "cp-history",
    checkpoint_version: 6, state: historicalState, cursor: { source_window_number: 2 },
  }), "physical storage must digest the hydrated logical revision");
  assert.equal(out.revision.integrity, "computed_unanchored");
  assert.match(out.archive_ref, /^codex-revision:cp-history:6:sha256:[0-9a-f]{64}$/);
  assert.equal(out.revision.archive_ref, out.archive_ref);
  assert.equal(out.revision.digest, out.archive_ref.split(":").slice(-2).join(":"));
  const revisionQuery = statements.find(statement => statement.sql.includes("from codex_continuity_revision r"));
  assert.ok(revisionQuery);
  assert.match(revisionQuery.sql, /c\.organization_tenant_id=\$2/);
  assert.match(revisionQuery.sql, /c\.owner_actor_id=\(select id from actor where slug=\$3\)/);
  assert.match(revisionQuery.sql, /c\.native_task_id=\$4/);
  assert.match(revisionQuery.sql, /c\.project_id=\$5/);
  assert.match(revisionQuery.sql, /c\.cwd=\$6/);
  assert.match(revisionQuery.sql, /r\.checkpoint_version=\$7/);
  assert.deepEqual(revisionQuery.params, ["cp-history", "carr-internal", "joe", "task-history", "p", "/repo", 6]);
  assert.equal(statements.filter(statement => statement.sql.includes("from codex_continuity_event")).length, 1);
});

test("historical recovery verifies an expected digest and rejects a mismatch", async () => {
  const historicalState = { ...state, next_action: "historical next action" };
  const row = {
    revision_id: "rev-verify", checkpoint_id: "cp-verify", checkpoint_version: "3",
    state: historicalState, cursor: { source_window_number: 1 },
    created_at: "2026-09-05T12:00:00Z", native_task_id: "task-verify",
    project_id: "p", cwd: "/repo",
  };
  const client = { query: async sql => {
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-verify", native_task_id: "task-verify", project_id: "p", cwd: "/repo",
      state, cursor: null, checkpoint_version: "4",
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.includes("from codex_continuity_revision r")) return { rows: [row] };
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const first = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-verify", project_id: "p", cwd: "/repo",
    checkpoint_version: 3,
  });
  assert.equal(first.revision.digest, digest({
    schema_version: "codex-continuity-revision.v1", checkpoint_id: "cp-verify",
    checkpoint_version: 3, state: historicalState, cursor: { source_window_number: 1 },
  }), "an empty legacy manifest must preserve the original revision digest");
  const verified = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-verify", project_id: "p", cwd: "/repo",
    checkpoint_version: 3, expected_digest: first.revision.digest,
  });
  assert.equal(verified.revision.integrity, "verified");
  await assert.rejects(() => tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-verify", project_id: "p", cwd: "/repo",
    checkpoint_version: 3, expected_digest: "sha256:" + "0".repeat(64),
  }), error => error.payload?.error === "codex_recovery_revision_digest_mismatch");
});

test("legacy unresolved placeholders remain readable for repair but cannot be written", async () => {
  const legacy = {
    objective: "repair legacy evidence", latest_corrections: [{ text: "repair this", refs: ["legacy:{REF1}"] }],
    next_action: "replace the unresolved reference",
  };
  const client = { query: async sql => {
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-placeholder", native_task_id: "task-placeholder", project_id: "p", cwd: "/repo",
      state: legacy, cursor: null, checkpoint_version: "2",
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.includes("from codex_continuity_revision r")) return { rows: [{
      revision_id: "rev-placeholder", checkpoint_id: "cp-placeholder", checkpoint_version: "1",
      state: legacy, cursor: null, created_at: "2026-09-05T12:00:00Z",
    }] };
    if (sql.startsWith("select cursor from codex_continuity_event")) return { rows: [] };
    if (sql.includes("with prompts as")) return { rows: [{ turns: [], omitted: 0, coverage_known: true }] };
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const current = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-placeholder", project_id: "p", cwd: "/repo",
  });
  assert.deepEqual(current.checkpoint.state, legacy);
  const historical = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-placeholder", project_id: "p", cwd: "/repo", checkpoint_version: 1,
  });
  assert.deepEqual(historical.revision.state, legacy);
  assert.equal(historical.revision.digest, digest({
    schema_version: "codex-continuity-revision.v1", checkpoint_id: "cp-placeholder",
    checkpoint_version: 1, state: legacy, cursor: null,
  }));
  await assert.rejects(() => tools()["codex-checkpoint"].handler({ query: async () => {
    throw new Error("invalid placeholder must not reach the database");
  } }, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-placeholder", project_id: "p", cwd: "/repo",
    expected_version: 0, state: legacy,
  }), error => error.payload?.error === "codex_checkpoint_reference_invalid");
});

test("historical recovery rejects unsafe or incomplete archive selectors before database use", async () => {
  const client = { query: async () => { throw new Error("invalid selector must not query"); } };
  for (const args of [
    { checkpoint_version: 0 },
    { checkpoint_version: "01" },
    { checkpoint_version: Number.MAX_SAFE_INTEGER + 1 },
    { expected_digest: "sha256:" + "0".repeat(63) },
    { expected_digest: "sha256:" + "0".repeat(64) },
  ]) {
    await assert.rejects(() => tools()["codex-read-recovery"].handler(client, actor, {
      runtime: "codex", native_task_id: "task-invalid", project_id: "p", cwd: "/repo", ...args,
    }), error => [
      "codex_recovery_version_invalid", "codex_recovery_expected_digest_invalid",
      "codex_recovery_historical_version_required",
    ].includes(error.payload?.error));
  }
});

test("historical recovery reports a bounded not-found result for an absent revision", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-missing", native_task_id: "task-missing", project_id: "p", cwd: "/repo",
      state, cursor: null, checkpoint_version: "4",
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.includes("from codex_continuity_revision r")) return { rows: [] };
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const out = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-missing", project_id: "p", cwd: "/repo",
    checkpoint_version: 3,
  });
  assert.deepEqual(out, {
    ok: true, found: false, historical: true, historical_archive: true,
    archive_ref: null, revision: null, integrity: "not_found",
    storage_contract: {
      version: "codex-continuity-storage.v2", semantic_state_max_bytes: 24000,
      reference_manifest_max_bytes: 128000, recovery_state: "full_logical_state",
    },
  });
  assert.equal(statements.filter(statement => statement.sql.includes("from codex_continuity_revision r")).length, 1);
});

test("historical recovery rejects a wrong binding and never reaches the revision query", async () => {
  let revisionQuery = false;
  const client = { query: async sql => {
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-bound", native_task_id: "task-bound", project_id: "other-project", cwd: "/repo",
      state, cursor: null, checkpoint_version: "2",
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.includes("from codex_continuity_revision r")) revisionQuery = true;
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  await assert.rejects(() => tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-bound", project_id: "p", cwd: "/repo",
    checkpoint_version: 1,
  }), error => error.payload?.error === "codex_recovery_binding_conflict");
  assert.equal(revisionQuery, false);
});

test("checkpoint rejects stale version and immutable task binding before update", async () => {
  const client = { query: async sql => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{ id: "cp-1", project_id: "other", cwd: "/repo", checkpoint_version: 2 }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    throw new Error("must not update a changed binding");
  } };
  await assert.rejects(() => tools()["codex-checkpoint"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
    expected_version: 2, state,
  }), error => error.payload?.error === "codex_checkpoint_binding_conflict");
});

test("ten repeated recovery cycles preserve correction and next action in one bounded task", async () => {
  let version = 0;
  const client = { query: async (sql, params) => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return version ? { rows: [{ id: "cp-1", project_id: "p", cwd: "/repo", checkpoint_version: String(version) }] } : { rows: [] };
    if (sql.startsWith("insert into codex_continuity_checkpoint")) { version = 1; return { rows: [{ id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", state: JSON.parse(params[5]), reference_manifest: JSON.parse(params[7]), checkpoint_version: String(version) }] }; }
    if (sql.startsWith("update codex_continuity_checkpoint")) { version += 1; return { rows: [{ id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", state: JSON.parse(params[3]), reference_manifest: JSON.parse(params[5]), checkpoint_version: String(version) }] }; }
    return { rows: [] };
  } };
  const verb = tools()["codex-checkpoint"];
  let current = 0;
  for (let i = 0; i < 10; i += 1) {
    const out = await verb.handler(client, actor, {
      idempotency_key: `${key}-${i + 2}`, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
      expected_version: current, state: {
        ...state,
        latest_corrections: [{ text: "rejected approach", refs: ["turn:user-correction"] }],
        decisions: [{ text: "keep the native source", why: "it preserves provenance",
          refs: ["decision:native-source"] }],
        next_action: "verify final output",
      },
    });
    current = out.checkpoint.checkpoint_version;
  }
  assert.equal(current, 10);
  assert.ok(JSON.stringify(state).length < 24000);
});

test("checkpoint handler refuses malformed or unsafe versions before CAS", async () => {
  const checkpoint = tools()["codex-checkpoint"];
  for (const checkpoint_version of ["01", "9007199254740992", true]) {
    let updated = false;
    const client = { query: async sql => {
      if (sql.startsWith("select pg_advisory")) return { rows: [] };
      if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
        id: "cp-1", project_id: "p", cwd: "/repo", checkpoint_version,
      }] };
      if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
      if (sql.startsWith("update codex_continuity_checkpoint")) updated = true;
      return { rows: [] };
    } };
    await assert.rejects(() => checkpoint.handler(client, actor, {
      idempotency_key: key, runtime: "codex", native_task_id: "task-1",
      project_id: "p", cwd: "/repo", expected_version: 1, state,
    }), error => error.payload?.error === "codex_checkpoint_version_invalid");
    assert.equal(updated, false);
  }
  const client = { query: async () => { throw new Error("unsafe input must not query"); } };
  await assert.rejects(() => checkpoint.handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1",
    project_id: "p", cwd: "/repo", expected_version: Number.MAX_SAFE_INTEGER + 1, state,
  }), error => error.payload?.error === "codex_checkpoint_expected_version_invalid");

  let exhaustedUpdate = false;
  const exhaustedClient = { query: async sql => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-max", project_id: "p", cwd: "/repo",
      checkpoint_version: String(Number.MAX_SAFE_INTEGER),
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.startsWith("update codex_continuity_checkpoint")) exhaustedUpdate = true;
    return { rows: [] };
  } };
  await assert.rejects(() => checkpoint.handler(exhaustedClient, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1",
    project_id: "p", cwd: "/repo", expected_version: Number.MAX_SAFE_INTEGER, state,
  }), error => error.payload?.error === "codex_checkpoint_version_exhausted");
  assert.equal(exhaustedUpdate, false, "CAS never increments beyond the JSON safe-integer boundary");
});

test("state list items are closed and required text is documented in the schema", async () => {
  const checkpoint = tools()["codex-checkpoint"];
  assert.equal(checkpoint.inputSchema.properties.state.additionalProperties, false);
  assert.deepEqual(checkpoint.inputSchema.properties.state.properties.progress.items.required, ["text"]);
  const correctionSchema = checkpoint.inputSchema.properties.state.properties.latest_corrections.items;
  assert.deepEqual(correctionSchema.required, ["text", "refs"]);
  assert.equal(correctionSchema.properties.refs.minItems, 1);
  assert.equal(correctionSchema.properties.refs.items.minLength, 1);
  assert.equal(correctionSchema.properties.refs.items.pattern, "\\S");
  assert.match("/tmp/canonical-evidence.json", new RegExp(correctionSchema.properties.refs.items.pattern));
  assert.match("https://github.com/jbookout/carr-system/pull/897", new RegExp(correctionSchema.properties.refs.items.pattern));
  const decisionSchema = checkpoint.inputSchema.properties.state.properties.decisions.items;
  assert.deepEqual(decisionSchema.required, ["text", "why", "refs"]);
  assert.equal(decisionSchema.properties.why.minLength, 1);
  assert.equal(decisionSchema.properties.why.pattern, "\\S");
  assert.equal(decisionSchema.properties.refs.minItems, 1);
  assert.equal(decisionSchema.properties.refs.items.minLength, 1);
  assert.equal(decisionSchema.properties.refs.items.pattern, "\\S");
  const client = { query: async () => { throw new Error("must not query invalid state"); } };
  for (const invalid of [
    { ...state, progress: [{ refs: [] }] },
    { ...state, progress: [{ text: "ok", unexpected: true }] },
    { ...state, latest_corrections: [{ text: "uncited correction" }] },
    { ...state, latest_corrections: [{ text: "uncited correction", refs: [] }] },
    { ...state, latest_corrections: [{ text: "blank citation", refs: ["  "] }] },
    { ...state, decisions: [{ text: "unsupported decision", refs: ["decision:1"] }] },
    { ...state, decisions: [{ text: "unsupported decision", why: "  ", refs: ["decision:1"] }] },
    { ...state, decisions: [{ text: "uncited decision", why: "because" }] },
    { ...state, decisions: [{ text: "uncited decision", why: "because", refs: [] }] },
  ]) {
    await assert.rejects(() => checkpoint.handler(client, actor, {
      idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
      expected_version: 0, state: invalid,
    }), error => error.payload?.error === "codex_checkpoint_field_invalid");
  }
  for (const invalid of [
    { ...state, latest_corrections: [{ text: "placeholder citation", refs: ["doctrine-section:{REF1}"] }] },
    { ...state, decisions: [{ text: "placeholder decision", why: "because", refs: ["native-user-turn:{ref2}"] }] },
    { ...state, objective: "continue from {REF3}" },
    { ...state, progress: [{ text: "resolved against {REF4}" }] },
    { ...state, decisions: [{ text: "decision", why: "evidence {REF5}", refs: ["decision:valid"] }] },
  ]) {
    await assert.rejects(() => checkpoint.handler(client, actor, {
      idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
      expected_version: 0, state: invalid,
    }), error => error.payload?.error === "codex_checkpoint_reference_invalid" &&
      error.payload?.paths?.length === 1);
  }
  let deeplyNested = "leaf";
  for (let depth = 0; depth < 10000; depth += 1) deeplyNested = [deeplyNested];
  await assert.rejects(() => checkpoint.handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
    expected_version: 0, state: { ...state, progress: deeplyNested },
  }), error => error.payload?.error === "codex_checkpoint_field_invalid");
});

test("Claude and unverified callers are rejected before database use", async () => {
  const client = { query: async () => { throw new Error("must not query"); } };
  for (const bad of [
    { ...actor, slug: "claude" },
    { ...actor, continuity_surface: "claude" },
    { ...actor, continuity_surface: undefined },
    { ...actor, native_agent_verified: false },
  ]) {
    await assert.rejects(() => tools()["codex-read-recovery"].handler(client, bad, {
      runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
    }), error => error.payload?.error === "codex_native_principal_required");
    await assert.rejects(() => tools()["codex-read-recovery"].handler(client, bad, {
      runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
      checkpoint_version: 1,
    }), error => error.payload?.error === "codex_native_principal_required");
  }
});

test("Codex OAuth and surface-isolated local transport share the verified owner key", () => {
  const local = agentActorForToken("Bearer local-secret", JSON.stringify({ "joe-local": "local-secret" }), "local-token");
  const continuity = continuityActorForTokenMaps(
    "Bearer codex-secret", JSON.stringify({ joe: "codex-secret" }), JSON.stringify({ joe: "claude-secret" }));
  const oauth = actorFromProps({ slug: "codex", human: false, client_id: "client-1", sponsoring_human_slug: "joe", via: "oauth-google" }, JSON.stringify({ "client-1": "codex" }));
  assert.equal(local.native_agent_verified, true);
  assert.equal(local.sponsoring_human_slug, "joe");
  assert.equal(local.continuity_surface, undefined);
  assert.equal(continuity.native_agent_verified, true);
  assert.equal(continuity.continuity_surface, "codex");
  assert.equal(continuity.sponsoring_human_slug, "joe");
  assert.equal(oauth.native_agent_verified, true);
  assert.equal(oauth.continuity_surface, "codex");
  assert.equal(oauth.sponsoring_human_slug, "joe");
});

test("event retries use insert-do-nothing and reject a conflicting deterministic payload", async () => {
  const event = { id: "ev-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact", cursor: null, transcript_ref: null };
  let mode = "insert";
  const client = { query: async (sql) => {
    if (sql.includes("select project_id,cwd from codex_continuity_checkpoint")) return { rows: [] };
    if (sql.includes("insert into codex_continuity_event")) return { rows: mode === "insert" ? [event] : [] };
    if (sql.includes("from codex_continuity_event where")) return { rows: [{ ...event, event_type: "different" }] };
    return { rows: [] };
  } };
  let writes = 0;
  const eventTools = codexContinuityTools({
    ToolError: TestToolError, assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => { writes += 1; },
  });
  const args = { idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact" };
  await eventTools["codex-record-event"].handler(client, actor, args);
  mode = "conflict";
  await assert.rejects(() => eventTools["codex-record-event"].handler(client, actor, { ...args, event_type: "post_compact" }), error => error.payload?.error === "codex_event_key_conflict");
  assert.equal(writes, 1);
});

test("event replay treats reordered JSONB cursor keys as the same payload", async () => {
  const event = { id: "ev-2", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact", cursor: { b: 2, a: 1 }, transcript_ref: null };
  let mode = "insert";
  const client = { query: async sql => {
    if (sql.includes("select project_id,cwd from codex_continuity_checkpoint")) return { rows: [] };
    if (sql.includes("insert into codex_continuity_event")) return { rows: mode === "insert" ? [event] : [] };
    if (sql.includes("from codex_continuity_event where")) return { rows: [event] };
    return { rows: [] };
  } };
  let writes = 0;
  const eventTools = codexContinuityTools({
    ToolError: TestToolError, assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => { writes += 1; },
  });
  const args = { idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact", cursor: { a: 1, b: 2 } };
  await eventTools["codex-record-event"].handler(client, actor, args);
  mode = "retry";
  await eventTools["codex-record-event"].handler(client, actor, args);
  assert.equal(writes, 2);
});

test("event rejects a task binding change once a checkpoint exists", async () => {
  const client = { query: async sql => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{ id: "cp-1", project_id: "p", cwd: "/original", checkpoint_version: 1 }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    throw new Error("must not insert a mismatched event");
  } };
  await assert.rejects(() => tools()["codex-record-event"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact",
  }), error => error.payload?.error === "codex_event_binding_conflict");
});

test("the first lifecycle event binds checkpoint identity before any checkpoint exists", async () => {
  const client = { query: async sql => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event"))
      return { rows: [{ project_id: "original-project", cwd: "/original" }] };
    throw new Error("must not insert a checkpoint against the first event binding");
  } };
  await assert.rejects(() => tools()["codex-checkpoint"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "other-project", cwd: "/other",
    expected_version: 0, state,
  }), error => error.payload?.error === "codex_checkpoint_binding_conflict");
});

test("checkpoint and event writers take the identical tenant-owner-task advisory lock", async () => {
  const lockParams = [];
  const client = { query: async (sql, params) => {
    if (sql.startsWith("select pg_advisory")) { lockParams.push(params); return { rows: [] }; }
    if (sql.startsWith("select id,native_task_id,project_id") || sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.startsWith("insert into codex_continuity_checkpoint"))
      return { rows: [{ id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", state, cursor: null, checkpoint_version: 1 }] };
    if (sql.startsWith("insert into codex_continuity_revision")) return { rows: [] };
    if (sql.includes("insert into codex_continuity_event"))
      return { rows: [{ id: "ev-1", native_task_id: "task-2", project_id: "p", cwd: "/repo", event_type: "pre_compact", cursor: null, transcript_ref: null }] };
    return { rows: [] };
  } };
  await tools()["codex-checkpoint"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
    expected_version: 0, state,
  });
  await tools()["codex-record-event"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-2", project_id: "p", cwd: "/repo",
    event_type: "pre_compact",
  });
  assert.deepEqual(lockParams, [["carr-internal:joe:task-1"], ["carr-internal:joe:task-2"]]);
});

test("recovery returns bounded pending prompt receipts, highwater, omission count and known coverage", async () => {
  const prompt = {
    event_type: "user_prompt_submit", cursor: { byte_offset: 900, checkpoint_version: 7 },
    transcript_ref: "/native/rollout.jsonl", created_at: "2026-09-05T12:00:00Z",
  };
  let pendingSql;
  let pendingParams;
  const client = { query: async (sql, params) => {
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", checkpoint_version: "7",
      state, cursor: { byte_offset: 800 }, updated_at: "2026-09-05T11:59:00Z",
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event"))
      return { rows: [{ project_id: "p", cwd: "/repo" }] };
    if (sql.startsWith("select cursor from codex_continuity_event"))
      return { rows: [{ cursor: { byte_offset: 900, checkpoint_version: 7 } }] };
    if (sql.includes("with prompts as")) {
      pendingSql = sql;
      pendingParams = params;
      return { rows: [{ turns: [prompt], omitted: 4, coverage_known: true }] };
    }
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const out = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
  });
  assert.deepEqual(out.source_highwater, { byte_offset: 900, checkpoint_version: 7 });
  assert.equal(out.checkpoint.native_task_id, "task-1");
  assert.equal(out.checkpoint.checkpoint_version, 7);
  assert.equal(pendingParams[5], 7);
  assert.deepEqual(out.unincorporated_user_turns, [prompt]);
  assert.equal(out.unincorporated_user_turns_omitted, 4);
  assert.equal(out.source_coverage, "known");
  assert.match(pendingSql, /order by created_at desc,id desc limit \$7/);
  assert.match(pendingSql, /order by created_at asc,id asc\) from selected/);
});

test("legacy prompt receipts remain visible and make recovery coverage unknown", async () => {
  let pendingSql;
  const client = { query: async (sql, params) => {
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-1", project_id: "p", cwd: "/repo", checkpoint_version: 8, state, cursor: null,
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event") || sql.startsWith("select cursor from codex_continuity_event")) return { rows: [] };
    if (sql.includes("with prompts as")) {
      pendingSql = sql;
      assert.equal(params[5], 8);
      assert.equal(params[6], 25);
      return { rows: [{ turns: [{ event_type: "user_prompt_submit", cursor: {}, transcript_ref: null, created_at: "2026-09-05T12:00:00Z" }], omitted: 0, coverage_known: false }] };
    }
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const out = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
  });
  assert.match(pendingSql, /not version_known/);
  assert.equal(out.unincorporated_user_turns.length, 1);
  assert.equal(out.source_coverage, "unknown");
});

test("real PostgreSQL handlers serialize races and isolate tenant, owner, and task reads and writes", {
  skip: !process.env.CARR_CONTINUITY_EPHEMERAL_DATABASE_URL,
  timeout: 30000,
}, async () => {
  const driverModule = process.env.CARR_CONTINUITY_DATABASE_DRIVER_MODULE || "@neondatabase/serverless";
  const databaseDriver = await import(driverModule);
  const Pool = databaseDriver.Pool || databaseDriver.default?.Pool;
  assert.equal(typeof Pool, "function", `database driver ${driverModule} must export Pool`);
  // Revisions are deliberately undeletable.  This must target a disposable
  // database that the test runner destroys after the process exits.
  const pool = new Pool({ connectionString: process.env.CARR_CONTINUITY_EPHEMERAL_DATABASE_URL });
  const setup = await pool.connect();
  const task = `continuity-test-${randomUUID()}`;
  const bindingTask = `continuity-binding-${randomUUID()}`;
  const otherTask = `continuity-other-${randomUUID()}`;
  const foreignTenant = `continuity-foreign-${randomUUID()}`;
  let dbActor;
  let dellActor;
  let joeOwnerId;
  try {
    const actorRows = await setup.query("select id,slug from actor where slug in ('codex','joe','dell')");
    const actorsBySlug = Object.fromEntries(actorRows.rows.map(row => [row.slug, row.id]));
    assert.ok(actorsBySlug.joe && actorsBySlug.dell,
      "integration database needs joe and dell owner rows");
    dbActor = { ...actor, id: actorsBySlug.codex || actorsBySlug.joe };
    dellActor = { ...dbActor, sponsoring_human_slug: "dell" };
    joeOwnerId = actorsBySlug.joe;
  } finally {
    setup.release();
  }

  const transactionalTools = codexContinuityTools({
    ToolError: TestToolError,
    assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (client, _actor, _verb, _args, fn) => {
      await client.query("begin");
      try {
        const result = await fn();
        await client.query("commit");
        return result;
      } catch (error) {
        await client.query("rollback");
        throw error;
      }
    },
    writeEvent: async () => {},
  });
  const invokeAs = async (invokingActor, verb, args) => {
    const client = await pool.connect();
    try { return await transactionalTools[verb].handler(client, invokingActor, args); }
    finally { client.release(); }
  };
  const invoke = (verb, args) => invokeAs(dbActor, verb, args);
  const base = { runtime: "codex", native_task_id: task, project_id: "integration", cwd: "/integration" };

  try {
    const cas = await Promise.allSettled([
      invoke("codex-checkpoint", { ...base, idempotency_key: randomUUID(), expected_version: 0, state }),
      invoke("codex-checkpoint", { ...base, idempotency_key: randomUUID(), expected_version: 0,
        state: { ...state, next_action: "the other concurrent writer" } }),
    ]);
    assert.equal(cas.filter(result => result.status === "fulfilled").length, 1,
      cas.map(result => result.status === "fulfilled" ? "fulfilled" : String(result.reason)).join("; "));
    assert.equal(cas.filter(result => result.status === "rejected" &&
      result.reason.payload?.error === "codex_checkpoint_version_conflict").length, 1);

    const eventKey = randomUUID();
    const eventArgs = { ...base, idempotency_key: eventKey, event_type: "user_prompt_submit",
      cursor: { byte_offset: 10, checkpoint_version: 1 }, transcript_ref: "/integration/rollout.jsonl" };
    const replay = await Promise.all([
      invoke("codex-record-event", eventArgs),
      invoke("codex-record-event", eventArgs),
    ]);
    assert.equal(replay[0].event.id, replay[1].event.id);
    await assert.rejects(() => invoke("codex-record-event", {
      ...eventArgs, cursor: { byte_offset: 11, checkpoint_version: 1 },
    }), error => error.payload?.error === "codex_event_key_conflict",
    "a changed cursor under an accepted event key must be rejected");
    const recovered = await invoke("codex-read-recovery", base);
    assert.equal(recovered.found, true);
    assert.equal(recovered.unincorporated_user_turns.length, 1);
    assert.equal(recovered.unincorporated_user_turns_omitted, 0);
    assert.equal(recovered.source_coverage, "known");
    assert.equal(recovered.source_highwater.checkpoint_version, 1);
    const historical = await invoke("codex-read-recovery", { ...base, checkpoint_version: 1 });
    assert.equal(historical.historical_archive, true);
    assert.equal(historical.historical, true);
    assert.equal(historical.found, true);
    assert.equal(historical.checkpoint, undefined);
    assert.equal(historical.revision.checkpoint_version, 1);
    assert.match(historical.archive_ref, /^codex-revision:[^:]+:1:sha256:[0-9a-f]{64}$/);
    const verifiedHistorical = await invoke("codex-read-recovery", {
      ...base, checkpoint_version: 1, expected_digest: historical.revision.digest,
    });
    assert.equal(verifiedHistorical.revision.integrity, "verified");
    await assert.rejects(() => invoke("codex-record-event", { ...eventArgs, event_type: "pre_compact" }),
      error => error.payload?.error === "codex_event_key_conflict");

    const firstBindingRace = await Promise.allSettled([
      invoke("codex-record-event", { ...base, native_task_id: bindingTask, project_id: "event-first",
        cwd: "/event-first", idempotency_key: randomUUID(), event_type: "pre_compact" }),
      invoke("codex-checkpoint", { ...base, native_task_id: bindingTask, project_id: "checkpoint-first",
        cwd: "/checkpoint-first", idempotency_key: randomUUID(), expected_version: 0, state }),
    ]);
    assert.equal(firstBindingRace.filter(result => result.status === "fulfilled").length, 1);
    assert.equal(firstBindingRace.filter(result => result.status === "rejected" &&
      /codex_(event|checkpoint)_binding_conflict/.test(result.reason.payload?.error)).length, 1);

    const foreign = await pool.connect();
    try {
      await foreign.query(
        `insert into codex_continuity_checkpoint
          (organization_tenant_id,owner_actor_id,native_task_id,project_id,cwd,state)
         values ($1,$2,$3,'foreign-project','/foreign',$4::jsonb)`,
        [foreignTenant, joeOwnerId, otherTask, JSON.stringify({ ...state, next_action: "foreign only" })]);
    } finally {
      foreign.release();
    }

    const internalOther = { ...base, native_task_id: otherTask };
    assert.equal((await invoke("codex-read-recovery", internalOther)).found, false,
      "foreign-tenant state must not be readable through the server-derived tenant");
    await invoke("codex-checkpoint", { ...internalOther, idempotency_key: randomUUID(),
      expected_version: 0, state: { ...state, next_action: "internal only" } });

    assert.equal((await invokeAs(dellActor, "codex-read-recovery", base)).found, false,
      "another sponsor must not read Joe's checkpoint");
    await invokeAs(dellActor, "codex-checkpoint", { ...base, project_id: "dell-project", cwd: "/dell",
      idempotency_key: randomUUID(), expected_version: 0,
      state: { ...state, next_action: "dell only" } });

    const thirdTask = { ...base, native_task_id: `continuity-third-${randomUUID()}` };
    assert.equal((await invoke("codex-read-recovery", thirdTask)).found, false,
      "another native task must not read the original task");
    await invoke("codex-checkpoint", { ...thirdTask, idempotency_key: randomUUID(),
      expected_version: 0, state: { ...state, next_action: "third task only" } });

    await invoke("codex-checkpoint", { ...base, idempotency_key: randomUUID(), expected_version: 1,
      state: { ...state, next_action: "joe version two" } });
    const joeRecovered = await invoke("codex-read-recovery", base);
    const dellRecovered = await invokeAs(dellActor, "codex-read-recovery",
      { ...base, project_id: "dell-project", cwd: "/dell" });
    const otherRecovered = await invoke("codex-read-recovery", internalOther);
    const thirdRecovered = await invoke("codex-read-recovery", thirdTask);
    assert.equal(Number(joeRecovered.checkpoint.checkpoint_version), 2);
    assert.equal(joeRecovered.checkpoint.state.next_action, "joe version two");
    assert.equal(Number(dellRecovered.checkpoint.checkpoint_version), 1);
    assert.equal(dellRecovered.checkpoint.state.next_action, "dell only");
    assert.equal(Number(otherRecovered.checkpoint.checkpoint_version), 1);
    assert.equal(otherRecovered.checkpoint.state.next_action, "internal only");
    assert.equal(Number(thirdRecovered.checkpoint.checkpoint_version), 1);
    assert.equal(thirdRecovered.checkpoint.state.next_action, "third task only");

    const verify = await pool.connect();
    try {
      const rows = await verify.query(
        `select organization_tenant_id,project_id,checkpoint_version,state->>'next_action' as next_action
           from codex_continuity_checkpoint
          where native_task_id=$1 order by organization_tenant_id`, [otherTask]);
      assert.deepEqual(rows.rows.map(row => ({
        tenant: row.organization_tenant_id, project: row.project_id,
        version: Number(row.checkpoint_version), next: row.next_action,
      })), [
        { tenant: "carr-internal", project: "integration", version: 1, next: "internal only" },
        { tenant: foreignTenant, project: "foreign-project", version: 1, next: "foreign only" },
      ]);
    } finally {
      verify.release();
    }
  } finally {
    await pool.end();
  }
});
