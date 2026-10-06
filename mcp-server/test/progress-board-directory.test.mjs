import { CURRENT_REGISTRY_VERSION } from "../../ops/scac-mutation-inventory.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { progressBoardSummary } from "../src/board-answers.js";
import { assertRegisteredOperation, mutationManifestIdentity } from "../src/mutation-registry.js";

const actor = { id: "10000000-0000-0000-0000-000000000009", slug: "codex",
  human: false, native_agent_verified: true, sponsoring_human_slug: "joe" };

test("v100 admits the directory's exact read contract and refuses caller scope injection", async () => {
  const tool = TOOLS["list-progress-boards"];
  assert.equal(mutationManifestIdentity().registry_version, CURRENT_REGISTRY_VERSION);
  const row = await assertRegisteredOperation("list-progress-boards", tool, {});
  assert.equal(row.write, false);
  await assert.rejects(() => assertRegisteredOperation("list-progress-boards", tool,
    { sponsoring_human_slug: "dell" }));
  await assert.rejects(() => assertRegisteredOperation("list-progress-boards", { ...tool, write: true }, {}));
});

test("published directory is a read-only typed, tenant and sponsor scoped projection", async () => {
  const tool = TOOLS["list-progress-boards"];
  assert.equal(tool.write, false);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(tool.inputSchema.properties, {});
  const calls = [];
  const client = { async query(sql, params) {
    calls.push([sql, params]);
    assert.match(sql, /organization_tenant_id=\$1 and sponsoring_human_slug=\$2/);
    assert.match(sql, /case when board_id='carr-v5' then 0 else 1 end/);
    return { rows: params[1] === "joe" ? [{ board_id: "carr-v5", updated_at: "2026-10-01T12:00:00Z",
      snapshot_json: { title: "System progress", project: "carr-v5", tasks: {
        a: { status: "running", title: "Synthetic build" }, b: { status: "done" }, c: { status: "running" },
      }, deliverables: ["Private detail excluded"] } }] : [] };
  } };
  assert.deepEqual(await tool.handler(client, actor, {}), {
    ok: true, schema: "progress-board-directory.v1", boards: [{ board_id: "carr-v5",
      title: "System progress", project: "carr-v5", updated_at: "2026-10-01T12:00:00Z",
      task_counts: { done: 1, running: 2 } }],
  });
  assert.deepEqual(calls[0][1], ["carr-internal", "joe"]);
  assert.deepEqual((await tool.handler(client, { ...actor, sponsoring_human_slug: "dell" }, {})).boards, []);
  assert.equal((await tool.handler(client, { ...actor, organization_tenant_id: "demo-other" }, {})).boards.length, 1);
  assert.deepEqual(calls.at(-1)[1], ["carr-internal", "joe"], "caller fields cannot override the authenticated organization");
});

test("directory refuses an unverified sponsor before any query", async () => {
  const client = { query() { assert.fail("unverified sponsor must not query"); } };
  await assert.rejects(() => TOOLS["list-progress-boards"].handler(client,
    { ...actor, native_agent_verified: false, sponsoring_human_slug: undefined }, {}),
  e => e instanceof ToolError && e.payload.error === "board_sponsor_unavailable");
});

test("summary counts status values without exposing task data and tolerates legacy snapshot shapes", () => {
  const base = { board_id: "demo-project", updated_at: "2026-09-01T00:00:00Z" };
  assert.deepEqual(progressBoardSummary(base), { ...base, title: "demo-project", project: "demo-project", task_counts: {} });
  for (const tasks of [null, [], "invalid"]) assert.deepEqual(progressBoardSummary({ ...base, snapshot_json: { tasks } }).task_counts, {});
  assert.deepEqual(progressBoardSummary({ ...base, snapshot_json: { tasks: {
    empty: {}, missing: null, invalid: "bad", array: [], whitespace: { status: " " },
    blocked: { status: "blocked" }, unknown: { status: "custom" }, prototype: { status: "__proto__" },
  } } }).task_counts, JSON.parse('{"__proto__":1,"blocked":1,"custom":1,"queued":2}'));
});
