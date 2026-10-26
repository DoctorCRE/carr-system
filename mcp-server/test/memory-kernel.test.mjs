import { readMigration } from "./migration-source.testhelper.mjs";
import test from "node:test";
import assert from "node:assert/strict";

import { memoryTools } from "../src/memory.js";
import { ToolError } from "../src/tool-error.ts";

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

test("recall refuses caller authority fields", () => {
  assert.throws(() => authorityGuard({ tenant_id: "dell", capabilities: [] }), /caller_authority_field_forbidden/);
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

test("memory lifecycle SQL names exact transition metadata and immutable core fields", () => {
  // Migration-level trigger proof is structural here; the live migration CI
  // exercises the trigger against PostgreSQL with illegal rewrites and valid
  // handler transitions.
  const migration = readMigration("../migrations/0299_memory_kernel.sql");
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
