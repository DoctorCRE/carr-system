// DoctorCRE v5 V5-F05 — live, read-only rule-context adapter.
//
// The rule-applicability kernel below this seam is deliberately pure. This
// module supplies the piece its integration-gap report names: one authenticated
// actor-scoped read of the durable rule universe, followed by the existing
// compiler and coverage derivation. Callers may supply task facts only. Actor,
// sponsor scope, tenant, observation time, universe completeness, semantic
// candidates, and the consequential-action gate are all server-derived.

import {
  V5_F05_AUDIENCES,
  V5_F05_ENVIRONMENTS,
  V5_F05_FACT_DIMENSIONS,
  V5_F05_GUARDS,
  V5_F05_LIFECYCLE_TRANSITIONS,
  V5_F05_POLICY_VERSION,
  V5_F05_RISK_TIERS,
  V5_F05_ACTOR_CLASSES,
  V5_F05_UNIVERSE_SCHEMA_VERSION,
  V5_F05_UNKNOWN_FACT,
  compileRuleUniverse,
  coverageDecision,
  deriveRuleApplicability,
  ruleUniversePreimage,
  verifyCoverageReceipt,
} from "./rule-applicability.v5.js";
import { digest } from "./artifact-trust.js";
import {
  ORGANIZATION_TENANT_ID,
  authorizationClassForActor,
  personalScopeForActor,
} from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";

export const V5_F05_RUNTIME_SCHEMA_VERSION = "doctorcre-v5-f05-rule-context-runtime.v1";

const COMPLETE = "complete_authoritative_universe";
const PARTIAL = "partial_unknown_coverage";
const REQUEST_KEYS = Object.freeze(["facts"]);
const FACT_KEYS = Object.freeze([
  "action", "audience", "environment", "lifecycle_transition", "resource_class", "risk_tier",
]);

export class V5F05RuntimeError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5F05RuntimeError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5F05RuntimeError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function ownDataKeys(value, path) {
  if (!isPlainObject(value)) fail("invalid_runtime_request", `${path} must be a plain object`, { path });
  if (Object.getOwnPropertySymbols(value).length > 0) {
    fail("invalid_runtime_request", `${path} may not carry symbol keys`, { path });
  }
  const keys = Object.getOwnPropertyNames(value);
  for (const key of keys) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor?.get !== undefined || descriptor?.set !== undefined) {
      fail("invalid_runtime_request", `${path}.${key} may not be an accessor`, { path: `${path}.${key}` });
    }
  }
  return keys;
}

function assertNoRuntimeAuthorityInjection(request) {
  const keys = ownDataKeys(request, "request");
  for (const key of keys) {
    if (!REQUEST_KEYS.includes(key)) {
      fail("runtime_authority_injection",
        `request.${key} is server-derived and cannot be supplied by the caller`,
        { path: `request.${key}` });
    }
  }
  if (!keys.includes("facts")) fail("invalid_runtime_request", "request.facts is required");
  const factKeys = ownDataKeys(request.facts, "request.facts");
  if (factKeys.includes("actor_class")) {
    fail("runtime_authority_injection",
      "request.facts.actor_class is derived from the authenticated actor",
      { path: "request.facts.actor_class" });
  }
}

function assertSafeCount(value, path) {
  if (!Number.isSafeInteger(value) || value < 0) {
    fail("runtime_census_mismatch", `${path} must be a non-negative safe integer`, { path });
  }
  return value;
}

function readMissingRuleIds(value) {
  if (!Array.isArray(value)) {
    fail("runtime_census_mismatch", "snapshot.missing_rule_ids must be an array");
  }
  const ids = value.map((id, index) => {
    if (typeof id !== "string" || id.length === 0 || id !== id.trim()) {
      fail("runtime_census_mismatch", "missing rule ids must be non-empty canonical strings",
        { path: `snapshot.missing_rule_ids[${index}]` });
    }
    return id;
  });
  const sorted = [...new Set(ids)].sort();
  if (sorted.length !== ids.length || sorted.some((id, index) => id !== ids[index])) {
    fail("runtime_census_mismatch", "snapshot.missing_rule_ids must be unique and C-sorted");
  }
  return sorted;
}

