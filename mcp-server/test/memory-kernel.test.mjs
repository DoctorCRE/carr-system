import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { memoryTools } from "../src/memory.js";
import { ToolError } from "../src/tool-error.js";

async function withEnvelope(_c, _actor, _verb, args, fn) {
  if (!args.idempotency_key) throw new ToolError({ error: "missing_idempotency_key" });
  return fn();
}
const events = [];
async function writeEvent(_c, _actor, verb, subjectType, subjectId, fields) {
  events.push({ verb, subjectType, subjectId, fields });
}
const authorityGuard = args => {
  const banned = ["tenant", "tenant_id", "identity", "actor", "audience", "capabilities", "actions", "write", "calls_models"];
  if (Object.keys(args || {}).some(key => banned.includes(key))) throw new ToolError({ error: "caller_authority_field_forbidden" });
};
const TOOLS = memoryTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields: authorityGuard });
async function strictEnvelope(c, _actor, _verb, args, fn) {
  if (!args.idempotency_key) throw new ToolError({ error: "missing_idempotency_key" });
  await c.query("envelope lookup");
  return fn();
}
const STRICT_TOOLS = memoryTools({ withEnvelope: strictEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields: authorityGuard });
const KEY = "0297aaaa-0000-4000-8000-000000000001";
const MEMORY_ID = "02970000-0000-4000-8000-000000000010";
const SUCCESSOR_ID = "02970000-0000-4000-8000-000000000011";
const JOE_ACTOR_ID = "02970000-0000-4000-8000-0000000000a0";
const UUID_SHAPE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

test("memory kernel exposes observe, recall, promote, correct, and forget", () => {
  for (const name of ["observe-memory", "recall-memory", "promote-memory", "correct-memory", "forget-memory"])
    assert.ok(TOOLS[name], name);
  assert.equal(TOOLS["recall-memory"].write, undefined);
  assert.equal(TOOLS["observe-memory"].write, true);
  // humanOnly LABEL RETIRED (WR-000019 slice S1, 2026-08-27): dead since
  // executeRegisteredTool stopped reading it 2026-08-26 (Joe's ruling, decision
  // dc57f62d); this slice drops the stale declaration in memory.js. The real
  // gate — humanOnlyDirect, kept as a documented no-op — is unchanged.
  assert.equal(TOOLS["promote-memory"].humanOnly, undefined);
  assert.equal(TOOLS["correct-memory"].humanOnly, undefined);
  assert.equal(TOOLS["forget-memory"].humanOnly, undefined);
  assert.ok(TOOLS["review-memory"]);
});

test("observe derives personal scope from the verified sponsor and stores provenance", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/resolve_memory_plan_anchor/.test(sql)) return { rows: [{ plan_id: "02970000-0000-4000-8000-000000000001", work_request_id: "wr-1", work_request_version: 3 }] };
    if (/select id from actor where slug=/.test(sql)) return { rows: [{ id: JOE_ACTOR_ID }] };
    if (/insert into memory_item/.test(sql)) return { rows: [{ id: "memory-1", version: 1, status: "candidate", scope: "personal" }] };
    if (/insert into memory_evidence/.test(sql)) return { rows: [{ id: "evidence-1" }] };
    return { rows: [] };
  } };
  const out = await TOOLS["observe-memory"].handler(client,
    { id: "runtime", slug: "codex", human: false, sponsoring_human_slug: "joe", sponsor_required: true },
    { idempotency_key: KEY, kind: "preference", statement: "Prefer concise summaries", context: "briefing", scope: "personal",
      plan_id: "02970000-0000-4000-8000-000000000001",
      evidence: { source_type: "conversation", source_ref: "turn-1", observation: "Joe corrected a verbose draft" } });
  assert.equal(out.ok, true);
  const lookup = statements.find(s => /select id from actor where slug=/.test(s.sql));
  assert.equal(lookup.params[0], "joe", "the owner slug still comes from the verified sponsor, never from args");
  const item = statements.find(s => /insert into memory_item/.test(s.sql));
  // owner_actor_id is a uuid column. Asserting the SLUG here is what let the
  // personal-scope bug ship: the mock client never type-checks, so "joe" in a
  // uuid parameter looked correct in tests and died at the real driver.
  assert.equal(item.params[8], JOE_ACTOR_ID, "personal owner is the sponsor's ACTOR ID, not their slug");
  assert.match(item.params[8], UUID_SHAPE, "owner_actor_id must be uuid-shaped");
  assert.match(item.sql, /organization_tenant_id/);
  assert.match(item.sql, /work_request_id/);
  assert.match(item.sql, /plan_id/);
  assert.match(item.sql, /actor_id/);
  const evidence = statements.find(s => /insert into memory_evidence/.test(s.sql));
  assert.equal(evidence.params[1], "conversation");
  assert.equal(evidence.params[2], "turn-1");
});

