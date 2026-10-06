import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { allowedIn } from "../src/mcp.js";
import { TOOLS, executeRegisteredTool } from "../src/tools.js";

const actor = {
  id: "11111111-1111-4111-8111-111111111111",
  slug: "joe-local",
  authorization_class: "machine",
  organization_tenant_id: "carr",
};

test("writer runtime preflight is a full-profile read routed only through the writer binding", () => {
  const tool = TOOLS["engineering-writer-runtime-preflight"];
  assert.ok(tool);
  assert.equal(tool.write, undefined);
  assert.equal(tool.writerConnection, true);
  assert.equal(tool.fullOnly, true);
  assert.equal(allowedIn("full", "engineering-writer-runtime-preflight", tool), true);
  assert.equal(allowedIn("read", "engineering-writer-runtime-preflight", tool), false);
  assert.equal(tool.inputSchema.additionalProperties, false);
  const dispatcher = readFileSync(new URL("../src/mcp.js", import.meta.url), "utf8");
  assert.match(dispatcher, /!tool\.write && !tool\.writerConnection/);
  assert.match(dispatcher, /tool\.writerConnection && !tool\.write \? "begin read only" : "begin"/);
});

test("writer runtime preflight returns only identity and bounded privilege booleans", async () => {
  const client = { query: async (sql) => {
    assert.match(sql, /session_user::text/);
    assert.match(sql, /engineering_enqueue_slice_job\(text,text,text,text,integer\)/);
    return { rows: [{
      session_user: "app_writer", current_user: "app_writer", database: "neondb",
      transaction_read_only: "on", member_carr_writer: true,
      select_engineering_slice_plan: true, insert_engineering_execution_envelope: true,
      execute_engineering_passport_facts: true, execute_engineering_enqueue_slice_job: true,
    }] };
  } };
  const result = await executeRegisteredTool(client, actor, "engineering-writer-runtime-preflight", {});
  assert.equal(result.ok, true);
  assert.deepEqual(result.identity, { session_user: "app_writer", current_user: "app_writer", database: "neondb" });
  assert.equal(Object.keys(result).length, 3);
  assert.equal(JSON.stringify(result).includes("password"), false);
  assert.equal(JSON.stringify(result).includes("postgres"), false);
});

test("writer runtime preflight reports a wrong effective role without widening it", async () => {
  const client = { query: async () => ({ rows: [{
    session_user: "unexpected_writer", current_user: "unexpected_writer", database: "neondb",
    transaction_read_only: "on", member_carr_writer: false,
    select_engineering_slice_plan: false, insert_engineering_execution_envelope: false,
    execute_engineering_passport_facts: false, execute_engineering_enqueue_slice_job: false,
  }] }) };
  const result = await executeRegisteredTool(client, actor, "engineering-writer-runtime-preflight", {});
  assert.equal(result.ok, false);
  assert.equal(result.identity.current_user, "unexpected_writer");
  assert.equal(result.checks.identity_is_app_writer, false);
  assert.equal(result.checks.select_engineering_slice_plan, false);
});
