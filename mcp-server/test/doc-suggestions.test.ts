// Test Doc suggestion contracts and stale decision handling.

import assert from "node:assert/strict";
import test from "node:test";
import { Client } from "@neondatabase/serverless";
import { docSuggestionTools } from "../src/doc-suggestions.ts";
import { ToolError } from "../src/tool-error.ts";

const tools = docSuggestionTools({
  withEnvelope: (_c, _a, _name, _args, fn) => fn(),
  writeEvent: async () => {},
  ToolError,
});

test("suggestion contracts close caller authority fields and separate read, producer and human decisions", () => {
  const names = Object.keys(tools).sort();
  assert.deepEqual(names, [
    "complete-doc-suggestion-scan",
    "decide-doc-suggestion",
    "list-doc-suggestions",
    "propose-doc-correction",
    "suggest-doc-work",
  ]);
  for (const [name, tool] of Object.entries(tools)) {
    assert.equal(tool.inputSchema.additionalProperties, false, name);
    for (const forbidden of ["actor", "contributor", "source_at", "original_text", "status"]) {
      assert.equal(Object.hasOwn(tool.inputSchema.properties, forbidden), false, `${name}: ${forbidden}`);
    }
  }
  assert.equal(tools["suggest-doc-work"].authorityOnly, true);
  assert.equal(tools["complete-doc-suggestion-scan"].authorityOnly, true);
  assert.equal(tools["complete-doc-suggestion-scan"].write, true);
  assert.equal(tools["list-doc-suggestions"].writerConnection, true);
  assert.equal(Reflect.get(tools["list-doc-suggestions"], "write"), undefined);
  for (const name of ["decide-doc-suggestion", "propose-doc-correction"] as const) {
    assert.equal(tools[name].humanOnly, true);
    assert.equal(tools[name].writerConnection, true);
  }
});

test("a stale decision returns the current record without writing over it", async (t) => {
  const query = async () => ({
    rows: [{ result: { ok: false, reason_id: "version_conflict", current: { version: 4, polished_text: "Current" } } }],
  });
  const client = new Client();
  t.mock.method(client, "query", query);
  await assert.rejects(
    tools["decide-doc-suggestion"].handler(
      client,
      { id: "actor" },
      {
        suggestion_id: "a0000000-0000-4000-8000-000000000001",
        base_version: 3,
        choice: "dismiss",
        idempotency_key: "b0000000-0000-4000-8000-000000000001",
      },
    ),
    (error) => {
      if (!(error instanceof ToolError) || error.payload.error !== "version_conflict") return false;
      const current = error.payload.current;
      return typeof current === "object" && current !== null && "version" in current && current.version === 4;
    },
  );
});
