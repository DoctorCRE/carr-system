import { authorizationClassForActor, organizationTenantForActor,
  personalScopeForActor } from "./identity.js";
import { partnerAuthoritySlugForActor } from "./partner-authority.js";

const INGRESS = /^[a-z][a-z0-9_-]+:[^\n\r\t]{1,980}$/;
const RELATION = /^[a-z_][a-z0-9_$]*\.[a-z_][a-z0-9_$]*$/;
const COLUMN = /^[a-z_][a-z0-9_$]*$/;
const FUNCTION_SIGNATURE = /^[a-z_][a-z0-9_$]*\.[a-z_][a-z0-9_$]*\([^;\n\r]*\)$/;
const SESSION_PRINCIPALS = new Map([
  ["carr_writer", "carr_writer"],
  ["app_writer", "carr_writer"],
  ["carr_jobs", "carr_jobs"],
  ["carr_authority_joe", "carr_authority"],
  ["carr_authority_dell", "carr_authority"],
]);

export const SCAC_TRUSTED_PRINCIPAL_READBACK_SQL = Object.freeze({
  text: `select session_user::text as session_principal,
                current_user::text as current_principal,
                case
                  when session_user in ('carr_writer','app_writer')
                    and pg_has_role(session_user,'carr_writer','member')
                    and not pg_has_role(session_user,'carr_jobs','member')
                    and not pg_has_role(session_user,'carr_authority','member')
                    then 'carr_writer'
                  when session_user='carr_jobs'
                    and not pg_has_role(session_user,'carr_writer','member')
                    and pg_has_role(session_user,'carr_jobs','member')
                    and not pg_has_role(session_user,'carr_authority','member')
                    then 'carr_jobs'
                  when session_user in ('carr_authority_joe','carr_authority_dell')
                    and not pg_has_role(session_user,'carr_writer','member')
                    and not pg_has_role(session_user,'carr_jobs','member')
                    and pg_has_role(session_user,'carr_authority','member')
                    then 'carr_authority'
                  else null end as privilege_bundle,
                pg_has_role(session_user,'carr_writer','member') as member_carr_writer,
                pg_has_role(session_user,'carr_jobs','member') as member_carr_jobs,
                pg_has_role(session_user,'carr_authority','member') as member_carr_authority,
                pg_backend_pid()::integer as backend_pid`,
  values: Object.freeze([]),
});

export class ExactEffectRefusal extends Error {
  constructor(error, ingressKey, detail = null) {
    super(error);
    this.name = "ExactEffectRefusal";
    this.error = error;
    this.ingress_key = ingressKey;
    this.detail = detail;
  }
}

const UTF8_ENCODER = new TextEncoder();
function compareUtf8(left, right) {
  const a = UTF8_ENCODER.encode(left);
  const b = UTF8_ENCODER.encode(right);
  const length = Math.min(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    if (a[index] !== b[index]) return a[index] < b[index] ? -1 : 1;
  }
  return a.length < b.length ? -1 : a.length > b.length ? 1 : 0;
}

function exactObject(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new TypeError(`${label}_malformed`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]))
    throw new TypeError(`${label}_open_or_incomplete`);
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(canonicalize(value)));
  const result = await crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${[...new Uint8Array(result)].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
}

function sortedUniqueStrings(values, pattern, label, { allowEmpty = false } = {}) {
  if (!Array.isArray(values) || (!allowEmpty && values.length === 0) ||
      values.some(value => typeof value !== "string" || !pattern.test(value)))
    throw new TypeError(`${label}_malformed`);
  const sorted = [...values].sort(compareUtf8);
  if (new Set(sorted).size !== sorted.length || sorted.some((value, index) => value !== values[index]))
    throw new TypeError(`${label}_not_canonical`);
  return sorted;
}

