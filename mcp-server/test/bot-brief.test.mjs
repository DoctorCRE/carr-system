import test from "node:test";
import assert from "node:assert/strict";

import { botBriefTools } from "../src/bot-brief.js";
import { ToolError } from "../src/tool-error.js";
import { assertNoCallerAuthorityFields } from "../src/tools.js";

const profile = (overrides = {}) => ({
  profile_key: "doc", display_name: "Doc", charter: ["routine operations"],
  current_model: null, current_desk: null, sponsor_scope: "shared",
  status: "parked", version: "1", ...overrides,
});

function clientFor({ rows = profile(), generation = "42", mode = "shadow", packs = [] } = {}) {
  const calls = [];
  return {
    calls,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (/from agent_profile/.test(sql)) return { rows: rows ? [rows] : [] };
      if (/from doctrine_meta/.test(sql)) return { rows: [{ generation }] };
      if (/rule_delivery_policy/.test(sql)) return { rows: [{ mode }] };
      if (/rule_pack_index/.test(sql)) return { rows: packs };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
}

const joe = { id: "joe-runtime", slug: "joe-local", human: false,
  sponsoring_human_slug: "joe", human_slug: "joe", authorization_class: "sponsored_agent",
  operational_profile: "hermes", via: "hermes-token" };
const dell = { id: "dell-runtime", slug: "dell-local", human: false,
  sponsoring_human_slug: "dell", human_slug: "dell", authorization_class: "sponsored_agent",
  operational_profile: "hermes", via: "hermes-token" };

const TOOLS = botBriefTools({ ToolError, assertNoCallerAuthorityFields });

test("bot-brief is read-only, typed, and rejects caller authority fields", () => {
  assert.ok(TOOLS["bot-brief"]);
  assert.notEqual(TOOLS["bot-brief"].write, true);
  assert.equal(TOOLS["bot-brief"].inputSchema.additionalProperties, false);
  for (const key of ["sponsor", "partner", "tenant", "runtime", "device", "capability", "authority"])
    assert.equal(Object.hasOwn(TOOLS["bot-brief"].inputSchema.properties, key), false);
});

test("bot-brief refuses unsafe or invalid profile versions before other source reads", async () => {
  for (const version of ["9007199254740993", 9007199254740992, "0", "1.5", "invalid", null]) {
    const client = clientFor({ rows: profile({ version }) });
    await assert.rejects(
      TOOLS["bot-brief"].handler(client, joe, { profile_key: "doc" }),
      error => error instanceof ToolError && error.payload.error === "profile_version_invalid",
    );
    assert.equal(client.calls.length, 1);
  }
});

test("packs are canonicalized before recognition and definition hashing", async () => {
  const a = await TOOLS["bot-brief"].handler(clientFor({
    packs: [{ pack: "system-watch", title: "System", triggers: ["x"], rule_count: "1" }],
  }), joe, { profile_key: "doc", packs: ["system-watch"] });
  const b = await TOOLS["bot-brief"].handler(clientFor({
    packs: [{ pack: "system-watch", title: "System", triggers: ["x"], rule_count: "1" }],
  }), joe, { profile_key: "doc", packs: ["SYSTEM-WATCH", "system-watch", " "] });
  assert.deepEqual(b.requested_packs, ["system-watch"]);
  assert.deepEqual(b.unknown_packs, []);
  assert.equal(a.definition_digest, b.definition_digest);
});

test("same definition digest, different instance digest for Joe and Dell", async () => {
  const args = { profile_key: "doc", packs: ["system-watch"] };
  const a = await TOOLS["bot-brief"].handler(clientFor(), joe, args);
  const b = await TOOLS["bot-brief"].handler(clientFor(), dell, args);
  assert.equal(a.definition_digest, b.definition_digest);
  assert.notEqual(a.instance_digest, b.instance_digest);
  assert.equal(a.identity.sponsoring_human_slug, "joe");
  assert.equal(b.identity.sponsoring_human_slug, "dell");
});

test("profile and brief never grant authority; local files and memory are non-authoritative", async () => {
  const out = await TOOLS["bot-brief"].handler(clientFor(), joe, { profile_key: "doc" });
  assert.equal(out.profile_grants_authority, false);
  assert.equal(out.brief_grants_authority, false);
  assert.equal(out.local_markdown_authoritative, false);
  assert.equal(out.local_memory_authoritative, false);
  assert.deepEqual(out.runtime_requirements, {
    skip_context_files: true, skip_memory: false,
    memory_mode: "native_non_authoritative", ephemeral_system_prompt: true,
  });
  assert.equal(out.tool_allowlist_source, "mcp tools/list");
  assert.equal(out.boot_sources.standing_context, "standing-context");
  assert.equal(out.boot_sources.read_profiles, "read-profiles");
});

test("caller fields cannot cross brains or widen identity", async () => {
  const client = clientFor();
  await assert.rejects(TOOLS["bot-brief"].handler(client, joe, {
    profile_key: "doc", sponsor: "dell", partner: "dell", tenant: "other",
    runtime: "other", device: "other", capability: "full", authority: "human",
  }), error => error instanceof ToolError);
  const out = await TOOLS["bot-brief"].handler(clientFor(), joe, { profile_key: "doc" });
  assert.equal(out.identity.sponsoring_human_slug, "joe");
  assert.equal(out.identity.organization_tenant_id, "carr-internal");
  assert.equal(out.identity.runtime_principal, "joe-local");
  assert.equal(out.identity.human_only_authority, false);
});

test("canonical authority aliases are rejected by the direct handler too", async () => {
  for (const field of ["identity", "actor", "audience", "capabilities", "action", "actions",
    "action_authority", "action_authorities", "allowed_actions", "authorization", "profile",
    "write", "writes_records", "calls_models", "call_models"]) {
    const client = clientFor();
    await assert.rejects(
      TOOLS["bot-brief"].handler(client, joe, { profile_key: "doc", [field]: "caller" }),
      error => error instanceof ToolError,
      `direct handler must refuse ${field}`,
    );
    assert.equal(client.calls.length, 0, `${field} must be refused before any DB query`);
  }
});

test("Bot-Brief-specific authority aliases are rejected before any DB query", async () => {
  for (const field of ["partner", "runtime", "device", "authority", "personal_brain_scope",
    "operational_profile", "session_capability_profile", "human_only_authority", "device_id",
    "profile_grants_authority", "brief_grants_authority"]) {
    const client = clientFor();
    await assert.rejects(
      TOOLS["bot-brief"].handler(client, joe, { profile_key: "doc", [field]: "caller" }),
      error => error instanceof ToolError,
      `direct handler must refuse ${field}`,
    );
    assert.equal(client.calls.length, 0, `${field} must be refused before any DB query`);
  }
});

test("an ambiguous sponsored OAuth actor fails closed instead of becoming shared", async () => {
  const actor = { ...joe, via: "oauth-google", sponsor_required: true,
    sponsoring_human_slug: null, human_slug: null };
  await assert.rejects(
    TOOLS["bot-brief"].handler(clientFor(), actor, { profile_key: "doc" }),
    error => error instanceof ToolError &&
      error.payload.error === "missing_or_ambiguous_sponsor" &&
      /reconnect|partner/i.test(error.payload.hint),
  );
});

test("unknown profile is a typed error and does not read other sources", async () => {
  const client = clientFor({ rows: null });
  await assert.rejects(
    TOOLS["bot-brief"].handler(client, joe, { profile_key: "missing" }),
    error => error instanceof ToolError && error.payload.error === "profile_not_found",
  );
  assert.equal(client.calls.length, 1);
});

test("parked profile preserves null model and status, and reports unknown packs", async () => {
  const out = await TOOLS["bot-brief"].handler(clientFor({
    rows: profile({ current_model: null, current_desk: null, status: "parked", version: "7" }),
    packs: [{ pack: "known", title: "Known", triggers: ["x"], rule_count: "2" }],
  }), joe, { profile_key: "doc", packs: ["known", "missing"] });
  assert.equal(out.profile.status, "parked");
  assert.equal(out.profile.model, null);
  assert.equal(out.profile.desk, null);
  assert.deepEqual(out.requested_packs, ["known", "missing"]);
  assert.deepEqual(out.unknown_packs, ["missing"]);
});

test("governed bot brief refuses a mismatched assigned profile or unavailable required context", async () => {
  const governedClient = (assignment, rendered) => ({
    query: async (sql, params = []) => {
      if (/from agent_profile/.test(sql)) return { rows: [profile()] };
      if (/from doctrine_meta/.test(sql)) return { rows: [{ generation: "42" }] };
      if (/rule_delivery_policy/.test(sql)) return { rows: [{ mode: "shadow" }] };
      if (/rule_pack_index/.test(sql)) return { rows: [] };
      if (/context_activation_brief_assignment/.test(sql)) {
        assert.match(sql, /with tenant_scope as materialized/);
        assert.deepEqual(params, ["carr-internal", "WR-7", "ctx-0123456789abcdef"]);
        return { rows: [{ profile_key: assignment }] };
      }
      if (/render_context_activation_for_brief/.test(sql)) {
        assert.match(sql, /with tenant_scope as materialized/);
        assert.deepEqual(params, ["carr-internal", "WR-7", "ctx-0123456789abcdef"]);
        return { rows: [{ items: rendered }] };
      }
      throw new Error(`unexpected query: ${sql}`);
    },
  });
  const args = { profile_key: "doc", work_request: "WR-7", activation_binding_id: "ctx-0123456789abcdef" };
  await assert.rejects(TOOLS["bot-brief"].handler(governedClient("other", []), joe, args), error => error.payload.error === "activation_profile_binding_mismatch");
  await assert.rejects(TOOLS["bot-brief"].handler(governedClient("doc", null), joe, args), error => error.payload.error === "required_context_render_refused");
  const brief = await TOOLS["bot-brief"].handler(governedClient("doc", [{ canonical_ref: "rule:one", delivery_mode: "on_demand_tool", content: "must not be inline" }]), joe, args);
  assert.equal(brief.bound_context.items[0].content, undefined);
  assert.equal(brief.bound_context.items[0].retrieval_tool, "render-context-activation");
});

const hermes = { id: "hermes-pilot-id", slug: "hermes-pilot", human: false, hermes: true,
  via: "hermes-token", sponsoring_human_slug: "joe", human_slug: "joe",
  authorization_class: "sponsored_agent", operational_profile: "hermes" };

function hermesClient(registration) {
  const calls = [];
  return {
    calls,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (/from agent_profile/.test(sql)) return { rows: [profile({
        profile_key: "deal-steward", display_name: "Deal Steward", status: "active",
        current_model: "xai-oauth/grok-4.6", current_desk: "hermes-desktop", version: "4",
      })] };
      if (/from doctrine_meta/.test(sql)) return { rows: [{ generation: "42" }] };
      if (/rule_delivery_policy/.test(sql)) return { rows: [{ mode: "shadow" }] };
      if (/rule_pack_index/.test(sql)) return { rows: [] };
      if (/hermes_runtime_admission_for_brief/.test(sql)) {
        assert.match(sql, /with tenant_scope as materialized/);
        assert.equal(params[0], "carr-internal");
        return { rows: [{ registration }] };
      }
      if (/context_activation_brief_assignment/.test(sql)) {
        assert.match(sql, /with tenant_scope as materialized/);
        assert.equal(params[0], "carr-internal");
        return { rows: [{ profile_key: "deal-steward" }] };
      }
      if (/render_context_activation_for_brief/.test(sql)) {
        assert.match(sql, /with tenant_scope as materialized/);
        assert.equal(params[0], "carr-internal");
        return { rows: [{ items: [] }] };
      }
      throw new Error(`unexpected query: ${sql}`);
    },
  };
}

