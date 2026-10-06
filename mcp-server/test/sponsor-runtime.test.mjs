// Sponsor/runtime separation tests. These are intentionally pure/mock based:
// they prove that no caller argument selects a personal brain before a deploy.

import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  authenticatedIdentity,
  agentActorForToken,
  authorizationClassForActor,
  ORGANIZATION_TENANT_ID,
  personalScopeForActor,
  propsForSlug,
} from "../src/identity.js";

// `actorFromProps` is module-private under amendment 8 (PR 1013). The exported
// grant door is `authenticatedIdentity.connectionForGrant`; called without the
// server's witness it returns exactly the same actor, unbranded, which is what
// every case in this file is about.
const actorFromProps = (props, bindings = null) =>
  authenticatedIdentity.connectionForGrant(props, bindings);

import { doctrineTools, generatedRuleCount } from "../src/doctrine.js";
import { ToolError, auditIdentity } from "../src/tools.js";
import { callTool } from "../src/mcp.js";

// A deliberately non-production-sized corpus proves count derivation without
// turning today's live rule count into a fixture contract.
const SAMPLE_SHARED_RULE_COUNT = 7;
const shared = Array.from({ length: SAMPLE_SHARED_RULE_COUNT }, (_, n) => ({
  id: `shared-${n}`, statement: `Shared rule ${n} carries enough text for a gist response.`,
  human_quote: null, taught_by: "joe", personal_to: null, scope: {},
}));
const joePersonal = Array.from({ length: 30 }, (_, n) => ({
  id: `joe-${n}`, statement: `Joe personal rule ${n} carries enough text for a gist response.`,
  human_quote: null, taught_by: "joe", personal_to: "joe", scope: {},
}));
const dellPersonal = [];

function actor(slug, sponsor = null, extra = {}) {
  return actorFromProps(propsForSlug(slug, {
    human: false,
    sponsoring_human_slug: sponsor,
    sponsor_required: Boolean(sponsor || extra.sponsor_required),
    via: extra.via || "oauth-google",
    ...extra,
  }));
}

function mockClient() {
  return { query: async (sql, params = []) => {
    if (sql.includes("select id from actor")) return { rows: [{ id: "actor-id" }] };
    if (sql.includes("from ops.v_guidance_registry_state")) return { rows: [{ state: "inactive" }] };
    if (sql.includes("from v_compiled_rules")) {
      const who = params[0];
      return { rows: [...shared, ...(who === "joe" ? joePersonal : who === "dell" ? dellPersonal : [])] };
    }
    if (sql.includes("from loop_item")) return { rows: [] };
    if (sql.includes("from rule")) return { rows: [] };
    if (sql.includes("from doctrine_meta")) return { rows: [{ generation: 1 }] };
    throw new Error(`unexpected mock query: ${sql}`);
  } };
}

function activeGuidanceClient() {
  const base = mockClient();
  return { query: async (sql, params = []) => {
    if (sql.includes("with guidance_registry as")) {
      return { rows: [{ state: "active", manifest_digest: "a".repeat(64),
        standing_rules: [
          { ...shared[0], guidance_type: "doctrine", is_constitution: true },
          { ...shared[1], guidance_type: "constraint", is_constitution: false },
          { ...joePersonal[0], guidance_type: "constraint", is_constitution: false },
        ],
        projection_summary: [
          { guidance_type: "constraint", active_items: 2, projection_digest: "b".repeat(64) },
          { guidance_type: "doctrine", active_items: 4, projection_digest: "c".repeat(64) },
          { guidance_type: "rubric", active_items: 3, projection_digest: "d".repeat(64) },
        ], mode: null, map_versions: 0, map_digest: null, tagged_rules: 0,
        delivery_plan: [], pack_index: [] }] };
    }
    if (sql.includes("from ops.v_guidance_registry_state")) {
      return { rows: [{ state: "active", manifest_digest: "a".repeat(64) }] };
    }
    if (sql.includes("from ops.standing_guidance")) {
      return { rows: [
        { ...shared[0], guidance_type: "doctrine", is_constitution: true },
        { ...shared[1], guidance_type: "constraint", is_constitution: false },
        { ...joePersonal[0], guidance_type: "constraint", is_constitution: false },
      ] };
    }
    if (sql.includes("from ops.v_guidance_projection_summary")) {
      return { rows: [
        { guidance_type: "constraint", active_items: 2, projection_digest: "b".repeat(64) },
        { guidance_type: "doctrine", active_items: 4, projection_digest: "c".repeat(64) },
        { guidance_type: "rubric", active_items: 3, projection_digest: "d".repeat(64) },
      ] };
    }
    return base.query(sql, params);
  } };
}

