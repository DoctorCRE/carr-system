// Test rule scope catalog access and store behavior.

import assert from "node:assert/strict";
import { test } from "node:test";

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Expected object");
  return Object.fromEntries(Object.entries(value));
}
const registry = record(await import(new URL("../src/tools.js", import.meta.url).href));
async function executeRegisteredTool(...args: unknown[]) {
  const invoke = registry.executeRegisteredTool;
  if (typeof invoke !== "function") throw new TypeError("Expected registered executor");
  return record(await invoke(...args));
}

const JOE = { slug: "joe", human: true, via: "oauth-google" };
const rows = [
  {
    id: "12345678-0000-4000-8000-000000000000",
    statement: "Shared text.",
    scope: { moment: "Before replying" },
    personal_to: null,
    taught_by: "Joe",
    human_quote: "Original quote",
  },
  {
    id: "23456789-0000-4000-8000-000000000000",
    statement: "Personal text.",
    scope: { tools: ["Write"] },
    personal_to: "joe",
  },
  {
    id: "34567890-0000-4000-8000-000000000000",
    statement: "Introduction text.",
    scope: { kind: "intro_politics", moment: "Before introductions" },
    personal_to: null,
  },
];
function client() {
  return {
    query: async (sql: string, args: unknown[]) => {
      assert.match(sql, /standing-context:scope-catalog/);
      assert.match(sql, /personal_to is null/);
      assert.match(sql, /personal_to = \$1/);
      assert.doesNotMatch(sql, /intro_politics|rule_load_layer|rule_pack/);
      assert.deepEqual(args, ["joe"]);
      return { rows: structuredClone(rows) };
    },
  };
}
test("scope catalog returns authoritative scopes and complete text including introduction rules", async () => {
  const result = await executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes" });
  assert.equal(result.schema, "rule-scope-catalog/v1");
  assert.deepEqual(result.rules, rows);
  assert.equal(record(result.identity).sponsoring_human_id, "joe");
  assert.match(text(result.corpus_digest), /^[0-9a-f]{64}$/);
  assert.equal(
    result.corpus_digest,
    (await executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes" })).corpus_digest,
  );
});
test("catalog rejects caller-selected sponsor and tenant before reading", async () => {
  for (const fields of [{ partner: "dell" }, { tenant_id: "other" }]) {
    await assert.rejects(executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes", ...fields }));
  }
});
test("store outage is an error and a subsequent call reads the store again", async () => {
  await assert.rejects(
    executeRegisteredTool(
      {
        query: async () => {
          throw new Error("offline");
        },
      },
      JOE,
      "standing-context",
      { detail: "scopes" },
    ),
    /offline/,
  );
  const result = (await executeRegisteredTool(client(), JOE, "standing-context", { detail: "scopes" })).rules;
  if (!Array.isArray(result)) throw new TypeError("Expected rules");
  assert.equal(result.length, 3);
});

function text(value: unknown): string {
  if (typeof value !== "string") throw new TypeError("Expected string");
  return value;
}