function readSnapshot(result) {
  if (!isPlainObject(result)) fail("rule_universe_unavailable", "rule universe reader returned no snapshot");
  const allowed = ["observed_at", "policy", "active_rule_count", "projected_rule_count", "missing_rule_ids"];
  const keys = ownDataKeys(result, "snapshot");
  const unknown = keys.filter(key => !allowed.includes(key));
  const missing = allowed.filter(key => !keys.includes(key));
  if (unknown.length > 0 || missing.length > 0) {
    fail("runtime_census_mismatch", "rule universe snapshot shape does not match the runtime contract",
      { unknown, missing });
  }
  const active = assertSafeCount(result.active_rule_count, "snapshot.active_rule_count");
  const projected = assertSafeCount(result.projected_rule_count, "snapshot.projected_rule_count");
  const missingRuleIds = readMissingRuleIds(result.missing_rule_ids);
  if (!isPlainObject(result.policy) || !Array.isArray(result.policy.rules) ||
      projected !== result.policy.rules.length || projected > active ||
      missingRuleIds.length !== active - projected) {
    fail("runtime_census_mismatch",
      "the rule census, projected rules, and explicit missing-rule list disagree",
      { active_rule_count: active, projected_rule_count: projected,
        policy_rule_count: Array.isArray(result.policy?.rules) ? result.policy.rules.length : null,
        missing_rule_count: missingRuleIds.length });
  }
  return { observed_at: result.observed_at, policy: result.policy,
    active_rule_count: active, projected_rule_count: projected, missing_rule_ids: missingRuleIds };
}

function actorScope(actor) {
  if (!actor || typeof actor.id !== "string" || actor.id.length === 0) {
    fail("invalid_runtime_principal", "the authenticated actor has no durable id");
  }
  const scope = personalScopeForActor(actor);
  if (scope.status === "error") fail(scope.error, "the authenticated actor scope cannot be resolved");
  const actorClass = authorizationClassForActor(actor);
  if (!["verified_partner", "sponsored_agent"].includes(actorClass)) {
    fail("unsupported_runtime_principal",
      "the authenticated principal has no settled V5-F05 actor-class mapping",
      { authorization_class: actorClass });
  }
  return { actorClass, sponsor: scope.status === "personal" ? scope.sponsor : null };
}

// ---------------------------------------------------------------------------
// Zero projected rules.
//
// Until Joe binds the first typed contract, the census projects an EMPTY
// policy while the store still holds active rules. The kernel refuses to
// compile an empty universe (policy.rules min 1) and that refusal is right for
// a universe that claims to be a rule set; it is not relaxed here. Instead the
// runtime answers the question the kernel cannot be asked: no rule is typed,
// so nothing can be classified, every active rule is named as missing, and the
// consequential write is blocked. The receipt is digest-bound with the
// kernel's own convention (sha256 over the receipt minus receipt_digest and
// effects), so verifyCoverageReceipt checks it unchanged.
//
// ZERO RULES IS NEVER COMPLETE. Not even when the census also counts zero
// active rules: an empty projection is the absence of evidence, and the
// completeness and the gate below are constants, not derived from the census.
// ---------------------------------------------------------------------------

export const V5_F05_EMPTY_PROJECTION_COVERAGE_SCHEMA_VERSION =
  "doctorcre-v5-f05-empty-projection-coverage.v1";
const EMPTY_PROJECTION_COMPLETENESS = PARTIAL;
const EMPTY_PROJECTION_COVERAGE_COMPLETE = false;
const EMPTY_PROJECTION_WRITE_PERMITTED = false;
const EMPTY_PROJECTION_READ_PERMITTED = true;

const G = V5_F05_GUARDS;
const UNIVERSE_KEYS = Object.freeze([
  "schema_version", "universe_version", "tenant", "completeness",
  "declared_actions", "declared_resource_classes", "rules",
]);
const CLOSED_FACT_VALUES = Object.freeze({
  actor_class: V5_F05_ACTOR_CLASSES,
  audience: V5_F05_AUDIENCES,
  environment: V5_F05_ENVIRONMENTS,
  lifecycle_transition: V5_F05_LIFECYCLE_TRANSITIONS,
  risk_tier: V5_F05_RISK_TIERS,
});

