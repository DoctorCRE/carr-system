import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import {
  canonicalExactEffectContract,
  deriveTrustedPrincipalBinding,
  ExactEffectRefusal,
  immutableExactEffectContracts,
  resolveExactEffects,
  SCAC_EXACT_EFFECT_CONTRACTS,
  SCAC_TRUSTED_PRINCIPAL_READBACK_SQL,
} from "../src/scac-exact-effects.js";
import { executeWithTrustedPrincipal } from "../src/mcp.js";
import { ToolError } from "../src/tools.js";
const migration = fs.readFileSync(new URL(
  "../../migrations/0469_siep18_exact_effects_trusted_principal.sql", import.meta.url), "utf8");

const contract = (ingress_key, direct_effects = [], delegates_to = [], extra = {}) => ({
  schema_version: "scac-exact-effect-contract.v1", ingress_key, direct_effects, delegates_to,
  sql_state: "static_reviewed", integration_state: "reviewed_source_test", ...extra,
});
const insert = (relation, columns) => ({ kind: "insert", relation, columns });
const update = (relation, columns) => ({ kind: "update", relation, columns });
const execute = function_signature => ({ kind: "execute", function_signature });

test("exact DML and EXECUTE contracts are finite, canonical, closed, and immutable", () => {
  const reviewed = canonicalExactEffectContract(contract("mcp-tool:leaf", [
    execute("ops.a(A)"),
    execute("ops.a(a)"),
    execute("ops.record_event(uuid,text)"),
    insert("public.event", ["actor_id", "kind", "subject_id"]),
    update("public.loop_item", ["status", "updated_at", "updated_by"]),
  ]));
  assert.deepEqual(resolveExactEffects("mcp-tool:leaf", immutableExactEffectContracts([reviewed])), [
    execute("ops.a(A)"),
    execute("ops.a(a)"),
    execute("ops.record_event(uuid,text)"),
    insert("public.event", ["actor_id", "kind", "subject_id"]),
    update("public.loop_item", ["status", "updated_at", "updated_by"]),
  ]);
  assert.equal(Object.isFrozen(reviewed), true);
  assert.equal(Object.isFrozen(reviewed.direct_effects), true);
  assert.equal(Object.isFrozen(reviewed.direct_effects[3].columns), true);
  assert.throws(() => canonicalExactEffectContract({ ...reviewed, inferred_from_write: true }),
    /open_or_incomplete/);
  assert.throws(() => canonicalExactEffectContract(contract("mcp-tool:leaf", [
    insert("public.event", ["kind", "actor_id"]),
  ])), /not_canonical/);
  assert.throws(() => canonicalExactEffectContract(contract("mcp-tool:leaf", [
    { kind: "execute", function_signature: "ops.anything(text); drop table actor" },
  ])), /target_malformed/);
});

test("recursive delegation returns the exact transitive union once", () => {
  const contracts = immutableExactEffectContracts([
    contract("mcp-tool:outer", [update("public.loop_item", ["status"])],
      ["mcp-tool:left", "mcp-tool:right"]),
    contract("mcp-tool:left", [insert("public.event", ["actor_id", "kind"])]),
    contract("mcp-tool:right", [execute("ops.audit_event(uuid)"),
      insert("public.event", ["actor_id", "kind"])]),
  ]);
  assert.deepEqual(resolveExactEffects("mcp-tool:outer", contracts), [
    { kind: "execute", function_signature: "ops.audit_event(uuid)" },
    { kind: "insert", relation: "public.event", columns: ["actor_id", "kind"] },
    { kind: "update", relation: "public.loop_item", columns: ["status"] },
  ]);
});