export function canonicalExactEffect(effect) {
  if (!effect || typeof effect !== "object" || Array.isArray(effect))
    throw new TypeError("scac_exact_effect_malformed");
  if (effect.kind === "execute") {
    exactObject(effect, ["kind", "function_signature"], "scac_execute_effect");
    if (!FUNCTION_SIGNATURE.test(effect.function_signature || ""))
      throw new TypeError("scac_execute_effect_target_malformed");
    return Object.freeze({ kind: "execute", function_signature: effect.function_signature });
  }
  exactObject(effect, ["kind", "relation", "columns"], "scac_dml_effect");
  if (!["insert", "update", "delete"].includes(effect.kind) || !RELATION.test(effect.relation || ""))
    throw new TypeError("scac_dml_effect_target_malformed");
  const columns = sortedUniqueStrings(effect.columns, COLUMN, "scac_dml_effect_columns");
  return Object.freeze({ kind: effect.kind, relation: effect.relation,
    columns: Object.freeze(columns) });
}

function effectKey(effect) {
  return effect.kind === "execute"
    ? `execute:${effect.function_signature}`
    : `${effect.kind}:${effect.relation}:${effect.columns.join(",")}`;
}

export function canonicalExactEffectContract(contract) {
  exactObject(contract, ["schema_version", "ingress_key", "direct_effects", "delegates_to",
    "sql_state", "integration_state"], "scac_exact_effect_contract");
  if (contract.schema_version !== "scac-exact-effect-contract.v1" ||
      !INGRESS.test(contract.ingress_key || "") ||
      !["static_reviewed", "dynamic_refused", "opaque_refused"].includes(contract.sql_state) ||
      !["reviewed_source_test", "unintegrated_default_deny"].includes(contract.integration_state))
    throw new TypeError("scac_exact_effect_contract_value_malformed");
  const effects = contract.direct_effects.map(canonicalExactEffect);
  const keys = effects.map(effectKey);
  if (new Set(keys).size !== keys.length ||
      keys.some((key, index) => index > 0 && compareUtf8(key, keys[index - 1]) < 0))
    throw new TypeError("scac_exact_effects_not_canonical");
  const delegates = sortedUniqueStrings(contract.delegates_to, INGRESS,
    "scac_exact_effect_delegates", { allowEmpty: true });
  if (delegates.some(value => value.includes("*")))
    throw new TypeError("scac_exact_effect_wildcard_delegate_refused");
  if (contract.sql_state === "static_reviewed" && contract.integration_state === "reviewed_source_test" &&
      effects.length === 0 && delegates.length === 0)
    throw new TypeError("scac_exact_effect_contract_empty");
  return Object.freeze({
    schema_version: contract.schema_version,
    ingress_key: contract.ingress_key,
    direct_effects: Object.freeze(effects),
    delegates_to: Object.freeze(delegates),
    sql_state: contract.sql_state,
    integration_state: contract.integration_state,
  });
}

export function immutableExactEffectContracts(contracts) {
  if (!Array.isArray(contracts)) throw new TypeError("scac_exact_effect_contracts_malformed");
  const entries = contracts.map(canonicalExactEffectContract);
  const out = {};
  for (const contract of entries) {
    if (out[contract.ingress_key]) throw new TypeError("scac_exact_effect_contract_duplicate");
    out[contract.ingress_key] = contract;
  }
  return Object.freeze(out);
}