function readDeclaredList(raw, path) {
  G.assertArray(raw, path, { min: 1, max: 256 });
  const seen = new Set();
  return raw.map((value, index) => {
    const item = G.assertExternalIdent(value, `${path}[${index}]`, { maxLength: 128 });
    if (seen.has(item)) G.fail("duplicate_declared_value", `${path} repeats "${item}"`, { path, value: item });
    seen.add(item);
    return item;
  }).sort();
}

// The same header checks compileRuleUniverse runs, minus the rule list that is
// empty by definition here. The store's own completeness claim is read for
// vocabulary only and never trusted.
function readEmptyProjectedPolicy(policy) {
  G.assertObject(policy, "policy");
  G.assertClosedKeys(policy, UNIVERSE_KEYS, "policy");
  G.assertRequiredKeys(policy, UNIVERSE_KEYS, "policy");
  if (policy.schema_version !== V5_F05_UNIVERSE_SCHEMA_VERSION) {
    G.fail("unknown_schema_version", `policy.schema_version must be "${V5_F05_UNIVERSE_SCHEMA_VERSION}"`,
      { expected: V5_F05_UNIVERSE_SCHEMA_VERSION });
  }
  G.assertTenant(policy.tenant, "policy.tenant");
  G.assertSafeInteger(policy.universe_version, "policy.universe_version", { min: 1 });
  G.assertEnum(policy.completeness, [COMPLETE, PARTIAL], "policy.completeness", "unknown_completeness");
  G.assertArray(policy.rules, "policy.rules", { min: 0, max: 0 });
  return {
    universe_version: policy.universe_version,
    declared_actions: readDeclaredList(policy.declared_actions, "policy.declared_actions"),
    declared_resource_classes: readDeclaredList(policy.declared_resource_classes,
      "policy.declared_resource_classes"),
  };
}

// Mirrors the kernel's fact reading: closed dimensions, registered vocabulary,
// and an action or resource class outside the declared vocabulary is UNKNOWN,
// never a mismatch.
function readEmptyProjectionFacts(raw, declared) {
  G.assertObject(raw, "request.facts");
  G.assertClosedKeys(raw, V5_F05_FACT_DIMENSIONS, "request.facts");
  const known = {};
  const unknown = [];
  for (const dimension of V5_F05_FACT_DIMENSIONS) {
    const path = `request.facts.${dimension}`;
    const value = raw[dimension];
    if (value === undefined || value === null) {
      unknown.push({ dimension, reason_id: "fact_absent" });
      continue;
    }
    G.assertSafeText(value, path, { maxLength: 128 });
    if (value === V5_F05_UNKNOWN_FACT) {
      unknown.push({ dimension, reason_id: "fact_declared_unknown" });
      continue;
    }
    if (CLOSED_FACT_VALUES[dimension] !== undefined) {
      G.assertEnum(value, CLOSED_FACT_VALUES[dimension], path, `unknown_${dimension}`);
      known[dimension] = value;
      continue;
    }
    G.assertExternalIdent(value, path, { maxLength: 128 });
    if (!declared[dimension].includes(value)) {
      unknown.push({ dimension, reason_id: "fact_outside_declared_vocabulary", value });
      continue;
    }
    known[dimension] = value;
  }
  return { known, unknown: unknown.sort((a, b) => (a.dimension < b.dimension ? -1 : 1)) };
}

