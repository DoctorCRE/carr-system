import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, canExercisePartnerAuthority } from "../src/tools.js";
import { authorityDsnForActor, callTool } from "../src/mcp.js";
import { authenticatedIdentity, agentActorForToken } from "../src/identity.js";
const actorFromProps = (props, bindings = null) =>
  authenticatedIdentity.connectionForGrant(props, bindings);


const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "test" };
const dell = { id: "10000000-0000-0000-0000-000000000003", slug: "dell", display: "Dell", human: true, via: "test" };
const NATIVE_BINDINGS = JSON.stringify({ "codex-client": "codex", "claude-client": "claude" });
const codexForJoe = { id: "10000000-0000-0000-0000-000000000004", slug: "codex", display: "Codex", human: false, sponsoring_human_slug: "joe", native_agent_verified: true, via: "oauth-agent", client_id: "codex-client" };
const claudeForDell = { id: "10000000-0000-0000-0000-000000000005", slug: "claude", display: "Claude", human: false, sponsoring_human_slug: "dell", native_agent_verified: true, via: "oauth-agent", client_id: "claude-client" };

test("authority DSNs follow verified partners and their sponsored Codex or Claude agents", () => {
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn" }, joe), "joe-dsn");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback" }, joe), "fallback");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_DELL_URL: "dell-dsn" }, dell), "dell-dsn");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "joe-fallback" }, dell), null);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn", CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, codexForJoe), "joe-dsn");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_DELL_URL: "dell-dsn", CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, claudeForDell), "dell-dsn");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback", CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, codexForJoe), "fallback");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback", CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, claudeForDell), null);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback" }, { slug: "codex", human: false }), null);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn" }, { slug: "grok", human: false, sponsoring_human_slug: "joe" }), null);
});

test("partners, sponsored native agents, and the local machine doors cross the partner boundary", () => {
  assert.equal(canExercisePartnerAuthority(joe), true);
  assert.equal(canExercisePartnerAuthority(dell), true);
  assert.equal(canExercisePartnerAuthority(codexForJoe), true);
  assert.equal(canExercisePartnerAuthority(claudeForDell), true);
  const joeLocal = agentActorForToken("Bearer joe-secret", '{"joe-local":"joe-secret"}', "local-token");
  const dellLocal = agentActorForToken("Bearer dell-secret", '{"dell-local":"dell-secret"}', "local-token");
  assert.equal(canExercisePartnerAuthority(joeLocal), true);
  assert.equal(canExercisePartnerAuthority(dellLocal), true);
  assert.equal(canExercisePartnerAuthority({ ...codexForJoe, native_agent_verified: false }), false);
  assert.equal(canExercisePartnerAuthority({ slug: "codex", human: false }), false);
  assert.equal(canExercisePartnerAuthority({ slug: "grok", human: false, sponsoring_human_slug: "joe" }), false);
  assert.equal(canExercisePartnerAuthority({ slug: "joe-local", human: false }), false);
});

test("native OAuth authority is recomputed from current client bindings on every request", () => {
  const storedGrant = { slug: "codex", human: false, human_slug: "joe", via: "oauth-agent",
    client_id: "codex-client", native_agent_verified: true };
  const current = actorFromProps(storedGrant, NATIVE_BINDINGS);
  assert.equal(current.native_agent_verified, true);
  assert.equal(canExercisePartnerAuthority(current), true);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn",
    CARR_NATIVE_AGENT_OAUTH_CLIENTS: NATIVE_BINDINGS }, current), "joe-dsn");
  const removed = actorFromProps(storedGrant, "{}");
  assert.equal(removed.native_agent_verified, undefined);
  assert.equal(canExercisePartnerAuthority(removed), false);
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn",
    CARR_NATIVE_AGENT_OAUTH_CLIENTS: "{}" }, storedGrant), null);
});

test("authority operation fails closed instead of falling back to writer credentials", async () => {
  await assert.rejects(() => callTool({ DATABASE_URL_WRITER: "writer-only" }, joe,
    "retire-workflow-cutover-plan", {
      idempotency_key: "authority-missing", plan_id: "10000000-0000-0000-0000-000000000001",
      disable_receipt_id: "10000000-0000-0000-0000-000000000002", reason: "fixture",
    }), e => e instanceof ToolError && e.payload.error === "authority_connection_unavailable");
});