test("observe names an unprovisioned sponsor instead of writing a null owner", async () => {
  const client = { query: async (sql) => {
    if (/select id from actor where slug=/.test(sql)) return { rows: [] };
    if (/insert into memory_item/.test(sql)) throw new Error("must not insert without a resolved owner");
    return { rows: [] };
  } };
  await assert.rejects(() => TOOLS["observe-memory"].handler(client,
    { id: "runtime", slug: "codex", human: false, sponsoring_human_slug: "joe", sponsor_required: true },
    { idempotency_key: KEY, kind: "preference", statement: "x", scope: "personal",
      evidence: { source_type: "conversation", observation: "y" } }),
    error => error.payload?.error === "actor_not_provisioned" && error.payload?.slug === "joe");
});

test("observe leaves a shared memory unowned and never looks an owner up", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/insert into memory_item/.test(sql)) return { rows: [{ id: "memory-2", version: 1, status: "candidate", scope: "shared" }] };
    if (/insert into memory_evidence/.test(sql)) return { rows: [{ id: "evidence-2" }] };
    return { rows: [] };
  } };
  const out = await TOOLS["observe-memory"].handler(client,
    { id: "runtime", slug: "codex", human: false, sponsoring_human_slug: "joe", sponsor_required: true },
    { idempotency_key: KEY, kind: "preference", statement: "Shared voice note", scope: "shared",
      evidence: { source_type: "conversation", observation: "z" } });
  assert.equal(out.ok, true);
  // Matched on the whole statement, not a substring: the memory_item insert
  // carries its own `(select id from actor where slug=$10)` for the OBSERVER,
  // so a substring match here passes for the wrong reason.
  assert.equal(statements.some(s => /^\s*select id from actor where slug=/i.test(s.sql)), false,
    "a shared memory resolves no owner");
  const item = statements.find(s => /insert into memory_item/.test(s.sql));
  assert.equal(item.params[8], null, "shared scope stores a null owner_actor_id");
});

test("observe rejects a caller authority field and invalid memory kind before DB use", async () => {
  const client = { query: async () => { throw new Error("must not query"); } };
  await assert.rejects(() => TOOLS["observe-memory"].handler(client, { slug: "joe", human: true },
    { idempotency_key: KEY, kind: "not-a-kind", statement: "x", scope: "shared" }),
    error => error.payload?.error === "memory_kind_invalid");
});