function emptyProjectionCoverage(snapshot, facts) {
  const header = readEmptyProjectedPolicy(snapshot.policy);
  G.assertInstant(snapshot.observed_at, "snapshot.observed_at");
  const { known, unknown } = readEmptyProjectionFacts(facts, {
    action: header.declared_actions, resource_class: header.declared_resource_classes,
  });
  const universe_digest = digest(ruleUniversePreimage({
    universe_version: header.universe_version,
    tenant: ORGANIZATION_TENANT_ID,
    completeness: EMPTY_PROJECTION_COMPLETENESS,
    declared_actions: header.declared_actions,
    declared_resource_classes: header.declared_resource_classes,
    removal_order: [],
    rules: [],
  }));
  // The kernel's rule, not a copied literal, so this receipt and the kernel's
  // partial receipt agree: a blocked write reads read_only, never allow.
  const decision = coverageDecision({
    read_only_exploration_permitted: EMPTY_PROJECTION_READ_PERMITTED,
    consequential_action_permitted: EMPTY_PROJECTION_WRITE_PERMITTED });
  const blocking_reasons = ["universe_coverage_unknown"];
  if (unknown.length > 0) blocking_reasons.push("typed_facts_unknown");
  blocking_reasons.push("no_rule_contract_projected");
  const receipt = {
    schema_version: V5_F05_EMPTY_PROJECTION_COVERAGE_SCHEMA_VERSION,
    policy_version: V5_F05_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    universe_version: header.universe_version,
    universe_digest,
    universe_completeness: EMPTY_PROJECTION_COMPLETENESS,
    now: snapshot.observed_at,
    decision,
    reason_id: "coverage_incomplete_read_only",
    facts: { ...known },
    unknown_facts: unknown,
    // No rule is typed, so none can be classified: every bucket is empty and
    // every active rule the census counted is named here instead.
    universe_rule_ids: [],
    active_rule_count: snapshot.active_rule_count,
    projected_rule_count: snapshot.projected_rule_count,
    missing_rule_ids: [...snapshot.missing_rule_ids],
    effective: [], possibly_binding: [], not_applicable: [], retired: [],
    superseded: [], overridden: [], suppressed_by_exception: [], pending_relations: [],
    skipped_relations: [], binding_conflicts: [], delivery: [], delivery_refusals: [],
    semantic_reinforcements: [], semantic_additions: [],
    semantic_may_remove_controls: false,
    model_resolves_conflicts: false,
    coverage_complete: EMPTY_PROJECTION_COVERAGE_COMPLETE,
    consequential_action_permitted: EMPTY_PROJECTION_WRITE_PERMITTED,
    read_only_exploration_permitted: EMPTY_PROJECTION_READ_PERMITTED,
    write_gate_field: "consequential_action_permitted",
    blocking_reasons,
  };
  return G.deepFreeze({ ...receipt, receipt_digest: digest(receipt), effects: V5_NO_EFFECTS });
}

export async function readActionContext(client, actor, request) {
  assertNoRuntimeAuthorityInjection(request);
  const scope = actorScope(actor);
  const response = await client.query(
    "select ops.f05_rule_universe($1::text,$2::text) as result",
    [request.facts.action, request.facts.resource_class],
  );
  const snapshot = readSnapshot(response.rows[0]?.result);
  if (snapshot.projected_rule_count === 0) {
    const coverage = emptyProjectionCoverage(snapshot,
      { ...request.facts, actor_class: scope.actorClass });
    verifyCoverageReceipt(coverage);
    return runtimeResult(snapshot, scope, coverage.universe_digest, coverage);
  }
  const censusComplete = snapshot.active_rule_count === snapshot.projected_rule_count &&
    snapshot.missing_rule_ids.length === 0;
  const completeness = censusComplete
    ? COMPLETE
    : PARTIAL;
  const universe = compileRuleUniverse({ ...snapshot.policy, completeness });
  const coverage = deriveRuleApplicability({
    tenant: ORGANIZATION_TENANT_ID,
    universe,
    facts: { ...request.facts, actor_class: scope.actorClass },
    now: snapshot.observed_at,
    semantic_candidates: [],
  });
  verifyCoverageReceipt(coverage);
  return runtimeResult(snapshot, scope, universe.universe_digest, coverage);
}

function runtimeResult(snapshot, scope, universeDigest, coverage) {
  return Object.freeze({
    schema_version: V5_F05_RUNTIME_SCHEMA_VERSION,
    observed_at: snapshot.observed_at,
    universe_digest: universeDigest,
    coverage_receipt: coverage,
    consequential_action_permitted: coverage.consequential_action_permitted,
    source: Object.freeze({
      kind: "ops.f05_rule_universe",
      actor_scoped: true,
      personal_scope: scope.sponsor,
      active_rule_count: snapshot.active_rule_count,
      projected_rule_count: snapshot.projected_rule_count,
      missing_rule_ids: Object.freeze([...snapshot.missing_rule_ids]),
    }),
    effects: V5_NO_EFFECTS,
  });
}

