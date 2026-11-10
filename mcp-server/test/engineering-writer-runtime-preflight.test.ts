// Test that retired engineering writer preflight makes no database query.
import assert from "node:assert/strict";
import test from "node:test";
import { ToolError } from "../src/tool-error.ts";

function object(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
const legacy = object(await import(new URL("../src/tools.js", import.meta.url).href));
const TOOLS = object(legacy.TOOLS);
async function executeRegisteredTool(db: object, actor: object, name: string, args: object) {
  const execute = legacy.executeRegisteredTool;
  assert.ok(typeof execute === "function");
  return object(await execute(db, actor, name, args));
}

test("retired engineering writer preflight executes no database query", async () => {
  let queries = 0;
  const client = {
    query: async () => {
      queries++;
      return { rows: [] };
    },
  };
  assert.equal(TOOLS["engineering-writer-runtime-preflight"], undefined);
  await assert.rejects(
    () => executeRegisteredTool(client, {}, "engineering-writer-runtime-preflight", {}),
    (error) => error instanceof ToolError && error.payload?.error === "unknown_tool",
  );
  assert.equal(queries, 0);
});