test("recall is contextual and includes shared plus sponsor personal memories", async () => {
  const client = { query: async (sql, params) => {
    assert.match(sql, /memory_item/);
    assert.match(sql, /status='promoted'|status in \('promoted'/);
    assert.match(sql, /organization_tenant_id/);
    assert.match(sql, /context/);
    assert.match(sql, /public\.retrieval_visibility_actor_id/);
    assert.doesNotMatch(sql, /from actor|join actor/);
    assert.ok(params.includes("joe"));
    return { rows: [{ id: MEMORY_ID, status: "promoted", statement: "Concise summaries", scope: "personal", confidence: 0.9, relevance: 2 }] };
  } };
  const out = await TOOLS["recall-memory"].handler(client,
    { slug: "codex", human: false, sponsoring_human_slug: "joe", sponsor_required: true },
    { query: "briefing summary", context: "morning brief", limit: 10 });
  assert.equal(out.count, 1);
  assert.equal(out.memories[0].id, MEMORY_ID);
  assert.ok(!out.memories[0].evidence, "autonomous recall does not silently expose unreviewed candidate evidence");
});

test("candidate review returns evidence and current version, while authority fields cause zero queries", async () => {
  let queries = 0;
  const client = { query: async (sql) => { queries++; assert.match(sql, /memory_evidence/); assert.doesNotMatch(sql, /retrieval_visibility_actor_id/, "either partner may review any memory (0579)"); assert.doesNotMatch(sql, /from actor|join actor/); return { rows: [{ id: MEMORY_ID, status: "candidate", version: 2, evidence: [{ source_type: "conversation" }] }] }; } };
  const out = await TOOLS["review-memory"].handler(client, { slug: "joe", human: true }, { memory_id: MEMORY_ID });
  assert.equal(out.memory.status, "candidate");
  assert.equal(out.memory.version, 2);
  assert.equal(out.evidence[0].source_type, "conversation");
  await assert.rejects(() => TOOLS["recall-memory"].handler(client, { slug: "joe", human: true }, { query: "x", actor: "dell" }), /caller_authority_field_forbidden/);
  assert.equal(queries, 1);
});

test("promotion requires a fresh version and records the transition", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/update memory_item/.test(sql)) return { rows: [{ id: MEMORY_ID, version: 2, status: "promoted" }] };
    return { rows: [] };
  } };
  const out = await TOOLS["promote-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: MEMORY_ID, base_version: 1, reason: "confirmed twice" });
  assert.equal(out.memory.status, "promoted");
  assert.match(statements[0].sql, /where id=\$1 and version=\$2 and organization_tenant_id=\$3 and status='candidate'/);
  assert.match(statements[0].sql, /organization_tenant_id/);
});