// WR-000125 closes the exact-effect lane for the new accepted-plan amendment
// ingress and the trusted ownership context. These are deliberately EXECUTE
// contracts, not inferred DML: the SECURITY DEFINER functions own their
// transaction-local checks and the Worker route is the only caller allowed to
// reach the existing 0450 lease functions. The registry, grants, or handler
// prose never become effect authority by themselves.
const WR125_EXECUTE = [
  ["db-function-acl:ops.accept_ready_plan_amendment(text,integer,text,uuid):execute",
    "ops.accept_ready_plan_amendment(text,integer,text,uuid)"],
  ["db-function-acl:ops.acknowledge_ready_plan_amendment(bigint,uuid):execute",
    "ops.acknowledge_ready_plan_amendment(bigint,uuid)"],
  ["db-function-acl:ops.canonical_ownership_trusted_context():execute",
    "ops.canonical_ownership_trusted_context()"],
  ["db-function-acl:ops.discover_ready_plan_amendments(bigint,int):execute",
    "ops.discover_ready_plan_amendments(bigint,int)"],
  ["db-function-acl:ops.effective_ready_plan(text):execute",
    "ops.effective_ready_plan(text)"],
  ["db-function-acl:ops.propose_ready_plan_amendment(text,integer,text,text,text,jsonb,text,text,jsonb,jsonb,uuid,uuid):execute",
    "ops.propose_ready_plan_amendment(text,integer,text,text,text,jsonb,text,text,jsonb,jsonb,uuid,uuid)"],
  ["db-function-acl:ops.mint_canonical_ownership_runtime_session(uuid,uuid,uuid,integer,text,text,timestamptz,uuid):execute",
    "ops.mint_canonical_ownership_runtime_session(uuid,uuid,uuid,integer,text,text,timestamptz,uuid)"],
  ["db-function-acl:ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer):execute",
    "ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer)"],
  ["db-function-acl:ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb):execute",
    "ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb)"],
  ["db-function-acl:ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer):execute",
    "ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer)"],
  ["db-function-acl:ops.release_canonical_ownership_lease(uuid,uuid,bigint):execute",
    "ops.release_canonical_ownership_lease(uuid,uuid,bigint)"],
];

const wr125Contract = (ingress_key, direct_effects = [], delegates_to = []) => ({
  schema_version: "scac-exact-effect-contract.v1",
  ingress_key,
  direct_effects,
  delegates_to,
  sql_state: "static_reviewed",
  integration_state: "reviewed_source_test",
});

export const SCAC_EXACT_EFFECT_CONTRACTS = immutableExactEffectContracts([
  ...WR125_EXECUTE.map(([ingress_key, function_signature]) =>
    wr125Contract(ingress_key, [{ kind: "execute", function_signature }])),
  wr125Contract("mcp-tool:accept-ready-plan-amendment", [], [WR125_EXECUTE[0][0]]),
  wr125Contract("mcp-tool:acknowledge-ready-plan-amendment", [], [WR125_EXECUTE[1][0]]),
  wr125Contract("mcp-tool:effective-ready-plan", [], [WR125_EXECUTE[4][0]]),
  wr125Contract("mcp-tool:propose-ready-plan-amendment", [], [WR125_EXECUTE[5][0]]),
  wr125Contract("mcp-tool:ready-plan-amendment-discovery", [], [WR125_EXECUTE[3][0]]),
  wr125Contract("worker-route:canonical-ownership", [], [
    WR125_EXECUTE[7][0], WR125_EXECUTE[2][0], WR125_EXECUTE[8][0],
    WR125_EXECUTE[6][0], WR125_EXECUTE[10][0], WR125_EXECUTE[9][0],
  ]),
  wr125Contract("worker-route:ready-plan-amendment", [], [
    WR125_EXECUTE[0][0], WR125_EXECUTE[1][0], WR125_EXECUTE[3][0],
    WR125_EXECUTE[4][0], WR125_EXECUTE[5][0],
  ]),
]);

