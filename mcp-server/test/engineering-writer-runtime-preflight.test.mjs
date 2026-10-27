// Test that retired engineering writer preflight makes no database query.
import assert from "node:assert/strict";
import test from "node:test";
import { TOOLS, executeRegisteredTool } from "../src/tools.js";

test("retired engineering writer preflight executes no database query", async () => {
  let queries = 0;
  const client = { query: async () => { queries++; return { rows: [] }; } };
  assert.equal(TOOLS["engineering-writer-runtime-preflight"], undefined);
  await assert.rejects(() => executeRegisteredTool(client, {}, "engineering-writer-runtime-preflight", {}),
    error => error.payload?.error === "unknown_tool");
  assert.equal(queries, 0);
});
