import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

import {
  canonicalize,
  digestContract,
  evaluateScacSpecification,
  validateCharter,
} from "../../ops/scac-charter-check.mjs";

const contract = JSON.parse(fs.readFileSync(
  new URL("../../ops/config/scac-charter.v1.json", import.meta.url),
  "utf8",
));
const authorityMigration = fs.readFileSync(
  new URL("../../migrations/0324_siep_program_authority.sql", import.meta.url),
  "utf8",
);

const clone = value => structuredClone(value);
const reject = (mutate, pattern = /SCAC charter invalid/) => {
  const candidate = clone(contract);
  mutate(candidate);
  assert.throws(() => validateCharter(candidate), pattern);
};

const healthyFacts = Object.freeze({
  artifact_trust_state: "trusted",
  compatibility_state: "compatible",
  connectivity_state: "online",
  device_assurance_state: "assured",
  epoch_state: "current",
  kill_switch_state: "inactive",
  mutation_kind: "scac.mutation.business_record",
  operation_manifest_digest: "a".repeat(64),
  principal_kind: "scac.principal.human_interactive",
  projection_state: "current",
  proof_state: "valid",
  reference_monitor_state: "current",
  request_digest: "b".repeat(64),
  revocation_state: "clear",
  root_trust_state: "trusted",
  target_surface: "scac.surface.database",
  tenant_scope: "carr-internal",
  token_state: "valid",
  updater_state: "converged",
  workload_state: "registered",
});

test("SIEP-10 is an exact specification-only charter with a stable digest", () => {
  const result = validateCharter(contract);
  assert.deepEqual(result, {
    valid: true,
    schema_version: "scac-charter.v1",
    contract_id: "carr.scac.charter",
    digest: "473b7b1cd2ea975ba118f05406b35f4affdda0cb61f4487c252db129a882151c",
    taxonomy_counts: {
      mutation_kinds: 10,
      principal_kinds: 4,
      refusal_reasons: 17,
      target_surfaces: 8,
    },
    operational: false,
    authorizes: false,
  });
  assert.equal(contract.delivery_state.migration, "not_required");
  assert.equal(contract.delivery_state.deploy, "not_active");
  assert.equal(contract.delivery_state.enforcement, "not_active");
  assert.equal(contract.delivery_state.production_effect, "none");
  assert.ok(contract.forbidden_claims.includes("production_ready_or_enforced"));
});