export function resolveExactEffects(ingressKey, contracts = SCAC_EXACT_EFFECT_CONTRACTS) {
  const visiting = new Set();
  const visited = new Set();
  const effects = new Map();
  function visit(key) {
    if (visiting.has(key)) throw new ExactEffectRefusal("effect_delegate_cycle", ingressKey, key);
    if (visited.has(key)) return;
    const contract = contracts[key];
    if (!contract) throw new ExactEffectRefusal("effect_contract_missing", ingressKey, key);
    if (contract.sql_state !== "static_reviewed")
      throw new ExactEffectRefusal("effect_sql_not_static", ingressKey, key);
    if (contract.integration_state !== "reviewed_source_test")
      throw new ExactEffectRefusal("effect_path_unintegrated", ingressKey, key);
    visiting.add(key);
    for (const effect of contract.direct_effects) effects.set(effectKey(effect), effect);
    for (const delegate of contract.delegates_to) visit(delegate);
    visiting.delete(key);
    visited.add(key);
  }
  if (!INGRESS.test(ingressKey || ""))
    throw new ExactEffectRefusal("effect_ingress_malformed", ingressKey);
  visit(ingressKey);
  if (effects.size === 0) throw new ExactEffectRefusal("effect_contract_empty", ingressKey);
  return Object.freeze([...effects.entries()].sort(([a], [b]) => compareUtf8(a, b))
    .map(([, effect]) => effect));
}

export async function deriveTrustedPrincipalBinding(actor, readback, requiredBundle) {
  exactObject(readback, ["session_principal", "current_principal", "privilege_bundle",
    "member_carr_writer", "member_carr_jobs", "member_carr_authority", "backend_pid"],
    "scac_trusted_principal_readback");
  if (!actor || typeof actor !== "object" || typeof actor.id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(actor.id) ||
      typeof actor.slug !== "string" || !/^[a-z][a-z0-9_-]{0,63}$/.test(actor.slug))
    throw new ExactEffectRefusal("trusted_actor_unavailable", null);
  const actorKind = actor.human === true ? "human" : "automation";
  const scope = personalScopeForActor(actor);
  if (scope.status === "error") throw new ExactEffectRefusal("trusted_actor_scope_unavailable", null);
  const authoritySponsor = requiredBundle === "carr_authority"
    ? partnerAuthoritySlugForActor(actor) : null;
  const observedBundle = SESSION_PRINCIPALS.get(readback.session_principal);
  const memberships = new Map([
    ["carr_writer", readback.member_carr_writer],
    ["carr_jobs", readback.member_carr_jobs],
    ["carr_authority", readback.member_carr_authority],
  ]);
  const membershipShapeValid = [...memberships.values()]
    .every(value => typeof value === "boolean");
  const activeMemberships = [...memberships.entries()]
    .filter(([, member]) => member === true).map(([bundle]) => bundle);
  const expectedAuthoritySession = scope.status === "personal"
    ? `carr_authority_${scope.sponsor}` : null;
  if (!observedBundle || observedBundle !== readback.privilege_bundle ||
      !["carr_writer", "carr_jobs", "carr_authority"].includes(requiredBundle) ||
      observedBundle !== requiredBundle || !membershipShapeValid ||
      activeMemberships.length !== 1 || activeMemberships[0] !== observedBundle ||
      readback.current_principal !== readback.session_principal ||
      (requiredBundle === "carr_authority" && readback.session_principal !== expectedAuthoritySession) ||
      (requiredBundle === "carr_authority" && authoritySponsor !== scope.sponsor) ||
      !Number.isSafeInteger(readback.backend_pid) || readback.backend_pid <= 0)
    throw new ExactEffectRefusal("trusted_database_principal_mismatch", null);
  const manifest = {
    schema_version: "scac-trusted-principal.v1",
    organization_tenant_id: organizationTenantForActor(actor),
    actor_id: actor.id,
    actor_slug: actor.slug,
    actor_kind: actorKind,
    human: actor.human === true,
    via: actor.via || null,
    client_id: actor.client_id || null,
    sponsoring_human_slug: scope.status === "personal" ? scope.sponsor : null,
    native_agent_verified: actor.native_agent_verified === true,
    authority_sponsor_slug: authoritySponsor,
    authorization_class: authorizationClassForActor(actor),
    session_principal: readback.session_principal,
    privilege_bundle: observedBundle,
    backend_pid: readback.backend_pid,
  };
  return Object.freeze({ ...manifest, principal_digest: await sha256(manifest),
    source: "server_authenticated_actor_plus_database_readback",
    production_enforcement_active: false });
}
