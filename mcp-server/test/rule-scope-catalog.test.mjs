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
