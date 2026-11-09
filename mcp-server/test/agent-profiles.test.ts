// Test agent profile roster reads.

import assert from "node:assert/strict";
import test from "node:test";

import { agentProfileTools } from "../src/agent-profiles.ts";

const TOOLS = agentProfileTools();

function profileRow(overrides = {}) {
  return {
    id: "profile-uuid",
    profile_key: "builder",
    display_name: "Builder",
    charter: ["implementation"],
    current_model: null,
    current_desk: null,
    sponsor_scope: "shared",
    status: "unstaffed",
    version: "1",
    ...overrides,
  };
}

test("read-profiles returns the roster ordered by key, open to any caller", async () => {
  const client = {
    query: async (sql: string) => {
      assert.match(sql, /from agent_profile/);
      assert.match(sql, /order by profile_key/);
      return { rows: [profileRow(), profileRow({ profile_key: "doc", status: "parked" })] };
    },
  };
  const out = await TOOLS["read-profiles"].handler(client);
  assert.equal(out.ok, true);
  assert.equal(out.total, 2);
  assert.equal(out.profiles[1]?.profile_key, "doc");
});
