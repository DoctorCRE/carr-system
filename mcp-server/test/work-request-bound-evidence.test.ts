// Tests evidence binding for Work Request transitions.

import assert from "node:assert/strict";
import { test } from "node:test";
import { executeRegisteredTool, hasTool, ToolError } from "./helpers/registered-tool.ts";

test("retired engineering evidence binding cannot complete a Work Request", async () => {
  let queries = 0;
  const client = {
    query: async () => {
      queries++;
      return { rows: [] };
    },
  };
  assert.equal(hasTool("bind-work-request-criterion-evidence"), false);
  await assert.rejects(
    () => executeRegisteredTool(client, {}, "bind-work-request-criterion-evidence", {}),
    (error) => error instanceof ToolError && error.payload.error === "unknown_tool",
  );
  assert.equal(queries, 0);
  assert.ok(hasTool("work-request-card"));
  assert.ok(hasTool("accept-outcome-feedback"));
});
