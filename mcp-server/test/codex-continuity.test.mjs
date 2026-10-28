// Tests authenticated Codex continuity tools and checkpoint handling.
import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import { codexContinuityTools } from "../src/codex-continuity.js";
import { TOOLS } from "../src/tools.js";
import { digest } from "../src/artifact-trust.js";
import { splitReferenceManifest, storedJsonBytes } from "../continuity-reference-manifest.mjs";
import { agentActorForToken, authenticatedIdentity, continuityActorForTokenMaps } from "../src/identity.js";
const actorFromProps = (props, bindings = null) =>
  authenticatedIdentity.connectionForGrant(props, bindings);

class TestToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}
const actor = { id: "actor-codex", slug: "codex", human: false, native_agent_verified: true,
  sponsoring_human_slug: "joe", via: "oauth-google", continuity_surface: "codex" };
const key = "00000000-0000-4000-8000-000000000001";
const state = { objective: "keep working", next_action: "verify result", progress: [{ text: "started", refs: [] }] };

function validStateAt(bytes) {
  const value = {
    objective: "x".repeat(4000), next_action: "continue",
    constraints: Array.from({ length: 6 }, (_item, index) => ({ text: index < 5 ? "x".repeat(3900) : "" })),
  };
  value.constraints[5].text = "x".repeat(bytes - storedJsonBytes(value));
  assert.ok(value.constraints[5].text.length <= 4000);
  assert.equal(storedJsonBytes(value), bytes);
  return value;
}

function tools() {
  return codexContinuityTools({
    ToolError: TestToolError,
    assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    writeEvent: async () => {},
  });
}

test("list-my-codex-sessions reads only first-hand checkpoints for the authenticated sponsor", async () => {
  const seen = [];
  const nativeId = "01a0eca8-f7f3-7153-a0a9-7f53dffb25b0";
  const client = { query: async (sql, params) => {
    seen.push({ sql, params });
    assert.match(sql, /from codex_continuity_checkpoint/);
    assert.match(sql, /owner_actor_id=\(select id from actor where slug=\$2\)/);
    assert.doesNotMatch(sql, /codex_continuity_event|session_identity/);
    return { rows: params[1] === "joe" ? [{ native_task_id: nativeId,
      checkpoint_version: "3", updated_at: "2026-09-29T12:00:00Z" }] : [] };
  } };
  const verb = tools()["list-my-codex-sessions"];
  assert.deepEqual(verb.inputSchema.required ?? [], []);
  assert.deepEqual(Object.keys(verb.inputSchema.properties), []);
  const joe = await verb.handler(client, { id: "joe-id", slug: "joe", human: true }, {});
  assert.deepEqual(joe.sessions, [{ native_session_id: nativeId, host: "codex_desktop",
    availability: "checkpoint_recorded", checkpoint_version: 3,
    updated_at: "2026-09-29T12:00:00Z" }]);
  const sponsoredApp = await verb.handler(client, { id: "app-id", slug: "codex", human: false,
    via: "oauth-google", sponsoring_human_slug: "joe", sponsor_required: true }, {});
  assert.deepEqual(sponsoredApp.sessions, joe.sessions,
    "the server-derived sponsor, not the app actor's own slug, owns the checkpoint");
  const dell = await verb.handler(client, { id: "dell-id", slug: "dell", human: true }, {});
  assert.deepEqual(dell.sessions, [], "Dell cannot see Joe's checkpoint");
  assert.deepEqual(seen.map(call => call.params), [["carr-internal", "joe"],
    ["carr-internal", "joe"], ["carr-internal", "dell"]]);
  await assert.rejects(() => verb.handler(client, { id: "joe-id", slug: "joe", human: true },
    { sponsoring_human_slug: "dell" }), error =>
    error.payload?.error === "codex_session_list_arguments_invalid");
  assert.equal(seen.length, 3, "caller supplied identity must be refused before any database query");
  await assert.rejects(() => verb.handler(client, { id: "unbound", slug: "codex", human: false,
    via: "oauth-google" }, {}), error => error.payload?.error === "codex_sponsor_required");
});

test("Codex OAuth and surface-isolated local transport share the verified owner key", () => {
  const local = agentActorForToken("Bearer local-secret", JSON.stringify({ "joe-local": "local-secret" }), "local-token");
  const continuity = continuityActorForTokenMaps(
    "Bearer codex-secret", JSON.stringify({ joe: "codex-secret" }));
  const oauth = actorFromProps({ slug: "codex", human: false, client_id: "client-1", sponsoring_human_slug: "joe", via: "oauth-google" }, JSON.stringify({ "client-1": "codex" }));
  assert.equal(local.native_agent_verified, true);
  assert.equal(local.sponsoring_human_slug, "joe");
  assert.equal(local.continuity_surface, undefined);
  assert.equal(continuity.native_agent_verified, true);
  assert.equal(continuity.continuity_surface, "codex");
  assert.equal(continuity.sponsoring_human_slug, "joe");
  assert.equal(oauth.native_agent_verified, true);
  assert.equal(oauth.continuity_surface, "codex");
  assert.equal(oauth.sponsoring_human_slug, "joe");
});
