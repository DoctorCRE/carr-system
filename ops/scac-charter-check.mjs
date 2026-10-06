#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const CONTRACT_URL = new URL("./config/scac-charter.v1.json", import.meta.url);
const REVIEWED_CONTRACT_DIGEST = "473b7b1cd2ea975ba118f05406b35f4affdda0cb61f4487c252db129a882151c";

const TOP_LEVEL_KEYS = [
  "authority",
  "canonical_binding",
  "canonical_request_facts",
  "canonical_source_catalog",
  "contract_id",
  "core_model",
  "current_authority_reuse",
  "decision_algebra",
  "delivery_state",
  "forbidden_claims",
  "identity_contract",
  "package_boundaries",
  "program_key",
  "protected_classes",
  "protocol_invariants",
  "schema_version",
  "status",
  "taxonomy",
  "tenant_scope",
  "threat_taxonomy",
];

const CANONICAL_FACTS = [
  "artifact_trust_state",
  "compatibility_state",
  "connectivity_state",
  "device_assurance_state",
  "epoch_state",
  "kill_switch_state",
  "mutation_kind",
  "operation_manifest_digest",
  "principal_kind",
  "projection_state",
  "proof_state",
  "reference_monitor_state",
  "request_digest",
  "revocation_state",
  "root_trust_state",
  "target_surface",
  "tenant_scope",
  "token_state",
  "updater_state",
  "workload_state",
];

const PACKAGE_BOUNDARIES = new Map([
  ["11", "mutation_registry_default_deny_ingress"],
  ["12", "monotonic_epoch_compatibility"],
  ["13", "artifact_registry_signing_transparency"],
  ["14", "root_trust_offline_recovery"],
  ["15", "device_enrollment_assurance"],
  ["16", "proof_of_possession_verifiers"],
  ["17", "token_challenge_revocation_kill_switch"],
  ["18", "atomic_database_reference_monitor"],
  ["19", "signed_updater_convergence"],
  ["20", "shared_core_projection_declared_overlays"],
  ["21", "workload_identity"],
  ["22", "offline_partition_draft_only"],
  ["23", "health_slo_alerts_runbooks_completion"],
]);

const FORBIDDEN_CLAIMS = [
  "artifact_or_root_trust_operational",
  "atomic_database_mediation_operational",
  "default_deny_mutation_coverage_operational",
  "device_or_workload_identity_operational",
  "epoch_or_rollback_protection_operational",
  "health_or_completion_authority_operational",
  "offline_draft_only_enforcement_operational",
  "proof_token_challenge_or_revocation_operational",
  "production_ready_or_enforced",
  "shared_core_convergence_operational",
  "updater_operational",
];

const SHAPES = {
  authority: ["caller_labels_authority", "dell_participation", "dell_veto", "model_output_authority", "runtime_self_authority", "system_authority"],
  canonical_binding: ["component_alias", "decision_provenance", "dependency_package_keys", "embedded_scac_decision_id", "master_decision_id", "package_key", "package_ref", "required_evidence_kinds"],
  decision_provenance: ["embedded_contract_binding", "master_repository_binding", "source_host_id", "source_kind", "source_thread_id"],
  source_catalog: ["id", "implementation_state", "owner_package", "source_class"],
  core_model: ["connected_partners_share_current_core", "current_core_count", "dell_filesystem_admin_actions", "dell_never_freezes_central_or_joe", "ordinary_business_data_shared_between_joe_and_dell", "overlays", "studio_executor", "unsupported_macs"],
  identity_contract: ["caller_labels", "capability_semantics", "collapse_or_inheritance", "derivation", "required_distinct_axes"],
  authority_reuse: ["authority_limit", "id", "physical_sources", "reuse_mode"],
  decision_algebra: ["caller_or_model_fields_may_escalate", "contributing_reasons", "missing_canonical_source", "mode", "permit_before_downstream_completion", "primary_reason", "unknown_category"],
  delivery_state: ["deploy", "enforcement", "migration", "production_effect", "readback", "rollback", "source"],
  package_boundary: ["control", "implementation_state", "package_key"],
  taxonomy: ["mutation_kinds", "principal_kinds", "refusal_reasons", "target_surfaces"],
  taxonomy_item: ["canonical_source", "definition", "id", "implementation_state", "owner_package"],
  refusal: ["canonical_source", "definition", "id", "implementation_state", "offline_posture", "owner_package", "precedence", "required_evidence", "retryable", "safe_projection"],
  threat: ["id", "implementation_state", "owner_package", "threat", "workspace_threat_refs"],
};