test("missing, wildcard, cycle, dynamic SQL, opaque, and unintegrated paths default deny", () => {
  assert.deepEqual(Object.keys(SCAC_EXACT_EFFECT_CONTRACTS).sort(), [
    "db-function-acl:ops.accept_ready_plan_amendment(text,integer,text,uuid):execute",
    "db-function-acl:ops.acknowledge_ready_plan_amendment(bigint,uuid):execute",
    "db-function-acl:ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer):execute",
    "db-function-acl:ops.canonical_ownership_trusted_context():execute",
    "db-function-acl:ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb):execute",
    "db-function-acl:ops.discover_ready_plan_amendments(bigint,int):execute",
    "db-function-acl:ops.effective_ready_plan(text):execute",
    "db-function-acl:ops.mint_canonical_ownership_runtime_session(uuid,uuid,uuid,integer,text,text,timestamptz,uuid):execute",
    "db-function-acl:ops.propose_ready_plan_amendment(text,integer,text,text,text,jsonb,text,text,jsonb,jsonb,uuid,uuid):execute",
    "db-function-acl:ops.release_canonical_ownership_lease(uuid,uuid,bigint):execute",
    "db-function-acl:ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer):execute",
    "mcp-tool:accept-ready-plan-amendment",
    "mcp-tool:acknowledge-ready-plan-amendment",
    "mcp-tool:effective-ready-plan",
    "mcp-tool:propose-ready-plan-amendment",
    "mcp-tool:ready-plan-amendment-discovery",
    "worker-route:canonical-ownership",
    "worker-route:ready-plan-amendment",
  ]);
  assert.deepEqual(resolveExactEffects("worker-route:canonical-ownership"), [
    { kind: "execute", function_signature: "ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer)" },
    { kind: "execute", function_signature: "ops.canonical_ownership_trusted_context()" },
    { kind: "execute", function_signature: "ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb)" },
    { kind: "execute", function_signature: "ops.mint_canonical_ownership_runtime_session(uuid,uuid,uuid,integer,text,text,timestamptz,uuid)" },
    { kind: "execute", function_signature: "ops.release_canonical_ownership_lease(uuid,uuid,bigint)" },
    { kind: "execute", function_signature: "ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer)" },
  ]);
  assert.deepEqual(resolveExactEffects("worker-route:ready-plan-amendment"), [
    { kind: "execute", function_signature: "ops.accept_ready_plan_amendment(text,integer,text,uuid)" },
    { kind: "execute", function_signature: "ops.acknowledge_ready_plan_amendment(bigint,uuid)" },
    { kind: "execute", function_signature: "ops.discover_ready_plan_amendments(bigint,int)" },
    { kind: "execute", function_signature: "ops.effective_ready_plan(text)" },
    { kind: "execute", function_signature: "ops.propose_ready_plan_amendment(text,integer,text,text,text,jsonb,text,text,jsonb,jsonb,uuid,uuid)" },
  ]);
  assert.deepEqual(resolveExactEffects("mcp-tool:propose-ready-plan-amendment"), [
    { kind: "execute", function_signature: "ops.propose_ready_plan_amendment(text,integer,text,text,text,jsonb,text,text,jsonb,jsonb,uuid,uuid)" },
  ]);
  assert.throws(() => resolveExactEffects("mcp-tool:unknown"),
    error => error instanceof ExactEffectRefusal && error.error === "effect_contract_missing");
  assert.throws(() => immutableExactEffectContracts([
    contract("mcp-tool:any", [], ["mcp-tool:*registered"]),
  ]), /wildcard_delegate_refused/);
  const cyclic = immutableExactEffectContracts([
    contract("mcp-tool:a", [], ["mcp-tool:b"]),
    contract("mcp-tool:b", [], ["mcp-tool:a"]),
  ]);
  assert.throws(() => resolveExactEffects("mcp-tool:a", cyclic),
    error => error instanceof ExactEffectRefusal && error.error === "effect_delegate_cycle");
  for (const [sql_state, expected] of [
    ["dynamic_refused", "effect_sql_not_static"], ["opaque_refused", "effect_sql_not_static"],
  ]) {
    const refused = immutableExactEffectContracts([contract("mcp-tool:x", [], [],
      { sql_state, integration_state: "unintegrated_default_deny" })]);
    assert.throws(() => resolveExactEffects("mcp-tool:x", refused),
      error => error instanceof ExactEffectRefusal && error.error === expected);
  }
  const unintegrated = immutableExactEffectContracts([contract("mcp-tool:x", [], [],
    { integration_state: "unintegrated_default_deny" })]);
  assert.throws(() => resolveExactEffects("mcp-tool:x", unintegrated),
    error => error instanceof ExactEffectRefusal && error.error === "effect_path_unintegrated");
});