test("correction creates immutable successor lineage and forget is human-only", async () => {
  const sqls = [];
  const client = { query: async (sql) => {
    sqls.push(sql);
    if (/update memory_item/.test(sql)) return { rows: [{ id: MEMORY_ID, version: 3, status: "corrected", statement: "Old" }] };
    if (/insert into memory_item/.test(sql)) return { rows: [{ id: SUCCESSOR_ID, version: 1, status: "candidate", predecessor_id: MEMORY_ID, statement: "Prefer short summaries" }] };
    return { rows: [] };
  } };
  await TOOLS["correct-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: MEMORY_ID, base_version: 2, statement: "Prefer short summaries", reason: "Joe corrected the preference" });
  assert.ok(sqls.every(sql => !/\bdelete\b/i.test(sql)));
  assert.match(sqls.find(sql => /insert into memory_item/.test(sql)), /predecessor_id/);
  sqls.length = 0;
  client.query = async sql => { sqls.push(sql); return { rows: [{ id: MEMORY_ID, version: 4, status: "forgotten" }] }; };
  await TOOLS["forget-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: MEMORY_ID, base_version: 3, reason: "no longer applies" });
  assert.ok(sqls.every(sql => !/\bdelete\b/i.test(sql)));
});

test("all write verbs use idempotency serialization and every mutation is owner/tenant scoped", () => {
  for (const name of ["observe-memory", "promote-memory", "correct-memory", "forget-memory"])
    assert.equal(TOOLS[name].write, true);
  // SQL contracts are asserted by the handler tests; this test locks the
  // direct-handler authority boundary separately from registered dispatch.
  assert.throws(() => authorityGuard({ tenant_id: "dell", capabilities: [] }), /caller_authority_field_forbidden/);
});

test("known private UUIDs stay partner-isolated in both recall and mutation predicates", async () => {
  const calls = [];
  const client = { query: async (sql, params) => {
    calls.push({ sql, params });
    if (/update memory_item/.test(sql)) return { rows: [] };
    return { rows: [] };
  } };
  await assert.rejects(() => TOOLS["promote-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: "02970000-0000-4000-8000-000000009999", base_version: 1, reason: "no" }),
    error => error.payload?.error === "memory_version_conflict_or_not_candidate");
  const mutation = calls.find(call => /update memory_item/.test(call.sql));
  assert.ok(mutation.sql.includes("owner_actor_id=(select id from actor where slug=$5)"));
  assert.equal(mutation.params[4], "joe");
  await TOOLS["recall-memory"].handler(client, { id: "dell", slug: "dell", human: true }, { query: "private" });
  const recall = calls.find(call => /select id, kind, statement/.test(call.sql));
  assert.equal(recall.params[1], "dell");
  assert.match(recall.sql, /organization_tenant_id=\$3/);
});

test("authority aliases are rejected before envelope/DB and nonhuman direct writes refuse before envelope", async () => {
  let queries = 0;
  const client = { query: async () => { queries++; return { rows: [] }; } };
  for (const alias of ["tenant", "tenant_id", "identity", "actor", "audience", "capabilities", "actions", "write", "calls_models"])
    await assert.rejects(() => STRICT_TOOLS["observe-memory"].handler(client, { slug: "joe", human: true },
      { idempotency_key: KEY, kind: "fact", statement: "x", scope: "shared", evidence: {}, [alias]: "injected" }),
      error => error.payload?.error === "caller_authority_field_forbidden");
  for (const name of ["promote-memory", "correct-memory", "forget-memory"])
    await assert.rejects(() => STRICT_TOOLS[name].handler(client, { slug: "codex", human: false }, { idempotency_key: KEY, memory_id: "m", base_version: 1, statement: "x", reason: "x" }),
      error => error.payload?.error !== "human_only");  // INVERTED: ruling dc57f62d, 2026-08-26
  assert.equal(queries, 0);
});

test("a server-verified sponsored Codex session crosses the direct memory transition gate", async () => {
  let queries = 0;
  const client = { query: async () => { queries++; return { rows: [] }; } };
  await assert.rejects(() => STRICT_TOOLS["promote-memory"].handler(client, {
    id: "runtime", slug: "codex", human: false, via: "oauth-google",
    sponsoring_human_slug: "joe", sponsor_required: true, native_agent_verified: true,
  }, {
    idempotency_key: KEY, memory_id: MEMORY_ID, base_version: 1, reason: "verified",
  }), error => error.payload?.error === "memory_version_conflict_or_not_candidate");
  assert.ok(queries > 0, "sponsored Codex passed partner authority and reached the scoped mutation");
});

test("sponsor failures are typed and happen before any query", async () => {
  let queries = 0;
  const client = { query: async () => { queries++; return { rows: [] }; } };
  await assert.rejects(() => TOOLS["recall-memory"].handler(client, { slug: "codex", human: false, via: "oauth-google", sponsor_required: true }, { query: "x" }),
    error => error.payload?.error === "missing_or_ambiguous_sponsor");
  await assert.rejects(() => TOOLS["recall-memory"].handler(client, { slug: "unknown", human: false }, { query: "x" }),
    error => error.payload?.error === "invalid_runtime_principal");
  assert.equal(queries, 0);
});

test("plan anchor is validated tenant-scoped and derives work request/version", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/resolve_memory_plan_anchor/.test(sql)) return { rows: [{ plan_id: "02970000-0000-4000-8000-000000000001", work_request_id: "wr-1", work_request_version: 7 }] };
    if (/insert into memory_item/.test(sql)) return { rows: [{ id: MEMORY_ID, status: "candidate", version: 1 }] };
    if (/insert into memory_evidence/.test(sql)) return { rows: [{ id: "e1" }] };
    return { rows: [] };
  } };
  await TOOLS["observe-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, kind: "fact", statement: "Plan-bound fact", scope: "shared", plan_id: "02970000-0000-4000-8000-000000000001",
      evidence: { source_type: "run", observation: "observed" } });
  const anchor = statements.find(s => /resolve_memory_plan_anchor/.test(s.sql));
  assert.match(anchor.sql, /resolve_memory_plan_anchor/);
  const insert = statements.find(s => /insert into memory_item/.test(s.sql));
  assert.match(insert.sql, /work_request_id/);
  assert.equal(insert.params.includes(7), true, "stored provenance uses plan's version, not caller input");
  assert.equal(insert.params[3], "02970000-0000-4000-8000-000000000001", "stored provenance uses resolver plan_id");
  assert.match(anchor.sql, /public\.resolve_memory_plan_anchor/);
});