const KNOWN_STATES = {
  artifact_trust_state: new Set(["trusted", "untrusted"]),
  compatibility_state: new Set(["compatible", "incompatible"]),
  connectivity_state: new Set(["online", "offline", "partitioned"]),
  device_assurance_state: new Set(["assured", "unassured"]),
  epoch_state: new Set(["current", "stale", "future", "rolled_back"]),
  kill_switch_state: new Set(["inactive", "active"]),
  proof_state: new Set(["valid", "invalid", "missing", "replayed"]),
  projection_state: new Set(["current", "drift"]),
  reference_monitor_state: new Set(["current", "unavailable", "stale"]),
  revocation_state: new Set(["clear", "revoked"]),
  root_trust_state: new Set(["trusted", "untrusted"]),
  token_state: new Set(["valid", "missing", "expired", "replayed", "revoked"]),
  updater_state: new Set(["converged", "not_converged", "downgraded", "split_brain"]),
  workload_state: new Set(["registered", "unregistered", "stale"]),
};

function fail(message) {
  throw new Error(`SCAC charter invalid: ${message}`);
}

function exactKeys(value, expected, path) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${path} must be an object`);
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
    fail(`${path} keys must be exactly ${wanted.join(", ")}; received ${actual.join(", ")}`);
  }
}

function exactStringSet(value, expected, path) {
  if (!Array.isArray(value) || value.some(item => typeof item !== "string")) fail(`${path} must be a string array`);
  if (new Set(value).size !== value.length) fail(`${path} contains a duplicate`);
  const actual = [...value].sort();
  const wanted = [...expected].sort();
  if (JSON.stringify(actual) !== JSON.stringify(wanted)) fail(`${path} is not the reviewed exact set`);
}

function stableId(value, family, path) {
  if (typeof value !== "string" || !new RegExp(`^scac\\.${family}\\.[a-z][a-z0-9_]*$`).test(value)) {
    fail(`${path} must be a stable scac.${family} identifier`);
  }
  if (/\.(?:scope|pack|enforcement|failure_class)(?:\.|$)/.test(value)) fail(`${path} collides with an existing taxonomy`);
  if (value.includes("*") || value.includes("?")) fail(`${path} cannot contain a wildcard`);
}

function nonemptyStrings(value, path) {
  if (!Array.isArray(value) || value.length === 0 || value.some(item => typeof item !== "string" || item.length === 0)) {
    fail(`${path} must be a nonempty string array`);
  }
  if (new Set(value).size !== value.length) fail(`${path} contains a duplicate`);
}

function validateTaxonomyItem(item, family, path, seen) {
  exactKeys(item, SHAPES.taxonomy_item, path);
  stableId(item.id, family, `${path}.id`);
  if (seen.has(item.id)) fail(`${path}.id duplicates ${item.id}`);
  seen.add(item.id);
  for (const key of ["definition", "canonical_source"]) {
    if (typeof item[key] !== "string" || item[key].trim() === "") fail(`${path}.${key} must be nonempty`);
  }
  if (!PACKAGE_BOUNDARIES.has(item.owner_package)) fail(`${path}.owner_package is outside SIEP-11..23`);
  if (item.implementation_state !== "declared_unimplemented") fail(`${path} must remain declared_unimplemented`);
}

function validateSafeProjection(fields, path) {
  nonemptyStrings(fields, path);
  for (const field of fields) {
    const secretBearing = /(?:^|_)(?:secret|password|credential|private_key|lease|session|payload|body)(?:_|$)/.test(field);
    const rawToken = /(?:^|_)(?:token|challenge|proof)(?:_|$)/.test(field) && !/(?:_digest|_state)$/.test(field);
    if (secretBearing || rawToken) fail(`${path} contains secret-bearing field ${field}`);
  }
}

export function canonicalize(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalize).sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  }
  return value;
}

export function digestContract(contract) {
  return createHash("sha256").update(JSON.stringify(canonicalize(contract))).digest("hex");
}

export function validateCharter(contract) {
  exactKeys(contract, TOP_LEVEL_KEYS, "contract");
  if (contract.schema_version !== "scac-charter.v1") fail("schema_version must be scac-charter.v1");
  if (contract.contract_id !== "carr.scac.charter") fail("contract_id must be carr.scac.charter");
  if (contract.program_key !== "carr-system-integrity-elimination-v1") fail("program_key must be the sealed SIEP program");
  if (contract.tenant_scope !== "carr-internal") fail("tenant_scope must be carr-internal");
  if (contract.status !== "specification_only") fail("status must remain specification_only");

  exactKeys(contract.canonical_binding, SHAPES.canonical_binding, "canonical_binding");
  const binding = contract.canonical_binding;
  if (binding.master_decision_id !== "e1e04703-4de6-4947-b2fa-ea0133c6bd74" || binding.embedded_scac_decision_id !== "e6ed7449-a149-4969-851c-cb047fbc78d1") fail("reviewed decision binding changed");
  exactKeys(binding.decision_provenance, SHAPES.decision_provenance, "canonical_binding.decision_provenance");
  if (binding.decision_provenance.source_kind !== "codex_delegation" || binding.decision_provenance.source_host_id !== "slingshot:env_e_6a7a05fd17a083339f33f6e3ba3bf69f" || binding.decision_provenance.source_thread_id !== "01a03987-0b24-7052-bc8c-a648dc0e0215") fail("delegated reviewed decision provenance changed");
  if (binding.decision_provenance.master_repository_binding !== "migrations/0324_siep_program_authority.sql#shape_fixed_surface_ref" || binding.decision_provenance.embedded_contract_binding !== "ops/config/scac-charter.v1.json#canonical_binding") fail("decision source binding changed");
  if (binding.package_key !== "10" || binding.package_ref !== "WR-SIEP-10" || binding.component_alias !== "SCAC-00") fail("SIEP-10 package binding changed");
  exactStringSet(binding.dependency_package_keys, ["B0"], "canonical_binding.dependency_package_keys");
  exactStringSet(binding.required_evidence_kinds, ["source", "tests", "readback", "rollback", "independent_review"], "canonical_binding.required_evidence_kinds");

  exactKeys(contract.authority, SHAPES.authority, "authority");
  if (contract.authority.system_authority !== "joe" || contract.authority.dell_participation !== "permitted_optional_nonblocking") fail("authority split changed");
  for (const key of ["dell_veto", "runtime_self_authority", "model_output_authority", "caller_labels_authority"]) {
    if (contract.authority[key] !== false) fail(`authority.${key} must be false`);
  }

  exactKeys(contract.core_model, SHAPES.core_model, "core_model");
  if (contract.core_model.current_core_count !== 1 || contract.core_model.connected_partners_share_current_core !== true) fail("one shared current core is required");
  if (contract.core_model.dell_never_freezes_central_or_joe !== true) fail("Dell must not freeze Joe or central");
  if (contract.core_model.studio_executor !== "optional_non_blocking_capacity_only") fail("Studio must remain optional and nonblocking");
  if (contract.core_model.dell_filesystem_admin_actions !== "dell_mpe_runner_only") fail("Dell-local administration must remain on MPE");

  nonemptyStrings(contract.protected_classes, "protected_classes");
  contract.protected_classes.forEach((id, index) => stableId(id, "protected", `protected_classes[${index}]`));
  nonemptyStrings(contract.protocol_invariants, "protocol_invariants");
  exactStringSet(contract.forbidden_claims, FORBIDDEN_CLAIMS, "forbidden_claims");

  exactKeys(contract.identity_contract, SHAPES.identity_contract, "identity_contract");
  exactStringSet(contract.identity_contract.required_distinct_axes, ["tenant", "actor", "sponsor", "partner", "agent", "runtime", "device", "workload", "session", "capability"], "identity_contract.required_distinct_axes");
  if (contract.identity_contract.derivation !== "server_derived_canonical_references_only" || contract.identity_contract.caller_labels !== "descriptive_non_authoritative" || contract.identity_contract.collapse_or_inheritance !== "forbidden" || contract.identity_contract.capability_semantics !== "non_transitive_non_inheritable_session_bound_expiring") fail("identity authority boundary changed");

  if (!Array.isArray(contract.current_authority_reuse) || contract.current_authority_reuse.length < 8) fail("current_authority_reuse is incomplete");
  const reuseIds = new Set();
  for (const [index, item] of contract.current_authority_reuse.entries()) {
    const path = `current_authority_reuse[${index}]`;
    exactKeys(item, SHAPES.authority_reuse, path);
    stableId(item.id, "reuse", `${path}.id`);
    if (reuseIds.has(item.id)) fail(`${path}.id duplicates ${item.id}`);
    reuseIds.add(item.id);
    nonemptyStrings(item.physical_sources, `${path}.physical_sources`);
    if (!new Set(["reference_only_no_copy", "diagnostic_only_never_authority"]).has(item.reuse_mode)) fail(`${path}.reuse_mode may not copy, shadow, or replace authority`);
    if (typeof item.authority_limit !== "string" || !item.authority_limit.trim()) fail(`${path}.authority_limit must be explicit`);
  }
  for (const required of ["scac.reuse.human_attribution", "scac.reuse.work_execution", "scac.reuse.rule_delivery", "scac.reuse.siep_lifecycle", "scac.reuse.action_risk_diagnostic"]) {
    if (!reuseIds.has(required)) fail(`missing current authority reuse ${required}`);
  }
  const reuseById = new Map(contract.current_authority_reuse.map(item => [item.id, item]));
  if (reuseById.get("scac.reuse.action_risk_diagnostic").reuse_mode !== "diagnostic_only_never_authority" || !/known stale.*140 verbs.*184/i.test(reuseById.get("scac.reuse.action_risk_diagnostic").authority_limit)) fail("stale action-risk diagnostics cannot be promoted to authority");
  if (!/never artifact or root trust/i.test(reuseById.get("scac.reuse.artifact_precedent").authority_limit)) fail("hash and signature references cannot be promoted to trust");
  if (!/shadow store/i.test(reuseById.get("scac.reuse.work_execution").authority_limit)) fail("work execution reuse must prohibit a shadow store");
  if (!/sole SIEP lifecycle/i.test(reuseById.get("scac.reuse.siep_lifecycle").authority_limit)) fail("B0 must remain sole SIEP lifecycle authority");

  const expectedFutureSources = new Map([
    ["scac.mutation.registry", "11"], ["scac.policy.epoch", "12"], ["scac.artifact.registry", "13"], ["scac.root.trust", "14"],
    ["scac.device.enrollment", "15"], ["scac.pop.verifier", "16"], ["scac.token.revocation", "17"], ["scac.db.reference_monitor", "18"],
    ["scac.updater.convergence", "19"], ["scac.core.projection", "20"], ["scac.workload.identity", "21"], ["scac.offline.policy", "22"], ["scac.health.evidence", "23"],
  ]);
  if (!Array.isArray(contract.canonical_source_catalog) || contract.canonical_source_catalog.length !== expectedFutureSources.size + 3) fail("canonical_source_catalog must be the exact reviewed source set");
  const sourceCatalog = new Map();
  for (const [index, item] of contract.canonical_source_catalog.entries()) {
    const path = `canonical_source_catalog[${index}]`;
    exactKeys(item, SHAPES.source_catalog, path);
    if (typeof item.id !== "string" || !item.id.trim() || sourceCatalog.has(item.id)) fail(`${path}.id is missing or duplicate`);
    sourceCatalog.set(item.id, item);
    if (item.source_class === "future_logical") {
      if (expectedFutureSources.get(item.id) !== item.owner_package || item.implementation_state !== "declared_unimplemented") fail(`${path} future source ownership changed`);
    } else if (item.source_class === "specification") {
      if (item.id !== "scac.charter.taxonomy" || item.owner_package !== "10" || item.implementation_state !== "specification_only") fail(`${path} invalid specification source`);
    } else if (item.source_class !== "current_reused") fail(`${path}.source_class is unknown`);
  }
  for (const [id] of expectedFutureSources) if (!sourceCatalog.has(id)) fail(`future canonical source ${id} is missing`);
  const currentPrincipal = sourceCatalog.get("authenticated_session_principal");
  const currentLifecycle = sourceCatalog.get("siep.package.lifecycle");
  if (!currentPrincipal || currentPrincipal.source_class !== "current_reused" || currentPrincipal.owner_package !== "18" || currentPrincipal.implementation_state !== "current_limited_nonuniform") fail("current session principal limits changed");
  if (!currentLifecycle || currentLifecycle.source_class !== "current_reused" || currentLifecycle.owner_package !== "B0" || currentLifecycle.implementation_state !== "current") fail("B0 lifecycle source changed");

  if (!Array.isArray(contract.threat_taxonomy) || contract.threat_taxonomy.length !== 13) fail("threat_taxonomy must contain exactly 13 owned threats");
  const workspaceThreatIds = new Set(Array.from({ length: 18 }, (_, index) => `T${String(index + 1).padStart(2, "0")}`));
  const threatIds = new Set();
  contract.threat_taxonomy.forEach((item, index) => {
    const path = `threat_taxonomy[${index}]`;
    exactKeys(item, SHAPES.threat, path);
    const expectedId = `SCAC-TH-${String(index + 1).padStart(2, "0")}`;
    const expectedOwner = String(index + 11);
    if (item.id !== expectedId || threatIds.has(item.id)) fail(`${path}.id must be ${expectedId}`);
    threatIds.add(item.id);
    if (item.owner_package !== expectedOwner || item.implementation_state !== "declared_unimplemented") fail(`${path} must be owned by unimplemented SIEP-${expectedOwner}`);
    if (typeof item.threat !== "string" || !/^[a-z][a-z0-9_]+$/.test(item.threat)) fail(`${path}.threat must be a closed stable label`);
    nonemptyStrings(item.workspace_threat_refs, `${path}.workspace_threat_refs`);
    if (item.workspace_threat_refs.some(id => !workspaceThreatIds.has(id))) fail(`${path} references an unknown Workspace threat`);
  });

  exactKeys(contract.taxonomy, SHAPES.taxonomy, "taxonomy");
  const seen = new Set();
  for (const [key, family] of [["principal_kinds", "principal"], ["mutation_kinds", "mutation"], ["target_surfaces", "surface"]]) {
    if (!Array.isArray(contract.taxonomy[key]) || contract.taxonomy[key].length === 0) fail(`taxonomy.${key} must be nonempty`);
    contract.taxonomy[key].forEach((item, index) => validateTaxonomyItem(item, family, `taxonomy.${key}[${index}]`, seen));
  }

  const refusals = contract.taxonomy.refusal_reasons;
  if (!Array.isArray(refusals) || refusals.length === 0) fail("taxonomy.refusal_reasons must be nonempty");
  const precedence = new Set();
  refusals.forEach((item, index) => {
    const path = `taxonomy.refusal_reasons[${index}]`;
    exactKeys(item, SHAPES.refusal, path);
    stableId(item.id, "refusal", `${path}.id`);
    if (seen.has(item.id)) fail(`${path}.id duplicates ${item.id}`);
    seen.add(item.id);
    if (typeof item.definition !== "string" || !item.definition.trim()) fail(`${path}.definition must be nonempty`);
    if (typeof item.canonical_source !== "string" || !item.canonical_source.trim()) fail(`${path}.canonical_source must be nonempty`);
    if (!sourceCatalog.has(item.canonical_source)) fail(`${path}.canonical_source is not cataloged`);
    if (!Number.isSafeInteger(item.precedence) || precedence.has(item.precedence)) fail(`${path}.precedence must be a unique safe integer`);
    precedence.add(item.precedence);
    if (typeof item.retryable !== "boolean" || item.offline_posture !== "deny") fail(`${path} must have typed retryability and deny offline posture`);
    nonemptyStrings(item.required_evidence, `${path}.required_evidence`);
    validateSafeProjection(item.safe_projection, `${path}.safe_projection`);
    if (item.id === "scac.refusal.malformed_or_unknown") {
      if (item.owner_package !== "10" || item.implementation_state !== "specification_only") fail(`${path} is the only SIEP-10 specification reason`);
    } else {
      if (!PACKAGE_BOUNDARIES.has(item.owner_package)) fail(`${path}.owner_package is outside SIEP-11..23`);
      if (item.implementation_state !== "declared_unimplemented") fail(`${path} must remain declared_unimplemented`);
    }
  });
  for (const required of ["scac.refusal.kill_switch", "scac.refusal.revoked", "scac.refusal.malformed_or_unknown", "scac.refusal.control_unimplemented"]) {
    if (!seen.has(required)) fail(`required reason ${required} is absent`);
  }
  for (const key of ["principal_kinds", "mutation_kinds", "target_surfaces"]) {
    for (const item of contract.taxonomy[key]) if (!sourceCatalog.has(item.canonical_source)) fail(`${item.id} canonical_source is not cataloged`);
  }
  const tokenInvalid = refusals.find(item => item.id === "scac.refusal.token_invalid");
  if (!tokenInvalid || tokenInvalid.retryable !== false) fail("token replay/revocation must not be retryable");
  const scopeMismatch = refusals.find(item => item.id === "scac.refusal.scope_mismatch");
  if (!scopeMismatch || scopeMismatch.owner_package !== "18" || scopeMismatch.safe_projection.includes("tenant")) fail("scope refusal must be reference-monitor owned and redact tenant identity");
  if (!refusals.some(item => item.owner_package === "19" && item.canonical_source === "scac.updater.convergence")) fail("updater convergence needs a distinct refusal path");
  if (!refusals.some(item => item.owner_package === "20" && item.id === "scac.refusal.projection_drift")) fail("projection drift needs a distinct refusal path");

  exactKeys(contract.decision_algebra, SHAPES.decision_algebra, "decision_algebra");
  if (contract.decision_algebra.mode !== "deny_dominant_specification" || contract.decision_algebra.permit_before_downstream_completion !== false || contract.decision_algebra.caller_or_model_fields_may_escalate !== false) fail("decision algebra could authorize prematurely");
  exactStringSet(contract.canonical_request_facts, CANONICAL_FACTS, "canonical_request_facts");

  if (!Array.isArray(contract.package_boundaries) || contract.package_boundaries.length !== PACKAGE_BOUNDARIES.size) fail("package_boundaries must cover exactly SIEP-11..23");
  const boundaryKeys = new Set();
  contract.package_boundaries.forEach((item, index) => {
    exactKeys(item, SHAPES.package_boundary, `package_boundaries[${index}]`);
    if (boundaryKeys.has(item.package_key)) fail(`duplicate package boundary ${item.package_key}`);
    boundaryKeys.add(item.package_key);
    if (PACKAGE_BOUNDARIES.get(item.package_key) !== item.control) fail(`package ${item.package_key} control changed`);
    if (item.implementation_state !== "declared_unimplemented") fail(`package ${item.package_key} cannot claim implementation`);
  });
  if ([...PACKAGE_BOUNDARIES.keys()].some(key => !boundaryKeys.has(key))) fail("a SIEP-11..23 package boundary is missing");

  exactKeys(contract.delivery_state, SHAPES.delivery_state, "delivery_state");
  const delivery = contract.delivery_state;
  if (delivery.source !== "implemented_declarative_contract" || delivery.migration !== "not_required" || delivery.deploy !== "not_active" || delivery.enforcement !== "not_active" || delivery.production_effect !== "none") fail("delivery state exceeds SIEP-10 authority");

  const serialized = JSON.stringify(contract);
  if (/failure_class/.test(serialized)) fail("failure_class belongs to the existing operational taxonomy");
  if (/"aliases?"\s*:/.test(serialized) || /[*?]/.test(contract.taxonomy.principal_kinds.map(item => item.id).join(""))) fail("aliases and wildcards are forbidden");

  const digest = digestContract(contract);
  if (digest !== REVIEWED_CONTRACT_DIGEST) fail("semantic bytes differ from the reviewed immutable v1 contract; create a new version instead of relabeling v1");

  return {
    valid: true,
    schema_version: contract.schema_version,
    contract_id: contract.contract_id,
    digest,
    taxonomy_counts: {
      mutation_kinds: contract.taxonomy.mutation_kinds.length,
      principal_kinds: contract.taxonomy.principal_kinds.length,
      refusal_reasons: refusals.length,
      target_surfaces: contract.taxonomy.target_surfaces.length,
    },
    operational: false,
    authorizes: false,
  };
}

function safeRefusal(reason) {
  return { reason_id: reason.id, precedence: reason.precedence, retryable: reason.retryable };
}

export function evaluateScacSpecification(contract, facts) {
  validateCharter(contract);
  const reasons = new Map(contract.taxonomy.refusal_reasons.map(reason => [reason.id, reason]));
  const selected = new Set(["scac.refusal.control_unimplemented"]);
  const malformed = !facts || typeof facts !== "object" || Array.isArray(facts) ||
    Object.keys(facts).some(key => !CANONICAL_FACTS.includes(key)) ||
    CANONICAL_FACTS.some(key => !(key in (facts ?? {}))) ||
    CANONICAL_FACTS.some(key => typeof facts[key] !== "string" || facts[key].length === 0);

  if (malformed) {
    selected.add("scac.refusal.malformed_or_unknown");
  } else {
    for (const [key, allowed] of Object.entries(KNOWN_STATES)) if (!allowed.has(facts[key])) selected.add("scac.refusal.malformed_or_unknown");
    const principalIds = new Set(contract.taxonomy.principal_kinds.map(item => item.id));
    const mutationIds = new Set(contract.taxonomy.mutation_kinds.map(item => item.id));
    const surfaceIds = new Set(contract.taxonomy.target_surfaces.map(item => item.id));
    if (!principalIds.has(facts.principal_kind) || !mutationIds.has(facts.mutation_kind) || !surfaceIds.has(facts.target_surface)) selected.add("scac.refusal.malformed_or_unknown");
    if (!/^[0-9a-f]{64}$/.test(facts.request_digest) || !/^[0-9a-f]{64}$/.test(facts.operation_manifest_digest)) selected.add("scac.refusal.malformed_or_unknown");
    if (facts.kill_switch_state === "active") selected.add("scac.refusal.kill_switch");
    if (facts.revocation_state === "revoked" || facts.token_state === "revoked") selected.add("scac.refusal.revoked");
    if (facts.connectivity_state !== "online") selected.add("scac.refusal.offline_mutation");
    if (facts.artifact_trust_state !== "trusted") selected.add("scac.refusal.untrusted_artifact");
    if (facts.root_trust_state !== "trusted") selected.add("scac.refusal.root_untrusted");
    if (facts.epoch_state !== "current" || facts.compatibility_state !== "compatible") selected.add("scac.refusal.epoch_incompatible");
    if (facts.proof_state !== "valid") selected.add("scac.refusal.proof_invalid");
    if (facts.token_state !== "valid") selected.add("scac.refusal.token_invalid");
    if (facts.device_assurance_state !== "assured") selected.add("scac.refusal.device_unassured");
    if (facts.workload_state !== "registered") selected.add("scac.refusal.workload_unregistered");
    if (facts.tenant_scope !== contract.tenant_scope) selected.add("scac.refusal.scope_mismatch");
    if (facts.reference_monitor_state !== "current") selected.add("scac.refusal.reference_monitor_unavailable");
    if (facts.updater_state !== "converged") selected.add("scac.refusal.core_not_converged");
    if (facts.projection_state !== "current") selected.add("scac.refusal.projection_drift");
  }

  const ordered = [...selected].map(id => reasons.get(id)).filter(Boolean)
    .sort((left, right) => right.precedence - left.precedence || left.id.localeCompare(right.id));
  return {
    schema_version: "scac-specification-decision.v1",
    contract_digest: digestContract(contract),
    status: "refused_specification_only",
    operational: false,
    authorizes: false,
    primary_reason: safeRefusal(ordered[0]),
    contributing_reasons: ordered.map(safeRefusal),
  };
}

export async function loadScacCharter(url = CONTRACT_URL) {
  return JSON.parse(await readFile(url, "utf8"));
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const contract = await loadScacCharter(process.argv[2] ? new URL(`file://${resolve(process.argv[2])}`) : CONTRACT_URL);
  process.stdout.write(`${JSON.stringify(validateCharter(contract), null, 2)}\n`);
}
