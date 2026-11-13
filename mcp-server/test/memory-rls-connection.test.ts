// Tests sponsor-scoped connections for memory reads.
import assert from "node:assert/strict";
import test from "node:test";

const registered: unknown = await import(new URL("../src/tools.js", import.meta.url).href);
assert.ok(typeof registered === "object" && registered !== null && "TOOLS" in registered);
assert.ok(typeof registered.TOOLS === "object" && registered.TOOLS !== null);
const TOOLS: Record<string, unknown> = Object.fromEntries(Object.entries(registered.TOOLS));

// Migration 0573 fences personal memories with row security keyed to
// carr.sponsoring_human_slug, which only the writer transaction sets
// (mcp.js setWriterActorContext). A memory verb on the stateless reader
// connection would silently lose its owner's personal rows.
test("every verb that reads memory_item runs where the sponsor is set", () => {
  for (const name of ["recall-memory"]) {
    const tool = TOOLS[name];
    assert.ok(typeof tool === "object" && tool !== null, `${name} is registered`);
    assert.ok(
      ("write" in tool && tool.write === true) || ("writerConnection" in tool && tool.writerConnection === true),
      `${name} must run on the writer transaction, or row security hides the partner's own personal memories`,
    );
  }
});