const standing = doctrineTools({ withEnvelope: async () => {}, writeEvent: async () => {}, ToolError })["standing-context"].handler;

test("Joe-sponsored Codex receives the complete sample shared corpus plus Joe-personal rules", async () => {
  const result = await standing(mockClient(), actor("codex", "joe"), {});
  assert.equal(result.shared_rules.length, SAMPLE_SHARED_RULE_COUNT);
  assert.equal(result.personal_rules.length, 30);
  assert.equal(result.recite, `Rules loaded: ${SAMPLE_SHARED_RULE_COUNT} shared, 30 joe-personal`);
  assert.deepEqual(result.identity, {
    organization_tenant_id: "carr-internal", sponsoring_human_id: "joe",
    agent_principal_id: "codex", runtime_principal: "codex", personal_brain_scope: "joe-personal",
    personal_scope_source: "verified_grant_sponsor", session_capability_profile: "unknown",
    operational_profile: "full", human_only_authority: false,
  });
});

test("active typed registry slims boot to constitution plus applicable constraints without hiding coverage", async () => {
  const result = await standing(activeGuidanceClient(), actor("codex", "joe"), {
    workflow: "fixture-workflow", surface: "codex", tier: "shared",
  });
  assert.equal(result.recite, `Rules loaded: 2 of ${SAMPLE_SHARED_RULE_COUNT} shared, 1 of 30 joe-personal`);
  assert.equal(result.shared_rules.length, 2);
  assert.equal(result.personal_rules.length, 1);
  assert.equal(result.guidance_registry.state, "active");
  assert.equal(result.guidance_registry.loaded.constitution, 1);
  assert.equal(result.guidance_registry.loaded.applicable_constraints, 2);
  assert.deepEqual(result.guidance_registry.deferred_by_type, { doctrine: 3, rubric: 3 });
  assert.equal(result.guidance_registry.manifest_digest, "a".repeat(64));
  assert.match(result.guidance_registry.hint, /search-doctrine-situations/);
});

test("connector counts match the exact shared, Joe, and Dell generated-render headers", async () => {
  // These are the two render header forms emitted today: section headers for
  // non-empty brains and the export footer for Dell's empty brain.
  const sharedRender = `**${SAMPLE_SHARED_RULE_COUNT} active rule(s), by section.** Recite the total; read the section`;
  const joeRender = "**30 active rule(s), by section.** Recite the total; read the section";
  const dellRender = "*Exported: 2026-08-11T12:00:06.876927+00:00 · 0 active rule(s)*";
  const joe = await standing(mockClient(), actor("codex", "joe"), {});
  const dell = await standing(mockClient(), actor("codex", "dell"), {});
  assert.equal(joe.shared_rules.length, generatedRuleCount(sharedRender));
  assert.equal(joe.personal_rules.length, generatedRuleCount(joeRender));
  assert.equal(dell.personal_rules.length, generatedRuleCount(dellRender));
  assert.equal(joe.identity.personal_brain_scope, "joe-personal");
  assert.equal(dell.identity.personal_brain_scope, "dell-personal");
});

test("Joe-sponsored Claude and a future approved runtime use Joe's verified brain", async () => {
  for (const runtime of ["claude", "grok"]) {
    const scope = personalScopeForActor(actor(runtime, "joe"));
    assert.deepEqual(scope, { status: "personal", sponsor: "joe", source: "verified_grant_sponsor" });
  }
  const claude = await standing(mockClient(), actor("claude", "joe"), {});
  assert.equal(claude.recite, `Rules loaded: ${SAMPLE_SHARED_RULE_COUNT} shared, 30 joe-personal`);
});