test("canonical binding reuses the sealed SIEP package, DAG, alias, and evidence contract", () => {
  assert.deepEqual(contract.canonical_binding, {
    master_decision_id: "e1e04703-4de6-4947-b2fa-ea0133c6bd74",
    embedded_scac_decision_id: "e6ed7449-a149-4969-851c-cb047fbc78d1",
    decision_provenance: {
      source_kind: "codex_delegation",
      source_host_id: "slingshot:env_e_6a7a05fd17a083339f33f6e3ba3bf69f",
      source_thread_id: "01a03987-0b24-7052-bc8c-a648dc0e0215",
      master_repository_binding: "migrations/0324_siep_program_authority.sql#shape_fixed_surface_ref",
      embedded_contract_binding: "ops/config/scac-charter.v1.json#canonical_binding",
    },
    package_key: "10",
    package_ref: "WR-SIEP-10",
    component_alias: "SCAC-00",
    dependency_package_keys: ["B0"],
    required_evidence_kinds: ["source", "tests", "readback", "rollback", "independent_review"],
  });
  assert.match(authorityMigration, /\('10',10,'WR-SIEP-10','SCAC charter and taxonomy'/);
  assert.match(authorityMigration, /\('10','B0'\)/);
  const aliases = [...authorityMigration.matchAll(/\('SCAC-(\d{2})','([^']+)'\)/g)]
    .map(([, alias, packageKey]) => [`SCAC-${alias}`, packageKey]);
  assert.deepEqual(aliases, [
    ["SCAC-00", "10"], ["SCAC-01", "11"], ["SCAC-02", "12"], ["SCAC-03", "13"],
    ["SCAC-04", "14"], ["SCAC-05", "15"], ["SCAC-06", "16"], ["SCAC-07", "17"],
    ["SCAC-08", "18"], ["SCAC-09", "19"], ["SCAC-10", "20"], ["SCAC-11", "21"],
    ["SCAC-12", "22"], ["SCAC-13", "23"], ["SCAC-14", "24A"], ["SCAC-15", "25"],
    ["SCAC-16", "26"],
  ]);
  assert.doesNotMatch(contract.canonical_binding.component_alias, /^(?:SCAC-(?:17|24)|MPE-)/);
});

test("charter names every existing authority it reuses and forbids shadow ownership", () => {
  const modes = new Set(contract.current_authority_reuse.map(item => item.reuse_mode));
  assert.deepEqual(modes, new Set(["reference_only_no_copy", "diagnostic_only_never_authority"]));
  assert.ok(contract.current_authority_reuse.some(item => item.id === "scac.reuse.siep_lifecycle" && /sole SIEP lifecycle/.test(item.authority_limit)));
  assert.ok(contract.current_authority_reuse.some(item => item.id === "scac.reuse.work_execution" && /shadow store/.test(item.authority_limit)));
  assert.ok(contract.current_authority_reuse.some(item => item.id === "scac.reuse.action_risk_diagnostic" && /known stale.*140 verbs.*184/i.test(item.authority_limit)));
  assert.ok(contract.current_authority_reuse.some(item => item.id === "scac.reuse.artifact_precedent" && /never artifact or root trust/.test(item.authority_limit)));
  assert.deepEqual(contract.identity_contract.required_distinct_axes, ["tenant", "actor", "sponsor", "partner", "agent", "runtime", "device", "workload", "session", "capability"]);

  for (const path of ["control-room/contracts/action-risk-registry.v1.json", "ops/action-risk-registry.py"]) {
    assert.equal(fs.existsSync(new URL(`../../${path}`, import.meta.url)), true, `${path} must resolve`);
  }
  const deviceMigration = fs.readFileSync(new URL("../../migrations/0163_control_plane_device_evidence.sql", import.meta.url), "utf8");
  const renewalMigration = fs.readFileSync(new URL("../../migrations/0249_renewal_signed_source_ingress.sql", import.meta.url), "utf8");
  const calendarMigration = fs.readFileSync(new URL("../../migrations/0229_calendar_prebrief_projection.sql", import.meta.url), "utf8");
  assert.match(deviceMigration, /create table if not exists ops\.device_evidence_receipt/);
  assert.match(renewalMigration, /create function ops\.ingest_renewal_signed_snapshot/);
  assert.match(calendarMigration, /create or replace function ops\.record_calendar_prebrief_verified_envelope/);
});

test("threats and future logical sources have exact downstream ownership", () => {
  assert.deepEqual(contract.threat_taxonomy.map(item => [item.id, item.owner_package]),
    Array.from({ length: 13 }, (_, index) => [`SCAC-TH-${String(index + 1).padStart(2, "0")}`, String(index + 11)]));
  assert.ok(contract.threat_taxonomy.every(item => item.implementation_state === "declared_unimplemented"));
  assert.ok(contract.threat_taxonomy.flatMap(item => item.workspace_threat_refs).every(id => /^T(?:0[1-9]|1[0-8])$/.test(id)));
  const future = contract.canonical_source_catalog.filter(item => item.source_class === "future_logical");
  assert.deepEqual(future.map(item => item.owner_package).sort((a, b) => Number(a) - Number(b)), Array.from({ length: 13 }, (_, index) => String(index + 11)));
  assert.ok(future.every(item => item.implementation_state === "declared_unimplemented"));
});

test("canonicalization is order-independent but any semantic change changes the digest", () => {
  const reordered = clone(contract);
  reordered.taxonomy.mutation_kinds.reverse();
  reordered.protocol_invariants.reverse();
  assert.deepEqual(canonicalize(reordered), canonicalize(contract));
  assert.equal(digestContract(reordered), digestContract(contract));
  const changed = clone(contract);
  changed.taxonomy.mutation_kinds[0].definition += " changed";
  assert.notEqual(digestContract(changed), digestContract(contract));
});

test("deny algebra never authorizes and is stable under input order", () => {
  const healthy = evaluateScacSpecification(contract, healthyFacts);
  assert.equal(healthy.authorizes, false);
  assert.equal(healthy.operational, false);
  assert.equal(healthy.primary_reason.reason_id, "scac.refusal.control_unimplemented");

  const hostile = { ...healthyFacts, kill_switch_state: "active", revocation_state: "revoked", connectivity_state: "offline", token_state: "replayed" };
  const reverseOrder = Object.fromEntries(Object.entries(hostile).reverse());
  const first = evaluateScacSpecification(contract, hostile);
  const second = evaluateScacSpecification(contract, reverseOrder);
  assert.deepEqual(first, second);
  assert.equal(first.primary_reason.reason_id, "scac.refusal.kill_switch");
  assert.deepEqual(first.contributing_reasons.map(item => item.reason_id).slice(0, 4), [
    "scac.refusal.kill_switch",
    "scac.refusal.revoked",
    "scac.refusal.offline_mutation",
    "scac.refusal.token_invalid",
  ]);
});

test("unknown, malformed, caller-authored, stale, and offline inputs fail closed", () => {
  for (const facts of [
    { ...healthyFacts, mutation_kind: "scac.mutation.typo_minted" },
    { ...healthyFacts, target_surface: "*" },
    { ...healthyFacts, request_digest: "not-a-digest" },
    { ...healthyFacts, caller_actor: "joe" },
    { ...healthyFacts, tenant_scope: "foreign-tenant" },
    { ...healthyFacts, connectivity_state: "partitioned" },
  ]) {
    const decision = evaluateScacSpecification(contract, facts);
    assert.equal(decision.authorizes, false);
    assert.equal(decision.status, "refused_specification_only");
  }
  assert.equal(evaluateScacSpecification(contract, { ...healthyFacts, caller_actor: "joe" }).primary_reason.reason_id, "scac.refusal.malformed_or_unknown");
  assert.equal(evaluateScacSpecification(contract, { ...healthyFacts, connectivity_state: "offline" }).primary_reason.reason_id, "scac.refusal.offline_mutation");
  const replayedProof = evaluateScacSpecification(contract, { ...healthyFacts, proof_state: "replayed" });
  assert.equal(replayedProof.primary_reason.reason_id, "scac.refusal.proof_invalid");
  assert.equal(replayedProof.primary_reason.retryable, false);
});

test("safe decisions redact canonical inputs and recursive secret material", () => {
  const facts = { ...healthyFacts, request_digest: "c".repeat(64), operation_manifest_digest: "d".repeat(64) };
  const decision = evaluateScacSpecification(contract, facts);
  const serialized = JSON.stringify(decision);
  assert.doesNotMatch(serialized, /c{64}|d{64}|carr-internal|human_interactive|business_record/);
  const forbiddenKeys = /(?:token|challenge|secret|password|credential|private_key|lease|session|payload|body)(?!_(?:digest|state))/i;
  const walk = value => {
    if (Array.isArray(value)) return value.forEach(walk);
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      assert.doesNotMatch(key, forbiddenKeys);
      walk(child);
    }
  };
  walk(decision);
});

test("closed-shape adversaries cannot expand authority or promote diagnostics", () => {
  reject(value => { value.caller_actor = "joe"; }, /keys must be exactly/);
  reject(value => { value.threat_taxonomy.pop(); }, /exactly 13/);
  reject(value => { value.threat_taxonomy[0].owner_package = "12"; }, /owned by unimplemented/);
  reject(value => { value.threat_taxonomy[0].workspace_threat_refs = ["T19"]; }, /unknown Workspace threat/);
  reject(value => { value.current_authority_reuse[0].reuse_mode = "copy_and_replace"; }, /may not copy/);
  reject(value => { value.identity_contract.collapse_or_inheritance = "allowed"; }, /identity authority boundary/);
  reject(value => { value.authority.caller_labels_authority = true; }, /must be false/);
  reject(value => { value.taxonomy.refusal_reasons.find(item => item.id === "scac.refusal.scope_mismatch").safe_projection.push("tenant"); }, /scope refusal.*redact tenant/);
  reject(value => { value.taxonomy.refusal_reasons.find(item => item.id === "scac.refusal.token_invalid").retryable = true; }, /must not be retryable/);
  reject(value => { value.current_authority_reuse.find(item => item.id === "scac.reuse.action_risk_diagnostic").reuse_mode = "reference_only_no_copy"; }, /diagnostics cannot be promoted/);
  reject(value => { value.current_authority_reuse.find(item => item.id === "scac.reuse.artifact_precedent").authority_limit = "hash is trust"; }, /cannot be promoted to trust/);
  reject(value => { value.canonical_source_catalog.find(item => item.id === "scac.artifact.registry").implementation_state = "current"; }, /future source ownership changed/);
  reject(value => { value.delivery_state.enforcement = "active"; }, /delivery state exceeds/);
  reject(value => { value.protocol_invariants = ["caller_labels_can_authorize"]; }, /reviewed immutable v1 contract/);
  reject(value => { value.current_authority_reuse.push({ id: "scac.reuse.secret_store", physical_sources: ["ops.credentials"], reuse_mode: "reference_only_no_copy", authority_limit: "may authorize" }); }, /reviewed immutable v1 contract/);
  reject(value => { const item = value.taxonomy.mutation_kinds.find(entry => entry.id === "scac.mutation.admin"); item.owner_package = "23"; item.canonical_source = "scac.health.evidence"; }, /reviewed immutable v1 contract/);
  reject(value => { value.taxonomy.refusal_reasons.find(item => item.id === "scac.refusal.kill_switch").precedence = 1; }, /reviewed immutable v1 contract/);
  reject(value => { value.taxonomy.refusal_reasons.find(item => item.id === "scac.refusal.kill_switch").safe_projection.push("api_key"); }, /reviewed immutable v1 contract/);
});

test("SIEP-10 adds no migration, grant, runtime ingress, or Production activation", () => {
  const migrations = fs.readdirSync(new URL("../../migrations/", import.meta.url));
  assert.equal(migrations.some(name => /siep[-_]?10|scac[-_]?charter/i.test(name)), false);
  const checker = fs.readFileSync(new URL("../../ops/scac-charter-check.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(checker, /\b(?:create table|grant\s|revoke\s|insert into|update\s+ops\.|delete from)\b/i);
  assert.doesNotMatch(checker, /mcp-server\/src\/tools|doctrine\.js|rule-delivery-cutover/);
});