test("unknown/cross-tenant plans and caller-assembled passport fields refuse before memory insert", async () => {
  const statements = [];
  const client = { query: async (sql) => { statements.push(sql); return { rows: [] }; } };
  await assert.rejects(() => TOOLS["observe-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, kind: "fact", statement: "x", scope: "shared", plan_id: "02970000-0000-4000-8000-000000009999", evidence: { source_type: "x", observation: "x" } }),
    error => error.payload?.error === "memory_plan_not_found_or_forbidden");
  await assert.rejects(() => TOOLS["observe-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, kind: "fact", statement: "x", scope: "shared", work_request: "WR-1", evidence: { source_type: "x", observation: "x" } }),
    error => error.payload?.error === "memory_plan_anchor_only");
  assert.equal(statements.some(sql => /insert into memory_item/.test(sql)), false);
});

test("raw UUID, limit, and reason validation fails before any query", async () => {
  let queries = 0;
  const client = { query: async () => { queries++; return { rows: [] }; } };
  for (const memory_id of ["not-a-uuid", ""]) {
    await assert.rejects(() => TOOLS["review-memory"].handler(client, { slug: "joe", human: true }, { memory_id }),
      error => error.payload?.error === "memory_id_invalid");
  }
  for (const limit of [NaN, 0, 101, 1.5, "nope"]) {
    await assert.rejects(() => TOOLS["recall-memory"].handler(client, { slug: "joe", human: true }, { query: "x", limit }),
      error => error.payload?.error === "memory_limit_invalid");
  }
  await assert.rejects(() => TOOLS["promote-memory"].handler(client, { slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: "not-a-uuid", base_version: 1, reason: " " }),
    error => error.payload?.error === "memory_id_invalid");
  assert.equal(queries, 0);
});

test("memory lifecycle SQL names exact transition metadata and immutable core fields", () => {
  // Migration-level trigger proof is structural here; the live migration CI
  // exercises the trigger against PostgreSQL with illegal rewrites and valid
  // handler transitions.
  const migration = readFileSync("../migrations/0299_memory_kernel.sql", "utf8");
  assert.match(migration, /memory_item_immutable_core/);
  assert.match(migration, /memory_item_insert_valid/);
  assert.match(migration, /new\.created_at := now\(\)/);
  assert.match(migration, /new\.status <> 'candidate'/);
  assert.match(migration, /prior\.status <> 'corrected'/);
  assert.match(migration, /new\.lineage_root_id is distinct from expected_root/);
  for (const field of ["kind", "context", "confidence", "observed_by_actor_id", "status", "version", "promoted_by_actor_id", "correction_reason", "forget_reason"])
    assert.match(migration, field === "version"
      ? /new\.version <> old\.version/
      : field === "status"
        ? /new\.status is not distinct from old\.status/
        : new RegExp(`new\\.${field}.*distinct from old\\.${field}`));
  assert.match(migration, /candidate.*promoted/);
  assert.match(migration, /promoted_by_actor_id/);
  assert.ok(migration.indexOf("has_function_privilege") < migration.lastIndexOf("commit;"), "privilege proof runs before transaction commit");
  assert.match(migration, /public\.resolve_memory_plan_anchor/);
  assert.match(migration, /grant execute on function public\.resolve_memory_plan_anchor/);
  assert.match(migration, /revoke all on function public\.resolve_memory_plan_anchor/);
});