test("Dell-sponsored Codex selects Dell only, even when Dell currently has zero personal rules", async () => {
  const result = await standing(mockClient(), actor("codex", "dell"), {});
  assert.equal(result.shared_rules.length, SAMPLE_SHARED_RULE_COUNT);
  assert.equal(result.personal_rules.length, 0);
  assert.equal(result.recite, `Rules loaded: ${SAMPLE_SHARED_RULE_COUNT} shared, 0 dell-personal`);
  assert.equal(result.identity.personal_brain_scope, "dell-personal");
});

test("personal brain cannot be selected or spoofed by a tool argument", async () => {
  await assert.rejects(
    standing(mockClient(), actor("codex", "joe"), { partner: "dell" }),
    (error) => error instanceof ToolError && error.payload.error === "partner_not_selectable",
  );
  const spoofed = actorFromProps({ slug: "codex", human: false, human_slug: "mallory",
    sponsor_required: true, via: "oauth-google" });
  assert.equal(personalScopeForActor(spoofed).error, "missing_or_ambiguous_sponsor");
});

test("missing or disabled expected sponsor fails visibly; unsponsored agents are truthfully shared-only", async () => {
  const missing = actor("codex", null, { sponsor_required: true });
  assert.equal(personalScopeForActor(missing).error, "missing_or_ambiguous_sponsor");
  const background = actorFromProps({ slug: "grok", human: false, via: "agent-token" });
  const result = await standing(mockClient(), background, {});
  assert.equal(result.personal_rules.length, 0);
  assert.equal(result.identity.personal_brain_scope, "none");
  assert.equal(result.identity.sponsoring_human_id, null);
});

test("server-marked probe and reviewer runtimes remain locked, shared-only machine identities", () => {
  for (const [slug, marker, via, authorization] of [
    ["smoke-probe", "probe", "probe-token", "probe_agent"],
    ["codex-reviewer", "review", "review-token", "review_agent"],
    ["grok-reviewer", "review", "review-token", "review_agent"],
  ]) {
    const machine = { slug, human: false, via, [marker]: true };
    assert.deepEqual(personalScopeForActor(machine), {
      status: "none", sponsor: null, source: "server_machine_token",
    });
    assert.equal(authorizationClassForActor(machine), authorization);
    assert.equal(machine.human, false);
  }
  assert.equal(personalScopeForActor({ slug: "attacker", human: false, via: "probe-token" }).error,
    "invalid_runtime_principal");
  assert.equal(personalScopeForActor({ slug: "attacker", human: false, probe: true, via: "probe-token" }).error,
    "invalid_runtime_principal");
  assert.equal(personalScopeForActor({ slug: "attacker", human: false, review: true, via: "review-token" }).error,
    "invalid_runtime_principal");
  assert.equal(personalScopeForActor({ slug: "smoke-probe", human: false, probe: true, via: "agent-token" }).error,
    "invalid_runtime_principal");
});

test("MCP routing admits only registered machine identities before normal profile routing", async () => {
  for (const [slug, marker, via, profile] of [
    ["smoke-probe", "probe", "probe-token", "probe"],
    ["codex-reviewer", "review", "review-token", "reviewer"],
    ["grok-reviewer", "review", "review-token", "reviewer"],
  ]) {
    await assert.rejects(
      callTool({}, { slug, human: false, via, [marker]: true }, "new-deal", {}, profile),
      (error) => error instanceof ToolError && error.payload.error === "not_in_profile",
    );
  }
  await assert.rejects(
    callTool({}, { slug: "attacker", human: false, probe: true, via: "probe-token" }, "not-a-real-tool", {}, "probe"),
    (error) => error instanceof ToolError && error.payload.error === "invalid_runtime_principal",
  );
});

test("runtime attribution and sponsor provenance survive without capability inheritance or rule bodies", () => {
  const runtime = actor("codex", "joe");
  const audit = auditIdentity(runtime);
  assert.deepEqual(audit, {
    organization_tenant_id: "carr-internal", sponsoring_human_slug: "joe",
    personal_scope: "joe-personal", authorization_class: "sponsored_agent",
    // Program 4 Gap A2 (2026-08-14): null here because `runtime` was built by
    // hand (this test's own actor() helper), never decorated by mcp.js's
    // dispatch() the way a real request's actor would be.
    correlation_id: null,
  });
  assert.equal(authorizationClassForActor(runtime), "sponsored_agent");
  assert.equal(runtime.human, false);
  assert.equal(Object.hasOwn(audit, "personal_rules"), false);
  assert.equal(authorizationClassForActor(actorFromProps({ slug: "grok", human: false, via: "agent-token" })), "unsponsored_agent");
});

