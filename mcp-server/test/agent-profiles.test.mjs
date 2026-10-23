import test from "node:test";
import assert from "node:assert/strict";

import { agentProfileTools } from "../src/agent-profiles.js";
import { ToolError } from "../src/tool-error.js";

async function withEnvelope(_c, _actor, _verb, args, fn) {
  if (!args.idempotency_key) throw new ToolError({ error: "missing_idempotency_key" });
  return fn();
}
const writeEvents = [];
async function writeEvent(_c, _actor, verb, subjectType, subjectId, fields) {
  writeEvents.push({ verb, subjectType, subjectId, fields });
}

const TOOLS = agentProfileTools({ withEnvelope, writeEvent, ToolError });

const KEY = "0284aaaa-0000-4000-8000-000000000001";

function profileRow(overrides = {}) {
  return {
    id: "profile-uuid", profile_key: "builder", display_name: "Builder",
    charter: ["implementation"], current_model: null, current_desk: null,
    sponsor_scope: "shared", status: "unstaffed", version: "1", ...overrides,
  };
}

test("read-profiles returns the roster ordered by key, open to any caller", async () => {
  const client = { query: async (sql) => {
    assert.match(sql, /from agent_profile/);
    assert.match(sql, /order by profile_key/);
    return { rows: [profileRow(), profileRow({ profile_key: "doc", status: "parked" })] };
  } };
  const out = await TOOLS["read-profiles"].handler(client, { slug: "probe", human: false }, {});
  assert.equal(out.ok, true);
  assert.equal(out.total, 2);
  assert.equal(out.profiles[1].profile_key, "doc");
});
