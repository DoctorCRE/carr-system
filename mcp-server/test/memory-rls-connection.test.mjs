import assert from "node:assert/strict";
import test from "node:test";
import { TOOLS } from "../src/tools.js";

// Migration 0573 fences personal memories with row security keyed to
// carr.sponsoring_human_slug, which only the writer transaction sets
// (mcp.js setWriterActorContext). A memory verb on the stateless reader
// connection would silently lose its owner's personal rows.
test("every verb that reads memory_item runs where the sponsor is set", () => {
  for (const name of ["recall-memory", "review-memory", "observe-memory",
                      "promote-memory", "correct-memory", "forget-memory"]) {
    const tool = TOOLS[name];
    assert.ok(tool, `${name} is registered`);
    assert.ok(tool.write === true || tool.writerConnection === true,
      `${name} must run on the writer transaction, or row security hides the partner's own personal memories`);
  }
});