test("direct human OAuth remains sponsored by its own verified actor", () => {
  const joe = actorFromProps({ slug: "joe", via: "oauth-google" });
  assert.deepEqual(personalScopeForActor(joe), {
    status: "personal", sponsor: "joe", source: "verified_human_actor",
  });
});

test("legacy Codex grants with human_slug retain Joe scope without reconnect", () => {
  const legacy = actorFromProps({ slug: "codex", human: false, human_slug: "joe", via: "oauth-google" });
  assert.deepEqual(personalScopeForActor(legacy), {
    status: "personal", sponsor: "joe", source: "verified_grant_sponsor",
  });
});

test("local token (Phase 1, decision 97e76a2f) resolves joe-local to Joe's personal scope end to end", () => {
  // The whole point of local-verb.mjs's new HTTPS path: a LOCAL_TOKENS bearer
  // should behave, for personal-scope purposes, like Joe's own interactive
  // session — while still failing the humanOnly gate, proven in
  // identity.test.mjs. This test proves the OTHER half, through the exact
  // function the Worker calls (agentActorForToken), never a hand-built actor.
  const localActor = agentActorForToken(
    "Bearer whatever-the-fixture-secret-is",
    JSON.stringify({ "joe-local": "whatever-the-fixture-secret-is" }),
    "local-token",
  );
  assert.deepEqual(personalScopeForActor(localActor), {
    status: "personal", sponsor: "joe", source: "verified_grant_sponsor",
  });
  assert.equal(authorizationClassForActor(localActor), "sponsored_agent");
  assert.equal(localActor.human, false, "must never resolve human:true — that would reopen the PARTNER_TOKENS hole");
});

test("personal brain never changes the server authority class or request-side limiter", () => {
  const joeCodex = actor("codex", "joe", { operational_profile: "capture" });
  const dellCodex = actor("codex", "dell", { operational_profile: "full" });
  assert.equal(authorizationClassForActor(joeCodex), "sponsored_agent");
  assert.equal(authorizationClassForActor(dellCodex), "sponsored_agent");
  assert.equal(joeCodex.human, false);
  assert.equal(dellCodex.human, false);
});

test("tenant is server fixed and cannot be selected by an actor or tool payload", async () => {
  const spoofed = actor("codex", "joe", { organization_tenant_id: "another-tenant" });
  const result = await standing(mockClient(), spoofed, {});
  assert.equal(ORGANIZATION_TENANT_ID, "carr-internal");
  assert.equal(result.identity.organization_tenant_id, "carr-internal");
  assert.notEqual(result.identity.organization_tenant_id, spoofed.organization_tenant_id);
  await assert.rejects(
    standing(mockClient(), spoofed, { tenant_id: "another-tenant" }),
    (error) => error instanceof ToolError && error.payload.error === "tenant_not_selectable",
  );
});

test("audit migration and both write paths preserve actor, sponsor, scope, and authority without rule bodies", async () => {
  const [migration, tools] = await Promise.all([
    readFile(new URL("../../migrations/0095_sponsor_runtime_audit.sql", import.meta.url), "utf8"),
    readFile(new URL("../src/tools.js", import.meta.url), "utf8"),
  ]);
  for (const text of [migration, tools]) {
    assert.match(text, /sponsoring_human_slug/);
    assert.match(text, /personal_scope/);
    assert.match(text, /authorization_class/);
    assert.match(text, /organization_tenant_id/);
  }
  assert.match(tools, /insert into tool_call \(idempotency_key, verb, actor_id/);
  assert.match(tools, /insert into event \(occurred_at, actor_id, verb/);
  const auditHelper = tools.slice(tools.indexOf("export function auditIdentity"), tools.indexOf("async function withEnvelope"));
  assert.doesNotMatch(auditHelper, /statement|personal_rules|human_quote/);
  assert.ok(migration.lastIndexOf("do $$") < migration.lastIndexOf("commit;"),
    "0095 commits only after its postflight guard, so a guard failure rolls back DDL");
});
