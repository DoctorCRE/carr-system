import assert from "node:assert/strict";
// Test lead history continuation cursors and malformed cursor rejection.
import test from "node:test";
import { assertRegisteredToolInput, ToolError, tool } from "./helpers/legacy-test-registry.ts";

test("F7 registered workspace admits independent history continuation cursors", async () => {
  await assertRegisteredToolInput("lead-board", {
    workspace: "leads",
    lead_id: "30000000-0000-0000-0000-000000000001",
    correspondence_cursor: { occurred_at: "2026-01-01T10:00:00.123456Z", id: "30000000-0000-0000-0000-000000000002" },
    stage_history_cursor: "9007199254740993",
  });
});

test("F7 malformed cursors refuse before any history read", async () => {
  const c = {
    query: () => {
      throw new Error("invalid cursor reached database");
    },
  };
  for (const extra of [
    { correspondence_cursor: { occurred_at: "2026-02-31T10:00:00Z", id: "30000000-0000-0000-0000-000000000002" } },
    { correspondence_cursor: { occurred_at: "2026-01-01T10:00:00Z", id: "other" } },
    { correspondence_cursor: null },
    { stage_history_cursor: "9223372036854775808" },
    { stage_history_cursor: 1 },
    { stage_history_cursor: "1", workspace: undefined },
  ])
    await assert.rejects(
      () =>
        tool("lead-board").handler(
          c,
          {},
          {
            workspace: "leads",
            lead_id: "30000000-0000-0000-0000-000000000001",
            ...extra,
          },
        ),
      (error) => error instanceof ToolError && error.payload?.error === "invalid_history_cursor",
    );
});
