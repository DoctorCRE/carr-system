import test from "node:test";
import assert from "node:assert/strict";
import { executeRegisteredTool, ToolError } from "../src/tools.js";

// Include machines with partner authority: the ordinary humanOnly gate admits
// those principals, but confirming an identity merge requires a human caller.
const machines = [
  { slug: "joe-local", human: false, via: "local-token", native_agent_verified: true,
    sponsoring_human_slug: "joe", client_id: "local-verb" },
  ...["codex", "claude"].map(slug => ({ slug, human: false, via: "oauth-agent",
    native_agent_verified: true, sponsoring_human_slug: "joe", client_id: "synthetic-client" })),
  { slug: "probe", human: false, probe: true, via: "probe-token" },
  { slug: "reviewer", human: false, review: true, via: "review-token" },
  { slug: "unknown-machine", human: false, via: "agent-token" },
];

for (const actor of machines) {
  for (const args of [{}, {
    idempotency_key: `synthetic-merge-${actor.slug}`,
    survivor_party: "20000000-0000-4000-8000-000000000001",
    merged_party: "20000000-0000-4000-8000-000000000002",
    match_basis: "matching synthetic phone and address",
  }]) {
  test(`confirm-merge refuses ${actor.slug} with ${Object.keys(args).length ? "valid" : "empty"} arguments before database access`, async () => {
    let queries = 0;
    const client = { query: async () => { queries++; throw new Error("unexpected database access"); } };
    await assert.rejects(() => executeRegisteredTool(client, actor, "confirm-merge", args), error => {
      assert.ok(error instanceof ToolError);
      assert.equal(error.payload.error, "human_only_verb_requires_verified_partner");
      assert.equal(error.payload.verb, "confirm-merge");
      return true;
    });
    assert.equal(queries, 0);
  });
  }
}