const FACT_SCHEMA = Object.freeze({
  type: "object",
  additionalProperties: false,
  properties: {
    action: { type: "string", minLength: 1, maxLength: 128 },
    audience: { type: "string", enum: ["internal", "partner", "client", "counterparty", "vendor", "public"] },
    environment: { type: "string", enum: ["development", "isolated_worktree", "staging", "production"] },
    lifecycle_transition: { type: "string", enum: ["none", "create", "update", "activate", "amend", "retire", "delete", "publish", "send"] },
    resource_class: { type: "string", minLength: 1, maxLength: 128 },
    risk_tier: { type: "string", enum: ["routine", "elevated", "consequential", "irreversible"] },
  },
  required: [...FACT_KEYS],
});

export function ruleContextRuntimeTools({ ToolError, withEnvelope }) {
  return {
    "read-action-context": {
      write: false,
      // The writer pool installs carr.acting_actor_slug and
      // carr.sponsoring_human_slug. The transaction remains read-only at the
      // verb contract: the connection choice supplies authenticated scope, not
      // mutation authority.
      writerConnection: true,
      description: "Read the authenticated actor's durable V5-F05 rule universe and derive a digest-bound applicability and coverage receipt. This read grants no authority; consequential_action_permitted is false whenever the authoritative census is incomplete or a binding control is unresolved.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { facts: FACT_SCHEMA }, required: ["facts"],
      },
      handler: async (client, actor, args) => {
        try {
          return await readActionContext(client, actor, args);
        } catch (error) {
          if (error?.code && typeof ToolError === "function") {
            throw new ToolError({ error: error.code, detail: error.detail ?? null, hint: error.message });
          }
          throw error;
        }
      },
    },
    "bind-rule-context-contract": {
      write: true,
      authorityOnly: true,
      description: "Append one Joe-authorized typed V5-F05 projection for a durable rule. Rule id, version, owner, scope, binding text, provenance, and observation time are re-derived from the rule row by the database; the caller supplies only the typed applicability and control classification.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string", format: "uuid" },
          rule_id: { type: "string", format: "uuid" },
          contract: {
            type: "object", additionalProperties: false,
            properties: {
              rule_class: { type: "string", enum: ["code_enforced", "workflow", "test", "scoped_judgment", "preference", "runtime_state"] },
              mandatory: { type: "boolean" },
              trigger: { type: "object" },
              control_effect: { type: ["object", "null"] },
              summary: { type: ["string", "null"] },
              code_enforcement: { type: ["object", "null"] },
              tests: { type: "array", items: { type: "string" } },
              no_machine_control_reason: { type: ["string", "null"] },
              retirement: { type: "object" },
              relations: { type: "array", items: { type: "object" } },
              scoped_validity: { type: ["object", "null"] },
            },
            required: ["rule_class", "mandatory", "trigger", "retirement"],
          },
        },
        required: ["idempotency_key", "rule_id", "contract"],
      },
      handler: async (client, actor, args) => {
        if (typeof withEnvelope !== "function") {
          throw new ToolError({ error: "rule_context_envelope_unavailable" });
        }
        return withEnvelope(client, actor, "bind-rule-context-contract", args, async () => {
          const response = await client.query(
            "select ops.bind_f05_rule_contract($1::uuid,$2::jsonb,$3::uuid) as result",
            [args.rule_id, args.contract, args.idempotency_key],
          );
          const result = response.rows[0]?.result;
          if (!isPlainObject(result) || result.ok !== true || !isPlainObject(result.contract)) {
            throw new ToolError({ error: "rule_context_contract_bind_failed" });
          }
          const trigger = isPlainObject(result.contract.trigger) ? result.contract.trigger : {};
          const actions = Array.isArray(trigger.action) ? trigger.action : [];
          const resources = Array.isArray(trigger.resource_class) ? trigger.resource_class : [];
          compileRuleUniverse({
            schema_version: "doctorcre-v5-f05-rule-universe.v1",
            universe_version: 1,
            tenant: ORGANIZATION_TENANT_ID,
            completeness: COMPLETE,
            declared_actions: [...new Set(["f05.universal", ...actions])].sort(),
            declared_resource_classes: [...new Set(["f05_record", ...resources])].sort(),
            rules: [result.contract],
          });
          return result;
        });
      },
    },
  };
}
