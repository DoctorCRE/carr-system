// Tests evidence binding for Work Request transitions.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, executeRegisteredTool } from "../src/tools.js";

test("retired engineering evidence binding cannot complete a Work Request", async () => {
  let queries = 0;
  const client = { query: async () => { queries++; return { rows: [] }; } };
  assert.equal(TOOLS["bind-work-request-criterion-evidence"], undefined);
  await assert.rejects(() => executeRegisteredTool(client, {}, "bind-work-request-criterion-evidence", {}),
    error => error.payload?.error === "unknown_tool");
  assert.equal(queries, 0);
  assert.ok(TOOLS["work-request-card"]);
  assert.ok(TOOLS["accept-outcome-feedback"]);
});