test("trusted principal binds server actor fields to the actual DB session readback", async () => {
  assert.match(SCAC_TRUSTED_PRINCIPAL_READBACK_SQL.text, /session_user::text/);
  assert.match(SCAC_TRUSTED_PRINCIPAL_READBACK_SQL.text, /current_user::text/);
  assert.match(SCAC_TRUSTED_PRINCIPAL_READBACK_SQL.text,
    /pg_has_role\(session_user,\s*'carr_writer',\s*'member'\)/);
  assert.match(SCAC_TRUSTED_PRINCIPAL_READBACK_SQL.text, /pg_backend_pid\(\)/);
  const actor = {
    id: "10000000-0000-4000-8000-000000000001", slug: "joe", display: "Joe Bookout",
    actor_kind: "human", human: true, via: "oauth-google", client_id: "trusted-client", sponsoring_human_slug: null,
    human_slug: null, sponsor_required: false,
  };
  const readback = { session_principal: "app_writer", current_principal: "app_writer",
    privilege_bundle: "carr_writer", member_carr_writer: true, member_carr_jobs: false,
    member_carr_authority: false, backend_pid: 4123 };
  const bound = await deriveTrustedPrincipalBinding(actor, readback, "carr_writer");
  assert.deepEqual({ ...bound, principal_digest: undefined }, {
    schema_version: "scac-trusted-principal.v1", organization_tenant_id: "carr-internal",
    actor_id: actor.id, actor_slug: "joe", actor_kind: "human", human: true, via: "oauth-google",
    client_id: "trusted-client", sponsoring_human_slug: "joe",
    native_agent_verified: false, authority_sponsor_slug: null,
    authorization_class: "verified_partner", session_principal: "app_writer",
    privilege_bundle: "carr_writer", backend_pid: 4123, principal_digest: undefined,
    source: "server_authenticated_actor_plus_database_readback",
    production_enforcement_active: false,
  });
  assert.match(bound.principal_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(Object.isFrozen(bound), true);
  const entered = await executeWithTrustedPrincipal(actor, readback, "carr_writer",
    trustedActor => trustedActor.trusted_principal);
  assert.equal(entered.session_principal, "app_writer",
    "the deployed writer login reaches the handler with its verified bundle binding");
});

test("caller labels and mismatched or elevated database principals cannot create a binding", async () => {
  const actor = { id: "10000000-0000-4000-8000-000000000001", slug: "joe", actor_kind: "human", human: true };
  const writer = { session_principal: "carr_writer", current_principal: "carr_writer",
    privilege_bundle: "carr_writer", member_carr_writer: true, member_carr_jobs: false,
    member_carr_authority: false, backend_pid: 9 };
  await assert.rejects(deriveTrustedPrincipalBinding(actor, { ...writer, claimed_actor: "dell" },
    "carr_writer"), /open_or_incomplete/);
  await assert.rejects(deriveTrustedPrincipalBinding(actor,
    { ...writer, session_principal: "carr_authority_joe", current_principal: "carr_authority_joe",
      privilege_bundle: "carr_authority" }, "carr_writer"),
    error => error instanceof ExactEffectRefusal && error.error === "trusted_database_principal_mismatch");
  await assert.rejects(deriveTrustedPrincipalBinding(actor,
    { ...writer, current_principal: "neondb_owner" }, "carr_writer"),
    error => error instanceof ExactEffectRefusal && error.error === "trusted_database_principal_mismatch");
  await assert.rejects(deriveTrustedPrincipalBinding(actor,
    { ...writer, session_principal: "app_writer", current_principal: "app_writer",
      member_carr_writer: false }, "carr_writer"),
    error => error instanceof ExactEffectRefusal && error.error === "trusted_database_principal_mismatch");
  await assert.rejects(deriveTrustedPrincipalBinding(actor,
    { ...writer, session_principal: "app_writer", current_principal: "app_writer",
      member_carr_authority: true }, "carr_writer"),
    error => error instanceof ExactEffectRefusal && error.error === "trusted_database_principal_mismatch");
  const joeAuthority = { session_principal: "carr_authority_joe",
    current_principal: "carr_authority_joe", privilege_bundle: "carr_authority",
    member_carr_writer: false, member_carr_jobs: false, member_carr_authority: true,
    backend_pid: 10 };
  const joe = await deriveTrustedPrincipalBinding(actor, joeAuthority, "carr_authority");
  assert.equal(joe.session_principal, "carr_authority_joe");
  await assert.rejects(deriveTrustedPrincipalBinding(actor, {
    ...joeAuthority,
    session_principal: "carr_authority_dell",
    current_principal: "carr_authority_dell",
  }, "carr_authority"),
  error => error instanceof ExactEffectRefusal && error.error === "trusted_database_principal_mismatch");
});

test("dispatch translates principal mismatch before handler entry", async () => {
  let handlerCalls = 0;
  const actor = { id: "10000000-0000-4000-8000-000000000001", slug: "joe",
    actor_kind: "human", human: true };
  const wrong = { session_principal: "carr_jobs", current_principal: "carr_jobs",
    privilege_bundle: "carr_jobs", member_carr_writer: false, member_carr_jobs: true,
    member_carr_authority: false, backend_pid: 9 };
  await assert.rejects(executeWithTrustedPrincipal(actor, wrong, "carr_writer", async () => {
    handlerCalls += 1;
  }), error => error instanceof ToolError &&
    error.payload.error === "trusted_database_principal_mismatch" &&
    error.payload.security_boundary === "scac_trusted_principal");
  assert.equal(handlerCalls, 0);
});

test("0469 persists exact effects and trusted principals without activating enforcement", () => {
  for (const fragment of [
    /create table ops\.scac_exact_effect_contract/i,
    /create table ops\.scac_trusted_principal_binding/i,
    /create or replace function ops\.scac_register_exact_effect_contract/i,
    /create or replace function ops\.scac_exact_effect_union/i,
    /with recursive walk/i,
    /create or replace function ops\.scac_bind_trusted_principal/i,
    /jsonb_typeof\(p_server_principal->'actor_id'\) is distinct from 'string'/i,
    /jsonb_typeof\(p_server_principal->'backend_pid'\) is distinct from 'number'/i,
    /jsonb_typeof\(p_server_principal->'authority_sponsor_slug'\) not in \('string','null'\)/i,
    /p_server_principal->>'session_principal' is distinct from session_user/i,
    /p_server_principal->>'privilege_bundle' is distinct from expected_bundle/i,
    /p_server_principal->'backend_pid' is distinct from to_jsonb\(pg_backend_pid\(\)\)/i,
    /actor_row\.kind is distinct from p_server_principal->>'actor_kind'/i,
    /p_server_principal->'human' is distinct from to_jsonb\(actor_row\.kind='human'\)/i,
    /p_server_principal->>'authorization_class' is distinct from case/i,
    /actor_row\.slug='joe-local'.*sponsoring_human_slug'='joe'/is,
    /session_user is distinct from\s*'carr_authority_'\|\|\(p_server_principal->>'authority_sponsor_slug'\)/i,
    /actor_row\.slug is distinct from.*authority_sponsor_slug/is,
    /where id=\(p_server_principal->>'actor_id'\)::uuid/i,
    /and slug=p_server_principal->>'actor_slug' and active/i,
    /recorded_by text not null check \(recorded_by='joe'\)/i,
    /production_enforcement_active boolean not null default false/i,
    /check \(not production_enforcement_active\)/i,
  ]) assert.match(migration, fragment);
  assert.doesNotMatch(migration, /insert into ops\.scac_exact_effect_contract[\s\S]+values\s*\('scac-mutation-registry/i);
  assert.doesNotMatch(migration, /mode\s*=\s*'enforced_source_test'/i);
  assert.doesNotMatch(migration, /^\s*(?:begin|commit)\s*;/im);
});

test("0469 refuses inference, wildcard delegation, dynamic SQL, and caller identity", () => {
  assert.match(migration, /write labels, prose, grants, opaque functions, and dynamic SQL are never effect authority/i);
  assert.match(migration, /p_contract->>'sql_state'<>'static_reviewed'/i);
  assert.match(migration, /p_contract->>'integration_state'<>'reviewed_source_test'/i);
  assert.match(migration, /d~E'\[\\n\\r\\t\*\]'/i);
  assert.match(migration, /de\.contract->>'operation'=raw\.d/i);
  assert.match(migration, /coalesce\(bool_and\(cardinality\(keys\)=1\),true\)/i);
  assert.match(migration, /resolved_registry_delegates<>p_contract->'delegates_to'/i);
  assert.doesNotMatch(migration, /contract->>'write'/i);
  assert.doesNotMatch(migration, /description/i);
});