async function environmentRegistration() {
  const binding = {
    provider_ref: "environment-provider:hermes-local:v1", provider_version: 1,
    provider_digest: `sha256:${"1".repeat(64)}`, requirement_digest: `sha256:${"2".repeat(64)}`,
    configuration_digest: `sha256:${"3".repeat(64)}`, backend_kind: "local",
    source_class: "built_in", isolation_class: "host_process",
    capability_refs: ["environment:exec", "environment:filesystem", "environment:process"],
    conformance_ref: "conformance-run:hermes-local-v1", conformance_digest: `sha256:${"4".repeat(64)}`,
  };
  const bytes = new TextEncoder().encode(JSON.stringify(Object.keys(binding).sort().reduce((out, key) => ({ ...out, [key]: binding[key] }), {})));
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  const bindingDigest = `sha256:${[...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
  return {
    environment_provider_ref: binding.provider_ref, environment_provider_version: binding.provider_version,
    environment_provider_digest: binding.provider_digest, environment_requirement_digest: binding.requirement_digest,
    environment_configuration_digest: binding.configuration_digest, environment_backend_kind: binding.backend_kind,
    environment_source_class: binding.source_class, environment_isolation_class: binding.isolation_class,
    environment_capability_refs: binding.capability_refs, environment_conformance_ref: binding.conformance_ref,
    environment_conformance_digest: binding.conformance_digest, environment_binding_digest: bindingDigest,
  };
}

test("Hermes Bot-Brief uses server-derived envelope identity only with an exact activation binding", async () => {
  const registration = {
    status: "registered", authorized: true, reason: "exact_server_envelope",
    registration_scope: "execution_envelope", grants_authority: false,
    runtime_registration_id: "envelope:00000000-0000-4000-8000-000000000000",
    runtime_principal: "runtime:deal-steward",
    agent_principal_id: "agent:deal-steward", organization_tenant_id: "carr-internal",
    sponsoring_human_slug: "joe", work_request: "WR-7", profile_version: 4,
    native_session_ref: "native:profile-deal-steward", surface: "hermes_desktop",
    adapter_id: "adapter:hermes-desktop", adapter_version: "v1",
    provider_id: "provider:xai-oauth", model_id: "model:xai-oauth/grok-4.6",
    configuration_fingerprint: `sha256:${"b".repeat(64)}`,
    capability_profile: "capability:metadata-only",
    read_only: true, envelope_digest: `sha256:${"a".repeat(64)}`,
    activation_binding_id: "ctx-0123456789abcdef", expires_at: "2099-08-25T12:00:00Z",
    device_binding_status: "not_asserted",
    operator_surface: "job-passport:context-activation",
    telemetry_ref: "observatory:activation-reliability:ctx-0123456789abcdef",
    ...await environmentRegistration(),
    credential: "must-not-cross-the-wire",
  };
  const out = await TOOLS["bot-brief"].handler(hermesClient(registration), hermes, {
    profile_key: "deal-steward", work_request: "WR-7", activation_binding_id: "ctx-0123456789abcdef",
  });
  assert.equal(out.identity.runtime_principal, "hermes-pilot");
  assert.equal(out.runtime_registration.runtime_principal, "runtime:deal-steward");
  assert.equal(out.runtime_registration.authorized, true);
  assert.equal(out.runtime_registration.surface, "hermes_desktop");
  assert.equal(out.runtime_registration.read_only, true);
  assert.equal(out.runtime_registration.grants_authority, false);
  assert.equal(out.runtime_registration.device_binding_status, "not_asserted");
  assert.equal(out.runtime_registration.credential, undefined);
  assert.equal(out.runtime_registration.activation_binding_id, "ctx-0123456789abcdef");
  assert.equal(out.profile.model, "xai-oauth/grok-4.6");
});

test("Hermes runtime admission fails closed for unknown, stale, or missing exact binding", async () => {
  for (const registration of [
    { status: "not_registered", authorized: false, reason: "runtime_or_activation_missing" },
    { status: "stale", authorized: false, reason: "activation_or_envelope_not_exact" },
  ]) {
    await assert.rejects(
      TOOLS["bot-brief"].handler(hermesClient(registration), hermes, {
        profile_key: "deal-steward", work_request: "WR-7", activation_binding_id: "ctx-0123456789abcdef",
      }),
      error => error instanceof ToolError && error.payload.error === "hermes_runtime_registration_refused",
    );
  }
  const unbound = await TOOLS["bot-brief"].handler(hermesClient({}), hermes, { profile_key: "deal-steward" });
  assert.deepEqual(unbound.runtime_registration, { status: "not_registered", authorized: false });
});

test("Hermes rejects a registered-looking projection that mismatches authenticated server identity", async () => {
  const base = {
    status: "registered", authorized: true, reason: "exact_server_envelope",
    registration_scope: "execution_envelope", grants_authority: false,
    runtime_registration_id: "envelope:00000000-0000-4000-8000-000000000000",
    runtime_principal: "runtime:deal-steward",
    agent_principal_id: "agent:deal-steward", organization_tenant_id: "other-tenant",
    sponsoring_human_slug: "joe", work_request: "WR-7", profile_version: 4,
    native_session_ref: "native:profile-deal-steward", surface: "hermes_desktop",
    adapter_id: "adapter:hermes-desktop", adapter_version: "v1",
    provider_id: "provider:xai-oauth", model_id: "model:xai-oauth/grok-4.6",
    configuration_fingerprint: `sha256:${"b".repeat(64)}`,
    capability_profile: "capability:metadata-only", read_only: true,
    envelope_digest: `sha256:${"a".repeat(64)}`,
    activation_binding_id: "ctx-0123456789abcdef", expires_at: "2099-08-25T12:00:00Z",
    device_binding_status: "not_asserted",
    operator_surface: "job-passport:context-activation",
    telemetry_ref: "observatory:activation-reliability:ctx-0123456789abcdef",
    ...await environmentRegistration(),
  };
  await assert.rejects(
    TOOLS["bot-brief"].handler(hermesClient(base), hermes, {
      profile_key: "deal-steward", work_request: "WR-7", activation_binding_id: "ctx-0123456789abcdef",
    }),
    error => error instanceof ToolError && error.payload.error === "hermes_runtime_registration_refused" &&
      error.payload.reason === "server_projection_invalid",
  );
});

test("Hermes rejects stale expiry or a profile version other than the live Bot-Brief profile", async () => {
  const valid = {
    status: "registered", authorized: true, registration_scope: "execution_envelope",
    grants_authority: false,
    runtime_registration_id: "envelope:00000000-0000-4000-8000-000000000000",
    runtime_principal: "runtime:deal-steward", agent_principal_id: "agent:deal-steward",
    organization_tenant_id: "carr-internal", sponsoring_human_slug: "joe",
    work_request: "WR-7", profile_version: 4,
    activation_binding_id: "ctx-0123456789abcdef",
    native_session_ref: "native:profile-deal-steward", surface: "hermes_desktop",
    adapter_id: "adapter:hermes-desktop", adapter_version: "v1",
    provider_id: "provider:xai-oauth", model_id: "model:xai-oauth/grok-4.6",
    configuration_fingerprint: `sha256:${"b".repeat(64)}`,
    capability_profile: "capability:metadata-only", read_only: true,
    envelope_digest: `sha256:${"a".repeat(64)}`,
    device_binding_status: "not_asserted", operator_surface: "job-passport:context-activation",
    telemetry_ref: "observatory:activation-reliability:ctx-0123456789abcdef",
    expires_at: "2099-08-25T12:00:00Z",
    ...await environmentRegistration(),
  };
  for (const registration of [
    { ...valid, profile_version: 3 },
    { ...valid, expires_at: "2020-01-01T00:00:00Z" },
    { ...valid, expires_at: "not-a-timestamp" },
  ]) await assert.rejects(
    TOOLS["bot-brief"].handler(hermesClient(registration), hermes, {
      profile_key: "deal-steward", work_request: "WR-7", activation_binding_id: "ctx-0123456789abcdef",
    }),
    error => error instanceof ToolError && error.payload.error === "hermes_runtime_registration_refused",
  );
});

test("Hermes refuses a forged execution-environment provider binding", async () => {
  const environment = await environmentRegistration();
  const registration = {
    status: "registered", authorized: true, registration_scope: "execution_envelope", grants_authority: false,
    runtime_registration_id: "envelope:00000000-0000-4000-8000-000000000000",
    runtime_principal: "runtime:deal-steward", agent_principal_id: "agent:deal-steward",
    organization_tenant_id: "carr-internal", sponsoring_human_slug: "joe", work_request: "WR-7", profile_version: 4,
    activation_binding_id: "ctx-0123456789abcdef", native_session_ref: "native:profile-deal-steward",
    surface: "hermes_desktop", adapter_id: "adapter:hermes-desktop", adapter_version: "v1",
    provider_id: "provider:xai-oauth", model_id: "model:xai-oauth/grok-4.6",
    configuration_fingerprint: `sha256:${"b".repeat(64)}`, capability_profile: "capability:metadata-only",
    read_only: true, envelope_digest: `sha256:${"a".repeat(64)}`, device_binding_status: "not_asserted",
    operator_surface: "job-passport:context-activation", telemetry_ref: "observatory:activation-reliability:ctx-0123456789abcdef",
    expires_at: "2099-08-25T12:00:00Z", ...environment,
    environment_provider_ref: "environment-provider:attacker:v1",
  };
  await assert.rejects(TOOLS["bot-brief"].handler(hermesClient(registration), hermes, {
    profile_key: "deal-steward", work_request: "WR-7", activation_binding_id: "ctx-0123456789abcdef",
  }), error => error instanceof ToolError && error.payload.error === "hermes_runtime_registration_refused");
});

test("Hermes cannot spoof sponsor, device, runtime, or activation authority before any query", async () => {
  const client = hermesClient({});
  await assert.rejects(
    TOOLS["bot-brief"].handler(client, hermes, {
      profile_key: "deal-steward", work_request: "WR-7", activation_binding_id: "ctx-0123456789abcdef",
      sponsor: "dell", device_id: "other-device", runtime: "runtime:other", authority: "full",
    }),
    error => error instanceof ToolError,
  );
  assert.equal(client.calls.length, 0);
});

test("bot-brief performs no database write", async () => {
  const client = clientFor();
  await TOOLS["bot-brief"].handler(client, joe, { profile_key: "doc" });
  assert.ok(client.calls.every(({ sql }) => /^\s*select/i.test(sql)),
    "all database interaction must be read-only SELECT");
});
