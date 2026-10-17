// THE RESEARCH-SITE WRITE VERBS REACH THE SCHEDULED PROFILES (#1646 review,
// finding 6). The weekly research tasks are told to add a useful source with
// add-research-site and retire a dead one with remove-research-site, and they
// run on the narrow `capture` / `away` profiles. Before this, both closed write
// sets left the two verbs out, so callTool refused them with not_in_profile
// while list-research-sites (a read) passed.
//
// What this pins:
//   1. Both write verbs are allowed under capture and away.
//   2. A real callTool dispatch under each profile clears the profile gate and
//      reaches the handler: a valid add is decided past not_in_profile.
//   3. The grant stops there: read, probe, reviewer and hermes still refuse.
import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { PROFILES, allowedIn, callTool } from "../src/mcp.js";
import { RESEARCH_SITE_WRITE_VERBS } from "../src/research-sites.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth", client_id: "claude-ai" };

const SCHEDULED = ["capture", "away"];
const NARROWER = ["read", "probe", "reviewer", "hermes"];

const ARGS = {
  "add-research-site": { idempotency_key: "00000000-0000-4000-8000-0000000016f6",
    host: "research.example.org", topics: ["npi"] },
  "remove-research-site": { idempotency_key: "00000000-0000-4000-8000-0000000016f7",
    host: "research.example.org", reason: "dead source" },
};

async function gateError(profile, name) {
  try {
    await callTool({}, joe, name, { ...ARGS[name] }, profile);
    return null;
  } catch (e) {
    return e instanceof ToolError ? e.payload : { error: "non_tool_error", message: e?.message };
  }
}

test("both research-site write verbs are in the capture and away write sets", () => {
  assert.deepEqual([...RESEARCH_SITE_WRITE_VERBS].sort(), ["add-research-site", "remove-research-site"]);
  for (const profile of SCHEDULED)
    for (const name of RESEARCH_SITE_WRITE_VERBS) {
      assert.ok(PROFILES[profile].has(name), `${name} missing from ${profile}`);
      assert.equal(allowedIn(profile, name, TOOLS[name]), true, `${name} under ${profile}`);
    }
});

test("a scheduled-profile dispatch clears the profile gate and reaches the handler", async () => {
  // There is no database here, so the call fails later (on the absent writer
  // credential). Any refusal other than not_in_profile proves it got past the
  // gate, which is what the scheduled runs need.
  for (const profile of SCHEDULED)
    for (const name of RESEARCH_SITE_WRITE_VERBS) {
      const payload = await gateError(profile, name);
      assert.notEqual(payload?.error, "not_in_profile", `${name} under ${profile}: ${JSON.stringify(payload)}`);
    }
});

test("the narrower profiles still refuse both write verbs by name", async () => {
  for (const profile of NARROWER)
    for (const name of RESEARCH_SITE_WRITE_VERBS) {
      assert.equal(allowedIn(profile, name, TOOLS[name]), false, `${name} under ${profile}`);
      const payload = await gateError(profile, name);
      assert.equal(payload?.error, "not_in_profile", `${name} under ${profile}`);
      assert.equal(payload?.verb, name);
    }
});

test("the read stays open everywhere it already was", () => {
  for (const profile of [...SCHEDULED, ...NARROWER])
    assert.equal(allowedIn(profile, "list-research-sites", TOOLS["list-research-sites"]), true, profile);
});
