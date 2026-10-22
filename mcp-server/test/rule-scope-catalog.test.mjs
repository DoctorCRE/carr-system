import { test } from "node:test";
import assert from "node:assert/strict";
import { executeRegisteredTool } from "../src/tools.js";

const JOE = { slug: "joe", human: true, via: "oauth-google" };
const rows = [
  { id: "12345678-0000-4000-8000-000000000000", statement: "Shared text.", scope: { moment: "Before replying" }, personal_to: null, taught_by: "Joe", human_quote: "Original quote" },
  { id: "23456789-0000-4000-8000-000000000000", statement: "Personal text.", scope: { tools: ["Write"] }, personal_to: "joe" },
  { id: "34567890-0000-4000-8000-000000000000", statement: "Introduction text.", scope: { kind: "intro_politics", moment: "Before introductions" }, personal_to: null },
];
function client() {
  return { query: async (sql, args) => {
    assert.match(sql, /standing-context:scope-catalog/);
    assert.match(sql, /personal_to is null/);
    assert.match(sql, /personal_to = \$1/);
    assert.doesNotMatch(sql, /intro_politics|rule_load_layer|rule_pack/);
    assert.deepEqual(args, ["joe"]);
    return { rows: structuredClone(rows) };
  } };
}
test("scope catalog returns authoritative scopes and complete text including introduction rules", async () => {
  const result = await executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes" });
  assert.equal(result.schema, "rule-scope-catalog/v1");
  assert.deepEqual(result.rules, rows);
  assert.equal(result.identity.sponsoring_human_id, "joe");
  assert.match(result.corpus_digest, /^[0-9a-f]{64}$/);
  assert.equal(result.corpus_digest, (await executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes" })).corpus_digest);
});
test("catalog rejects caller-selected sponsor and tenant before reading", async () => {
  for (const fields of [{ partner: "dell" }, { tenant_id: "other" }]) {
    await assert.rejects(executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes", ...fields }));
  }
});
test("store outage is an error and a subsequent call reads the store again", async () => {
  await assert.rejects(executeRegisteredTool({ query: async () => { throw new Error("offline"); } }, JOE, "standing-context", { detail: "scopes" }), /offline/);
  assert.equal((await executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes" })).rules.length, 3);
});
test("conditional catalog omits text only for the current digest and exact authenticated identity", async () => {
  const original = await executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes" });
  const scope_cache = { corpus_digest: original.corpus_digest, identity: original.identity };
  const unchanged = await executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes", scope_cache });
  assert.deepEqual(unchanged, { ok: true, schema: original.schema, corpus_digest: original.corpus_digest,
    identity: original.identity, not_modified: true });
  for (const candidate of [
    { ...scope_cache, corpus_digest: "0".repeat(64) },
    { ...scope_cache, identity: { ...original.identity, sponsoring_human_id: "dell" } },
    { ...scope_cache, identity: { ...original.identity, agent_principal_id: "other" } },
    { ...scope_cache, identity: { ...original.identity, runtime_principal: "other" } },
    { ...scope_cache, identity: { ...original.identity, organization_tenant_id: "other" } },
  ]) {
    const refreshed = await executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes", scope_cache: candidate });
    assert.deepEqual(refreshed.rules, rows);
    assert.equal(refreshed.not_modified, undefined);
  }
  const changed = { query: async () => ({ rows: rows.map(r => ({ ...r, statement: r.statement + " Revised." })) }) };
  const refreshed = await executeRegisteredTool(changed, JOE, "standing-context", { detail: "scopes", scope_cache });
  assert.notEqual(refreshed.corpus_digest, original.corpus_digest);
  assert.equal(refreshed.rules.length, rows.length);
});
test("switching authenticated sponsors cannot confirm the previous sponsor's empty catalog", async () => {
  const client = { query: async (_sql, args) => {
    assert.ok(["joe", "dell"].includes(args[0]));
    return { rows: [] };
  } };
  const joe = await executeRegisteredTool(client, JOE, "standing-context", { detail: "scopes" });
  const dell = await executeRegisteredTool(client, { ...JOE, slug: "dell" }, "standing-context", {
    detail: "scopes", scope_cache: { identity: joe.identity, corpus_digest: joe.corpus_digest },
  });
  assert.equal(dell.identity.sponsoring_human_id, "dell");
  assert.notEqual(dell.corpus_digest, joe.corpus_digest);
  assert.deepEqual(dell.rules, []);
  assert.equal(dell.not_modified, undefined);
});
