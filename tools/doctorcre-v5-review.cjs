// Deterministic review checks for the frozen DoctorCRE v5 design packet.
// These checks prove structure and exact artifact identity, never human
// acceptance, deployment, runtime capability, or authority to start a build.
// The database remains the design authority; this module contains validation
// code and trusted schema/digest constants, not a second editable plan.
const crypto = require("node:crypto");
const TRUSTED_SHAPE = {
  "unknown_fields_policy": "deny",
  "source_evidence_origin_fields": [
    "path",
    "canonical_minified_sha256",
    "preservation_rule",
    "parser_noise_corrections",
    "normalized_parent_path",
    "normalized_parent_file_sha256",
    "row_digest_rule"
  ],
  "canonicalization_contract_fields": [
    "standard",
    "encoding",
    "unicode",
    "object_key_order",
    "number_serialization",
    "trailing_newline",
    "digest_algorithm",
    "source_row_digest_scope",
    "document_digest_scope",
    "receipt_payload_digest_rule"
  ],
  "decision_contract_fields": [
    "operative_collection",
    "source_coverage",
    "multi_phase_rule",
    "dispositions",
    "phases",
    "source_binding_rule",
    "resolution_rule",
    "tabled_rule",
    "ownership_rule",
    "consumer_gate_rule",
    "successor_activation_rule",
    "oracle_rule",
    "predecessor_rule",
    "phase_evaluation_rule"
  ],
  "closed_schema_contract_fields": [
    "unknown_fields_policy",
    "source_evidence_origin_fields",
    "canonicalization_contract_fields",
    "decision_contract_fields",
    "closed_schema_contract_fields",
    "required_top_level_fields",
    "allowed_top_level_fields",
    "requirement_fields",
    "decision_required_fields",
    "decision_optional_fields",
    "authority_source_ref_fields",
    "predecessor_edge_fields",
    "target_registry_fields",
    "consumer_gate_registry_fields",
    "consumer_gate_receipt_schema_fields",
    "authenticated_receipt_identity_schema_fields",
    "rollout_component_receipt_schema_fields",
    "receipt_producer_step_registry_fields",
    "rollout_readiness_join_contract_fields",
    "rollout_required_receipt_fields",
    "oracle_type_registry_fields",
    "evidence_type_registry_fields",
    "conflict_registry_fields",
    "validation_invariant_fields",
    "benchmark_manifest_schema_fields",
    "attended_effect_capability_schema_fields",
    "attended_effect_consumption_receipt_schema_fields",
    "attended_effect_outcome_receipt_schema_fields"
  ],
  "required_top_level_fields": [
    "schema_version",
    "authority_status",
    "thread_id",
    "source_evidence_origin",
    "canonicalization_contract",
    "decision_contract",
    "target_registry",
    "consumer_gate_registry",
    "consumer_gate_receipt_schema",
    "authenticated_receipt_identity_schema",
    "subject_environment_registry",
    "evidence_scope_registry",
    "producer_role_registry",
    "causal_phase_registry",
    "target_dag_registry",
    "rollout_component_receipt_schema",
    "receipt_producer_step_registry",
    "rollout_readiness_join_contract",
    "oracle_type_registry",
    "evidence_type_registry",
    "predecessor_relation_registry",
    "conflict_registry",
    "closed_schema_contract",
    "validation_invariants",
    "requirements",
    "decisions",
    "benchmark_manifest_schema",
    "attended_effect_capability_schema",
    "attended_effect_consumption_receipt_schema",
    "attended_effect_outcome_receipt_schema"
  ],
  "allowed_top_level_fields": [
    "schema_version",
    "authority_status",
    "thread_id",
    "source_evidence_origin",
    "canonicalization_contract",
    "decision_contract",
    "target_registry",
    "consumer_gate_registry",
    "consumer_gate_receipt_schema",
    "authenticated_receipt_identity_schema",
    "subject_environment_registry",
    "evidence_scope_registry",
    "producer_role_registry",
    "causal_phase_registry",
    "target_dag_registry",
    "rollout_component_receipt_schema",
    "receipt_producer_step_registry",
    "rollout_readiness_join_contract",
    "oracle_type_registry",
    "evidence_type_registry",
    "predecessor_relation_registry",
    "conflict_registry",
    "closed_schema_contract",
    "validation_invariants",
    "requirements",
    "decisions",
    "benchmark_manifest_schema",
    "attended_effect_capability_schema",
    "attended_effect_consumption_receipt_schema",
    "attended_effect_outcome_receipt_schema"
  ],
  "requirement_fields": [
    "id",
    "question",
    "recommendation",
    "source_turn",
    "source_item",
    "user_response",
    "user_source_turn",
    "user_source_item",
    "target",
    "acceptance_hook"
  ],
  "decision_required_fields": [
    "decision_id",
    "source_question_id",
    "source_evidence_digest",
    "authority_source_refs",
    "conflict_refs",
    "resolution",
    "settled_requirement",
    "disposition",
    "phase",
    "target",
    "acceptance_hook",
    "authoritative_owner",
    "consumer_gates",
    "acceptance_predicate",
    "oracle_ref",
    "oracle_version",
    "oracle_type",
    "evidence_type",
    "predecessor_edges"
  ],
  "decision_optional_fields": [
    "future_decision_gate",
    "future_activation_predicate"
  ],
  "authority_source_ref_fields": [
    "role",
    "thread_ref",
    "item_ref"
  ],
  "predecessor_edge_fields": [
    "decision_id",
    "relation"
  ],
  "target_registry_fields": [
    "target",
    "acceptance_hook",
    "authoritative_owner",
    "acceptance_gate_id",
    "portfolio_step_ref"
  ],
  "consumer_gate_registry_fields": [
    "gate_id",
    "obligation_decision_ids",
    "receipt_producer_step_refs",
    "combiner",
    "receipt_schema_ref",
    "required_by_targets",
    "required_by_decision_ids",
    "activation_effect"
  ],
  "consumer_gate_receipt_schema_fields": [
    "schema_version",
    "additional_properties",
    "required_fields",
    "field_schemas",
    "status_enum",
    "negative_admission_result_enum",
    "pass_rule",
    "ttl_rule",
    "identity_rule",
    "combiner_rule",
    "denial_rule"
  ],
  "authenticated_receipt_identity_schema_fields": [
    "schema_version",
    "additional_properties",
    "required_fields",
    "field_schemas"
  ],
  "rollout_component_receipt_schema_fields": [
    "schema_version",
    "additional_properties",
    "required_fields",
    "field_schemas",
    "digest_fields",
    "status_enum",
    "negative_admission_result_enum",
    "identity_rule",
    "ttl_rule",
    "pass_rule",
    "denial_rule"
  ],
  "receipt_producer_step_registry_fields": [
    "step_ref",
    "produces_gate_ids",
    "produces_receipt_refs",
    "depends_on_step_refs",
    "consumes_gate_ids",
    "producer_role",
    "oracle_ref",
    "oracle_version",
    "evidence_scope",
    "subject_environment",
    "output_schema_ref",
    "target_dag",
    "causal_phase"
  ],
  "rollout_readiness_join_contract_fields": [
    "schema_version",
    "gate_id",
    "producer_step_ref",
    "receipt_schema_ref",
    "combiner",
    "required_receipts",
    "subject_binding_rule",
    "denial_rule"
  ],
  "rollout_required_receipt_fields": [
    "receipt_ref",
    "producer_step_ref",
    "evidence_scope",
    "subject_environment"
  ],
  "oracle_type_registry_fields": [
    "oracle_type",
    "description"
  ],
  "evidence_type_registry_fields": [
    "evidence_type",
    "description"
  ],
  "conflict_registry_fields": [
    "conflict_id",
    "source_question_id",
    "description",
    "governing_resolution"
  ],
  "validation_invariant_fields": [
    "invariant_id",
    "requirement",
    "negative_fixture"
  ],
  "benchmark_manifest_schema_fields": [
    "schema_version",
    "additional_properties",
    "required_fields",
    "field_schemas",
    "pass_rule",
    "denial_rule"
  ],
  "attended_effect_capability_schema_fields": [
    "schema_version",
    "additional_properties",
    "required_fields",
    "field_schemas",
    "pass_rule",
    "denial_rule"
  ],
  "attended_effect_consumption_receipt_schema_fields": [
    "schema_version",
    "additional_properties",
    "required_fields",
    "field_schemas",
    "pass_rule",
    "denial_rule"
  ],
  "attended_effect_outcome_receipt_schema_fields": [
    "schema_version",
    "additional_properties",
    "required_fields",
    "field_schemas",
    "pass_rule",
    "denial_rule"
  ]
};
// Re-frozen 2026-09-13 by the consumer-gate-receipt digest amendment, act 5 of
// accepted plan PLAN-a5059eb52474-v3 (Joe's acceptance of that plan, 2026-09-13,
// WR-000075 ready v4, IS the approval; paragraph 12 names the amendment so that
// accepting the plan accepts it knowingly).
//
// WHAT THE AMENDMENT CHANGED, and it is one string. r7's
// `canonicalization_contract.receipt_payload_digest_rule` named domain tags for
// four payload schemas and NOT for `consumer-gate-receipt.v1`, which left two
// readings of a Gate Zero outcome digest producing different values. The rule
// now declares that schema's payload digest as THE TAGGED PREIMAGE: sha256 over
// the RFC8785/JCS serialization of the two-element array whose first element is
// the domain-tag string `consumer-gate-receipt.v1` and whose second is the
// twenty-one-field receipt. A plain digest over the receipt object alone no
// longer satisfies the rule. No field was added or removed, so
// `canonicalization_contract_fields` above is unchanged.
//
// The constitution moved because it had to: `master_plan.architecture[0]` (P00)
// cites the design's JCS digest and validateConstitutionRelations asserts it, so
// re-rendering the design re-renders the constitution. Its bytes stayed 55,582.
//
// `requirements` is unchanged — the amendment touches no source row, and the
// 157 rows / 191 decisions counts do not move.
//
// Previously re-frozen 2026-09-12 by the card-10 Gate Zero amendment (decision
// 311a9af5-3685-4c47-a158-f8dd70870ca1, Joe's ruling on loop 589), which admitted
// `gate-zero-read-only-accepted`, the `step:gate-zero-read-only-outcome` producer
// row and its `independent_control_plane_oracle` role. That pair was
// design 10df2a65…/ea40f61a… and constitution 63114d72…/8a36fcba….
//
// THE SEMANTIC REVIEW IS STILL OWED, and is now two amendments stale: the
// recorded PASS, 0 P0 / 0 P1 was bound to the pre-loop-589 bytes.
const FROZEN = Object.freeze({
  "design": "e0380dfd48fce1cb491e51f78006ba5be08a56048925009d74948d3492778c12",
  "constitution": "eb0785d4fb7f7c0c6b6048085f8c0b23b9d081a3eb94c91d815b79783b47bfc8",
  "requirements": "51e0a89442fda95e537738e8321f9301af8ef48cf79d5ddb979c63421e8990ec"
});
const DEADLINE = {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "timezone",
    "calendar_days",
    "clock_origin_gate_id",
    "clock_origin_rule",
    "maximum_external_blocker_pause_days",
    "reset_policy",
    "amendment_policy",
    "clock_terminus_gate_id",
    "kernel_obligation_decision_ids",
    "miss_consequence"
  ],
  "properties": {
    "timezone": {
      "const": "America/Chicago"
    },
    "calendar_days": {
      "const": 30
    },
    "clock_origin_gate_id": {
      "const": "foundation-assurance-minimum-accepted"
    },
    "clock_origin_rule": {
      "const": "observed_at of the first current passing foundation-assurance-minimum receipt that makes Journey 1 admissible"
    },
    "maximum_external_blocker_pause_days": {
      "const": 5
    },
    "reset_policy": {
      "const": "never_reset_or_rebase_elapsed_history"
    },
    "amendment_policy": {
      "const": "verified_partner_exact_hash_amendment_preserves_original_origin_and_elapsed_history"
    },
    "clock_terminus_gate_id": {
      "const": "journey-one-kernel-production-accepted"
    },
    "kernel_obligation_decision_ids": {
      "type": "array",
      "const": [
        "Q002.D1",
        "Q014.D1",
        "Q123.D1"
      ]
    },
    "miss_consequence": {
      "const": "mark_deadline_missed_require_replan_preserve_origin_and_elapsed_continue_safe_construction_without_claiming_deadline_success"
    }
  }
};
const SOURCE_IDS = ["Q001.D1","Q002.D1","Q003.D1","Q004.D1","Q005.D1","Q005.D3","Q005.D2","Q006.D1","Q007.D1","Q008.D1","Q008.D2","Q009.D1","Q010.D1","Q011.D1","Q012.D1","Q012.D2","Q013.D1","Q014.D1","Q014.D2","Q015.D1","Q015.D2","Q016.D1","Q017.D1","Q018.D1","Q019.D1","Q020.D1","Q021.D1","Q021.D2","Q022.D1","Q023.D1","Q024.D1","Q025.D1","Q026.D1","Q027.D1","Q028.D1","Q029.D1","Q030.D1","Q031.D1","Q032.D1","Q033.D1","Q033.D2","Q034.D1","Q035.D1","Q036.D1","Q037.D1","Q038.D1","Q039.D1","Q040.D1","Q041.D1","Q042.D1","Q043.D1","Q044.D1","Q045.D1","Q046.D1","Q046.D2","Q047.D1","Q048.D1","Q049.D1","Q050.D1","Q051.D1","Q052.D1","Q053.D1","Q054.D1","Q055.D1","Q056.D1","Q057.D1","Q058.D1","Q059.D1","Q059.D2","Q059.D3","Q059.D4","Q059.D5","Q059.D6","Q060.D1","Q061.D1","Q062.D1","Q063.D1","Q064.D1","Q065.D1","Q066.D1","Q067.D1","Q068.D1","Q069.D1","Q070.D1","Q070.D2","Q071.D1","Q071.D2","Q072.D1","Q072.D2","Q073.D1","Q073.D2","Q074.D1","Q074.D2","Q075.D1","Q076.D1","Q077.D1","Q078.D1","Q079.D1","Q080.D1","Q080.D2","Q081.D1","Q082.D1","Q083.D1","Q084.D1","Q084.D2","Q085.D1","Q085.D2","Q086.D1","Q087.D1","Q088.D1","Q089.D1","Q090.D1","Q091.D1","Q092.D1","Q093.D1","Q094.D1","Q095.D1","Q096.D1","Q097.D1","Q098.D1","Q099.D1","Q100.D1","Q100.D2","Q101.D1","Q101.D2","Q102.D1","Q102.D2","Q103.D1","Q104.D1","Q105.D1","Q105.D2","Q106.D1","Q107.D1","Q108.D1","Q109.D1","Q110.D1","Q111.D1","Q112.D1","Q113.D1","Q114.D1","Q115.D1","Q116.D1","Q117.D1","Q118.D1","Q119.D1","Q120.D1","Q121.D1","Q122.D1","Q123.D1","Q123.D2","Q123.D3","Q123.D4","Q123.D5","Q124.D1","Q124.D2","Q125.D1","Q126.D1","Q127.D1","Q128.D1","Q129.D1","Q130.D1","Q131.D1","Q132.D1","Q132.D2","Q133.D1","Q134.D1","Q134.D2","Q135.D1","Q136.D1","Q137.D1","Q138.D1","Q139.D1","Q140.D1","Q141.D1","Q142.D1","Q143.D1","Q144.D1","Q145.D1","Q146.D1","Q147.D1","Q148.D1","Q148.D2","Q149.D1","Q150.D1","Q151.D1","Q152.D1","Q153.D1","Q154.D1","Q155.D1","Q156.D1","Q157.D1"];
function canonicalJson(value) {
  if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
  if (value && typeof value === "object") return "{" + Object.keys(value).sort()
    .map(key => JSON.stringify(key) + ":" + canonicalJson(value[key])).join(",") + "}";
  return JSON.stringify(value);
}
const digest = value => crypto.createHash("sha256").update(canonicalJson(value)).digest("hex");
const exactKeys = (value, expected) => value && typeof value === "object" && !Array.isArray(value)
  && canonicalJson(Object.keys(value).sort()) === canonicalJson([...expected].sort());
const sameValue = (a, b) => canonicalJson(a) === canonicalJson(b);

function decisionBindingErrors(decisions, requirements) {
  const errors = [];
  if (!Array.isArray(decisions) || !Array.isArray(requirements)) return ["decision_or_source_array_missing"];
  const sources = new Map(requirements.map(row => [row.id, row]));
  const seen = new Set(), covered = new Set();
  for (const row of decisions) {
    if (!row || typeof row !== "object" || Array.isArray(row)) { errors.push("malformed_decision"); continue; }
    const id = row.decision_id;
    if (seen.has(id) || !/^Q[0-9]{3}\.D[1-9][0-9]*$/.test(id)) errors.push("decision_identity:" + id);
    seen.add(id); covered.add(row.source_question_id);
    const fields = [...TRUSTED_SHAPE.decision_required_fields,
      ...TRUSTED_SHAPE.decision_optional_fields.filter(key => Object.hasOwn(row, key))];
    if (!exactKeys(row, fields)) errors.push("decision_shape:" + id);
    if (!["accepted", "modified", "rejected", "tabled"].includes(row.disposition)) errors.push("decision_disposition:" + id);
    if (!["v5", "successor", "non_goal"].includes(row.phase)) errors.push("decision_phase:" + id);
    for (const field of ["resolution", "settled_requirement", "acceptance_predicate", "oracle_ref",
      "oracle_version", "target", "acceptance_hook", "authoritative_owner"]) {
      if (typeof row[field] !== "string" || !row[field].trim()) errors.push("empty_decision_field:" + id + ":" + field);
    }
    if (row.phase === "successor" && (row.target !== "typed_successor"
      || typeof row.future_activation_predicate !== "string" || !row.future_activation_predicate.trim()))
      errors.push("successor_activation_contract:" + id);
    if (row.phase !== "successor" && Object.hasOwn(row, "future_activation_predicate"))
      errors.push("future_predicate_on_current_decision:" + id);
    const source = sources.get(row.source_question_id);
    if (!source) { errors.push("unknown_source:" + id); continue; }
    const refs = [
      { role: "assistant", thread_ref: source.source_turn, item_ref: source.source_item },
      { role: "user", thread_ref: source.user_source_turn, item_ref: source.user_source_item },
    ];
    if (!sameValue(row.authority_source_refs, refs)) errors.push("authority_reference:" + id);
    if (row.source_evidence_digest !== digest(source)) errors.push("source_digest:" + id);
  }
  for (const source of requirements) if (!covered.has(source.id)) errors.push("source_without_decision:" + source.id);
  return errors;
}
function productionOrderErrors(producers) {
  if (!Array.isArray(producers)) return ["producer_array_missing"];
  const errors = [];
  for (const ref of ["step:j1-kernel-production-outcome", "step:j1-core-production-outcome"]) {
    const row = producers.find(item => item?.step_ref === ref);
    if (!row || row.evidence_scope !== "production" || row.subject_environment !== "production")
      errors.push("j1_production_scope:" + ref);
  }
  const core = producers.find(item => item?.step_ref === "step:j1-core-production-outcome");
  if (!core?.consumes_gate_ids?.includes("journey-one-kernel-production-accepted"))
    errors.push("j1_core_requires_kernel");
  return errors;
}
function supplementalErrors(design) {
  const errors = [];
  if (!exactKeys(design, TRUSTED_SHAPE.required_top_level_fields)) return ["untrusted_top_level_shape"];
  if (!sameValue(design.closed_schema_contract, TRUSTED_SHAPE)) errors.push("candidate_changed_trusted_schema");
  if (digest(design.requirements) !== FROZEN.requirements) errors.push("preserved_source_rows_changed");
  errors.push(...decisionBindingErrors(design.decisions, design.requirements));
  if (!Array.isArray(design.decisions) || !sameValue(design.decisions.map(row => row?.decision_id).sort(), [...SOURCE_IDS].sort()))
    errors.push("frozen_decision_coverage_changed");
  if (!sameValue(design.benchmark_manifest_schema?.field_schemas?.deadline_contract, DEADLINE))
    errors.push("deadline_contract_changed");
  errors.push(...productionOrderErrors(design.receipt_producer_step_registry));
  const q20 = design.decisions?.find(row => row.decision_id === "Q020.D1");
  if (!q20?.predecessor_edges?.some(edge => edge.decision_id === "Q141.D1" && edge.relation === "depends_on"))
    errors.push("q020_reserved_authority_source_edge");
  return errors;
}
function frozenDigestErrors(design, constitution) {
  const errors = [];
  if (digest(design) !== FROZEN.design) errors.push("design_changed_requires_new_semantic_review");
  if (digest(constitution) !== FROZEN.constitution) errors.push("constitution_changed_requires_new_semantic_review");
  return errors;
}

function validateDesignRelations(design, portfolio) {
const errors = [];
const assert = (condition, message) => {
  if (!condition) errors.push(message);
};
const same = (left, right) =>
  left.length === right.length && left.every((value, index) => value === right[index]);
const unique = (values) => new Set(values).size === values.length;
const jcs = canonicalJson;
const sha256 = (value) => crypto.createHash("sha256").update(value).digest("hex");
const contract = TRUSTED_SHAPE;

assert(
  same(Object.keys(design).sort(), contract.required_top_level_fields.slice().sort()),
  "top-level fields mismatch",
);
for (const [key, fieldKey] of [
  ["source_evidence_origin", "source_evidence_origin_fields"],
  ["canonicalization_contract", "canonicalization_contract_fields"],
  ["decision_contract", "decision_contract_fields"],
  ["closed_schema_contract", "closed_schema_contract_fields"],
  ["consumer_gate_receipt_schema", "consumer_gate_receipt_schema_fields"],
  ["authenticated_receipt_identity_schema", "authenticated_receipt_identity_schema_fields"],
  ["benchmark_manifest_schema", "benchmark_manifest_schema_fields"],
  ["attended_effect_capability_schema", "attended_effect_capability_schema_fields"],
  ["attended_effect_consumption_receipt_schema", "attended_effect_consumption_receipt_schema_fields"],
  ["attended_effect_outcome_receipt_schema", "attended_effect_outcome_receipt_schema_fields"],
  ["rollout_component_receipt_schema", "rollout_component_receipt_schema_fields"],
  ["rollout_readiness_join_contract", "rollout_readiness_join_contract_fields"],
]) {
  assert(
    same(Object.keys(design[key]).sort(), contract[fieldKey].slice().sort()),
    `${key} fields mismatch`,
  );
}
for (const receipt of design.rollout_readiness_join_contract.required_receipts) {
  assert(
    same(Object.keys(receipt).sort(), contract.rollout_required_receipt_fields.slice().sort()),
    `rollout required receipt fields ${receipt.receipt_ref}`,
  );
}

const requirementIds = design.requirements.map((item) => item.id);
assert(design.requirements.length === 157, `requirements count ${design.requirements.length}`);
assert(unique(requirementIds), "duplicate requirement ids");
for (let index = 1; index <= 157; index += 1) {
  assert(requirementIds.includes(`Q${String(index).padStart(3, "0")}`), `missing Q${index}`);
}
for (const requirement of design.requirements) {
  assert(
    same(Object.keys(requirement).sort(), contract.requirement_fields.slice().sort()),
    `${requirement.id} requirement fields mismatch`,
  );
}

const targets = new Map();
for (const target of design.target_registry) {
  assert(
    same(Object.keys(target).sort(), contract.target_registry_fields.slice().sort()),
    `target fields ${target.target}`,
  );
  assert(!targets.has(target.target), `duplicate target ${target.target}`);
  targets.set(target.target, target);
}

const gates = new Map();
const producerRefs = [];
for (const gate of design.consumer_gate_registry) {
  assert(
    same(Object.keys(gate).sort(), contract.consumer_gate_registry_fields.slice().sort()),
    `gate fields ${gate.gate_id}`,
  );
  assert(!gates.has(gate.gate_id), `duplicate gate ${gate.gate_id}`);
  gates.set(gate.gate_id, gate);
  assert(gate.obligation_decision_ids.length > 0, `empty obligations ${gate.gate_id}`);
  assert(gate.receipt_producer_step_refs.length > 0, `empty producers ${gate.gate_id}`);
  const gateSchemaCombiners = {
    [design.consumer_gate_receipt_schema.schema_version]: "all_current_independent_pass",
    [design.benchmark_manifest_schema.schema_version]: "exact_verified_partner_hash_acceptance",
    [design.attended_effect_capability_schema.schema_version]:
      "one_current_exact_action_capability_with_atomic_pre_dispatch_consumption",
    [design.rollout_component_receipt_schema.schema_version]: "all_current_exact_distinct_pass",
  };
  assert(gate.receipt_schema_ref in gateSchemaCombiners, `bad receipt schema ${gate.gate_id}`);
  assert(gate.combiner === gateSchemaCombiners[gate.receipt_schema_ref], `bad combiner ${gate.gate_id}`);
  for (const step of gate.receipt_producer_step_refs) {
    assert(/^step:/.test(step), `bad step ref ${step}`);
    producerRefs.push(step);
  }
  for (const target of gate.required_by_targets) {
    assert(targets.has(target), `gate ${gate.gate_id} missing target ${target}`);
  }
}
assert(unique(producerRefs), "receipt producer reused across gates");

assert(design.consumer_gate_receipt_schema.additional_properties === false, "gate receipt schema open");
assert(
  same(
    design.consumer_gate_receipt_schema.required_fields.slice().sort(),
    Object.keys(design.consumer_gate_receipt_schema.field_schemas).sort(),
  ),
  "gate receipt required fields not fully typed",
);
assert(
  design.consumer_gate_receipt_schema.required_fields.includes("fixture_set_digest"),
  "gate receipt missing fixture_set_digest",
);
assert(
  same(design.consumer_gate_receipt_schema.negative_admission_result_enum, ["all_required_denials_observed"]),
  "gate receipt negative result enum",
);
assert(design.rollout_component_receipt_schema.additional_properties === false, "rollout receipt schema open");
assert(
  same(
    design.rollout_component_receipt_schema.required_fields.slice().sort(),
    Object.keys(design.rollout_component_receipt_schema.field_schemas).sort(),
  ),
  "rollout receipt required fields not fully typed",
);
assert(
  same(design.rollout_component_receipt_schema.negative_admission_result_enum, ["all_required_denials_observed"]),
  "rollout receipt negative result enum",
);
for (const [name, schema] of [
  ["benchmark", design.benchmark_manifest_schema],
  ["attended capability", design.attended_effect_capability_schema],
  ["attended consumption", design.attended_effect_consumption_receipt_schema],
  ["attended outcome", design.attended_effect_outcome_receipt_schema],
]) {
  assert(schema.additional_properties === false, `${name} schema open`);
  assert(
    same(schema.required_fields.slice().sort(), Object.keys(schema.field_schemas).sort()),
    `${name} required fields not fully typed`,
  );
}

const producers = new Map();
const producedReceipts = new Map();
const producerOracles = [];
for (const [name, values] of [
  ["subject environment", design.subject_environment_registry],
  ["evidence scope", design.evidence_scope_registry],
  ["producer role", design.producer_role_registry],
  ["causal phase", design.causal_phase_registry],
  ["target DAG", design.target_dag_registry],
]) {
  assert(Array.isArray(values) && values.length > 0 && unique(values), `${name} registry invalid`);
}
for (const producer of design.receipt_producer_step_registry) {
  assert(
    same(Object.keys(producer).sort(), contract.receipt_producer_step_registry_fields.slice().sort()),
    `producer fields ${producer.step_ref}`,
  );
  assert(!producers.has(producer.step_ref), `duplicate producer ${producer.step_ref}`);
  assert(/^step:/.test(producer.step_ref), `bad producer step ${producer.step_ref}`);
  assert(/^oracle:/.test(producer.oracle_ref), `bad producer oracle ${producer.step_ref}`);
  assert(design.evidence_scope_registry.includes(producer.evidence_scope), `producer scope ${producer.step_ref}`);
  assert(
    design.subject_environment_registry.includes(producer.subject_environment),
    `producer subject environment ${producer.step_ref}`,
  );
  assert(design.producer_role_registry.includes(producer.producer_role), `producer role ${producer.step_ref}`);
  assert(design.causal_phase_registry.includes(producer.causal_phase), `producer phase ${producer.step_ref}`);
  assert(design.target_dag_registry.includes(producer.target_dag), `producer target DAG ${producer.step_ref}`);
  assert(!producerOracles.includes(producer.oracle_ref), `duplicate producer oracle ${producer.oracle_ref}`);
  producerOracles.push(producer.oracle_ref);
  assert(
    [
      design.consumer_gate_receipt_schema.schema_version,
      design.rollout_component_receipt_schema.schema_version,
      design.benchmark_manifest_schema.schema_version,
      design.attended_effect_capability_schema.schema_version,
    ].includes(
      producer.output_schema_ref,
    ),
    `producer output schema ${producer.step_ref}`,
  );
  producers.set(producer.step_ref, producer);
  for (const receiptRef of producer.produces_receipt_refs) {
    assert(!producedReceipts.has(receiptRef), `duplicate receipt output ${receiptRef}`);
    producedReceipts.set(receiptRef, producer.step_ref);
  }
}
for (const gate of design.consumer_gate_registry) {
  for (const stepRef of gate.receipt_producer_step_refs) {
    const producer = producers.get(stepRef);
    assert(producer, `gate producer unresolved ${gate.gate_id} ${stepRef}`);
    assert(producer?.produces_gate_ids.includes(gate.gate_id), `producer does not produce gate ${gate.gate_id}`);
    assert(!producer?.consumes_gate_ids.includes(gate.gate_id), `producer consumes own gate ${gate.gate_id}`);
    assert(producer?.output_schema_ref === gate.receipt_schema_ref, `gate/producer schema mismatch ${gate.gate_id}`);
  }
}
for (const producer of producers.values()) {
  for (const gateId of producer.produces_gate_ids) assert(gates.has(gateId), `producer unknown gate ${gateId}`);
  for (const gateId of producer.consumes_gate_ids) {
    const gate = gates.get(gateId);
    assert(gate, `producer consumes unknown gate ${gateId}`);
  }
}
const rolloutJoin = design.rollout_readiness_join_contract;
assert(rolloutJoin.gate_id === "rollout-readiness-accepted", "rollout join gate");
assert(rolloutJoin.producer_step_ref === "step:rollout-prerequisite-join-receipt", "rollout join producer");
assert(rolloutJoin.receipt_schema_ref === "rollout-component-receipt.v1", "rollout join schema");
assert(rolloutJoin.combiner === "all_current_exact_distinct_pass", "rollout join combiner");
const rolloutGate = gates.get("rollout-readiness-accepted");
assert(rolloutGate?.receipt_schema_ref === rolloutJoin.receipt_schema_ref, "rollout public schema mismatch");
assert(rolloutGate?.combiner === rolloutJoin.combiner, "rollout public combiner mismatch");
const rolloutJoinProducer = producers.get(rolloutJoin.producer_step_ref);
assert(rolloutJoinProducer?.output_schema_ref === rolloutJoin.receipt_schema_ref, "rollout producer schema mismatch");
assert(rolloutJoin.required_receipts.length === 11, `rollout join count ${rolloutJoin.required_receipts.length}`);
assert(unique(rolloutJoin.required_receipts.map((item) => item.receipt_ref)), "rollout receipt refs duplicate");
assert(unique(rolloutJoin.required_receipts.map((item) => item.producer_step_ref)), "rollout producer refs duplicate");
for (const member of rolloutJoin.required_receipts) {
  const producer = producers.get(member.producer_step_ref);
  assert(producer, `rollout producer missing ${member.producer_step_ref}`);
  assert(producer?.produces_receipt_refs.includes(member.receipt_ref), `rollout output mismatch ${member.receipt_ref}`);
  assert(producer?.evidence_scope === member.evidence_scope, `rollout scope mismatch ${member.receipt_ref}`);
  assert(
    producer?.subject_environment === member.subject_environment,
    `rollout subject environment mismatch ${member.receipt_ref}`,
  );
}
for (const token of [
  "subject", "candidate", "policy", "rollout-environment-manifest", "evidence_scope",
  "subject_environment", "producer",
]) {
  assert(rolloutJoin.subject_binding_rule.includes(token), `rollout binding rule missing ${token}`);
}
for (const token of [
  "missing", "extra", "duplicate", "stale", "mismatched", "substituted", "replayed",
  "non-passing", "quarantined",
]) {
  assert(rolloutJoin.denial_rule.includes(token), `rollout denial rule missing ${token}`);
}

const oracleTypes = new Set(design.oracle_type_registry.map((item) => item.oracle_type));
const evidenceTypes = new Set(design.evidence_type_registry.map((item) => item.evidence_type));
const relations = new Set(design.predecessor_relation_registry);
const conflicts = new Set(design.conflict_registry.map((item) => item.conflict_id));
for (const item of design.oracle_type_registry) {
  assert(
    same(Object.keys(item).sort(), contract.oracle_type_registry_fields.slice().sort()),
    "oracle registry fields mismatch",
  );
}
for (const item of design.evidence_type_registry) {
  assert(
    same(Object.keys(item).sort(), contract.evidence_type_registry_fields.slice().sort()),
    "evidence registry fields mismatch",
  );
}
for (const item of design.conflict_registry) {
  assert(
    same(Object.keys(item).sort(), contract.conflict_registry_fields.slice().sort()),
    "conflict registry fields mismatch",
  );
}
for (const invariant of design.validation_invariants) {
  assert(
    Object.keys(invariant).every((key) => contract.validation_invariant_fields.includes(key)) &&
      ["invariant_id", "requirement"].every((key) => key in invariant),
    `invariant fields ${invariant.invariant_id}`,
  );
}

const decisions = new Map();
const oracleRefs = [];
const allowedDecisionFields = new Set([
  ...contract.decision_required_fields,
  ...contract.decision_optional_fields,
]);
for (const decision of design.decisions) {
  assert(
    contract.decision_required_fields.every((key) => key in decision) &&
      Object.keys(decision).every((key) => allowedDecisionFields.has(key)),
    `decision fields ${decision.decision_id}`,
  );
  assert(!decisions.has(decision.decision_id), `duplicate decision ${decision.decision_id}`);
  decisions.set(decision.decision_id, decision);
  assert(requirementIds.includes(decision.source_question_id), `missing source ${decision.decision_id}`);
  const requirement = design.requirements.find((item) => item.id === decision.source_question_id);
  assert(
    sha256(jcs(requirement)) === decision.source_evidence_digest,
    `source digest ${decision.decision_id}`,
  );
  assert(
    Array.isArray(decision.authority_source_refs) && decision.authority_source_refs.length > 0,
    `authority refs ${decision.decision_id}`,
  );
  for (const authorityRef of decision.authority_source_refs) {
    assert(
      same(Object.keys(authorityRef).sort(), contract.authority_source_ref_fields.slice().sort()),
      `authority ref fields ${decision.decision_id}`,
    );
  }
  for (const conflictRef of decision.conflict_refs) {
    assert(conflicts.has(conflictRef), `conflict ref ${decision.decision_id} ${conflictRef}`);
  }
  const target = targets.get(decision.target);
  assert(
    target &&
      target.acceptance_hook === decision.acceptance_hook &&
      target.authoritative_owner === decision.authoritative_owner,
    `target tuple ${decision.decision_id}`,
  );
  assert(
    Array.isArray(decision.consumer_gates) && decision.consumer_gates.length > 0,
    `consumer gates ${decision.decision_id}`,
  );
  for (const gate of decision.consumer_gates) {
    assert(gates.has(gate), `unresolved gate ${decision.decision_id} ${gate}`);
  }
  assert(oracleTypes.has(decision.oracle_type), `oracle type ${decision.decision_id}`);
  assert(evidenceTypes.has(decision.evidence_type), `evidence type ${decision.decision_id}`);
  assert(!oracleRefs.includes(decision.oracle_ref), `duplicate oracle ${decision.oracle_ref}`);
  oracleRefs.push(decision.oracle_ref);
  assert(Array.isArray(decision.predecessor_edges), `predecessor array ${decision.decision_id}`);
  for (const edge of decision.predecessor_edges) {
    assert(
      same(Object.keys(edge).sort(), contract.predecessor_edge_fields.slice().sort()),
      `predecessor fields ${decision.decision_id}`,
    );
  }
  if (decision.disposition === "tabled") {
    assert(
      decision.future_decision_gate && decision.consumer_gates.includes(decision.future_decision_gate),
      `tabled gate ${decision.decision_id}`,
    );
  }
}
assert(design.decisions.length === 191, `decision count ${design.decisions.length}`);

for (const gate of design.consumer_gate_registry) {
  for (const decisionId of gate.obligation_decision_ids) {
    assert(decisions.has(decisionId), `gate obligation unresolved ${gate.gate_id} ${decisionId}`);
    assert(
      decisions.get(decisionId)?.consumer_gates.includes(gate.gate_id),
      `gate obligation not consumed ${gate.gate_id} ${decisionId}`,
    );
  }
  for (const decisionId of gate.required_by_decision_ids) {
    assert(decisions.has(decisionId), `gate required decision unresolved ${gate.gate_id} ${decisionId}`);
    assert(
      decisions.get(decisionId)?.consumer_gates.includes(gate.gate_id),
      `gate required-by not consumed ${gate.gate_id} ${decisionId}`,
    );
  }
}
for (const decision of design.decisions) {
  for (const gateId of decision.consumer_gates) {
    const gate = gates.get(gateId);
    assert(
      gate &&
        (gate.required_by_targets.includes(decision.target) ||
          gate.obligation_decision_ids.includes(decision.decision_id) ||
          gate.required_by_decision_ids.includes(decision.decision_id)),
      `decision gate not reciprocally declared ${decision.decision_id} ${gateId}`,
    );
  }
}
for (const decision of design.decisions) {
  for (const edge of decision.predecessor_edges) {
    assert(decisions.has(edge.decision_id), `predecessor unresolved ${decision.decision_id}`);
    assert(edge.decision_id !== decision.decision_id, `self predecessor ${decision.decision_id}`);
    assert(relations.has(edge.relation), `bad relation ${decision.decision_id}`);
  }
}

const visiting = new Set();
const visited = new Set();
function visit(decisionId) {
  if (visiting.has(decisionId)) {
    errors.push(`cycle at ${decisionId}`);
    return;
  }
  if (visited.has(decisionId)) return;
  visiting.add(decisionId);
  for (const edge of decisions.get(decisionId).predecessor_edges) visit(edge.decision_id);
  visiting.delete(decisionId);
  visited.add(decisionId);
}
for (const decisionId of decisions.keys()) visit(decisionId);

const portfolioSteps = new Map(portfolio.dependency_dag.map((step) => [step.step_ref, step]));
for (const target of targets.values()) {
  assert(gates.has(target.acceptance_gate_id), `target acceptance gate ${target.target}`);
  assert(
    gates.get(target.acceptance_gate_id)?.required_by_targets.includes(target.target),
    `target acceptance gate not reciprocal ${target.target}`,
  );
  assert(portfolioSteps.has(target.portfolio_step_ref), `target portfolio step ${target.target}`);
}

const combinedGraph = new Map();
const addGraphNode = (stepRef, dependencies) => {
  if (!combinedGraph.has(stepRef)) combinedGraph.set(stepRef, new Set());
  for (const dependency of dependencies) combinedGraph.get(stepRef).add(dependency);
};
for (const step of portfolio.dependency_dag) addGraphNode(step.step_ref, step.depends_on);
for (const producer of producers.values()) addGraphNode(producer.step_ref, producer.depends_on_step_refs);
for (const target of targets.values()) {
  for (const gateProducer of gates.get(target.acceptance_gate_id)?.receipt_producer_step_refs || []) {
    if (target.portfolio_step_ref !== gateProducer) addGraphNode(target.portfolio_step_ref, [gateProducer]);
  }
}
for (const [stepRef, dependencies] of combinedGraph.entries()) {
  for (const dependency of dependencies) {
    assert(combinedGraph.has(dependency), `combined dependency unresolved ${stepRef} -> ${dependency}`);
    assert(dependency !== stepRef, `combined self dependency ${stepRef}`);
  }
}
const combinedVisiting = new Set();
const combinedVisited = new Set();
function visitCombined(stepRef) {
  if (combinedVisiting.has(stepRef)) {
    errors.push(`combined graph cycle at ${stepRef}`);
    return;
  }
  if (combinedVisited.has(stepRef)) return;
  combinedVisiting.add(stepRef);
  for (const dependency of combinedGraph.get(stepRef) || []) visitCombined(dependency);
  combinedVisiting.delete(stepRef);
  combinedVisited.add(stepRef);
}
for (const stepRef of combinedGraph.keys()) visitCombined(stepRef);
function hasDependencyPath(stepRef, ancestorRef, seen = new Set()) {
  if (stepRef === ancestorRef) return true;
  if (seen.has(stepRef)) return false;
  seen.add(stepRef);
  for (const dependency of combinedGraph.get(stepRef) || []) {
    if (hasDependencyPath(dependency, ancestorRef, seen)) return true;
  }
  return false;
}
for (const producer of producers.values()) {
  for (const gateId of producer.consumes_gate_ids) {
    for (const requiredProducer of gates.get(gateId)?.receipt_producer_step_refs || []) {
      assert(
        hasDependencyPath(producer.step_ref, requiredProducer),
        `producer gate dependency not causal ${producer.step_ref} -> ${requiredProducer}`,
      );
    }
  }
}
// The steps a producer may depend on WITHOUT an r7 producer row of its own.
// Before the card-10 amendment this was Gate Zero itself, because nothing in
// r7 produced it. Gate Zero now has a row, so the external boundary moved down
// to its four predecessors: they are portfolio master steps — accepted
// work-request outcomes and a scheduler receipt — and r7 registers no producer
// for any of them. The membership assertion below is what stops this list
// becoming a hole: an entry that is not a portfolio master step is refused.
const externalPreV5ProducerSteps = new Set([
  "step:scheduler-active-receipt",
  "step:wr40-repository-outcome",
  "step:wr46-dissolution-outcome",
  "step:wr54-backup-recovery-outcome",
]);
for (const stepRef of externalPreV5ProducerSteps) {
  assert(portfolioSteps.has(stepRef), `external producer-free step is not a portfolio step ${stepRef}`);
  assert(!producers.has(stepRef), `external producer-free step now has a producer ${stepRef}`);
}
for (const producer of producers.values()) {
  for (const dependency of producer.depends_on_step_refs) {
    assert(
      producers.has(dependency) || externalPreV5ProducerSteps.has(dependency),
      `depended-on step has no producer ${producer.step_ref} -> ${dependency}`,
    );
  }
}
for (const target of [...targets.values()].filter((item) => item.target.startsWith("product_journey_"))) {
  assert(producers.has(target.portfolio_step_ref), `product journey has no outcome producer ${target.target}`);
}
assert(
  same(gates.get("portfolio-constitution-accepted").receipt_producer_step_refs, [
    "step:portfolio-constitution-human-exact-hash-acceptance-receipt",
  ]),
  "portfolio gate is not human exact-hash acceptance",
);
for (const stepRef of [
  "step:foundation-control-plane-preactivation-contract-receipt",
  "step:assurance-fabric-preactivation-contract-receipt",
  "step:global-no-phi-boundary-independent-receipt",
  "step:global-secrets-boundary-independent-receipt",
  "step:global-prompt-injection-boundary-independent-receipt",
  "step:global-execution-contract-independent-receipt",
  "step:global-source-authority-independent-receipt",
  "step:benchmark-contract-human-exact-hash-acceptance-receipt",
]) {
  assert(hasDependencyPath(stepRef, "step:gate-zero-read-only-outcome"), `missing Gate Zero ancestry ${stepRef}`);
  assert(
    hasDependencyPath(stepRef, "step:portfolio-constitution-human-exact-hash-acceptance-receipt"),
    `missing human acceptance ancestry ${stepRef}`,
  );
}
assert(
  hasDependencyPath("step:foundation-assurance-minimum-receipt", "step:foundation-control-plane-preactivation-contract-receipt") &&
    hasDependencyPath("step:foundation-assurance-minimum-receipt", "step:assurance-fabric-preactivation-contract-receipt") &&
    hasDependencyPath("step:foundation-assurance-minimum-receipt", "step:benchmark-contract-human-exact-hash-acceptance-receipt"),
  "minimum receipt lacks Foundation, Assurance, or benchmark ancestry",
);
const j1KernelGate = gates.get("journey-one-kernel-production-accepted");
const j1OutcomeGate = gates.get("journey-one-production-accepted");
assert(
  same(j1KernelGate?.obligation_decision_ids || [], ["Q002.D1", "Q014.D1", "Q123.D1"]),
  "J1 kernel obligation set",
);
assert(
  same(j1KernelGate?.receipt_producer_step_refs || [], ["step:j1-kernel-production-outcome"]),
  "J1 kernel producer",
);
assert(
  same(j1OutcomeGate?.receipt_producer_step_refs || [], ["step:j1-core-production-outcome"]),
  "J1 full outcome producer",
);
assert(
  hasDependencyPath("step:j1-core-production-outcome", "step:j1-kernel-production-outcome") &&
    hasDependencyPath("step:j1-kernel-production-outcome", "step:journey-one-contract-binding-receipt"),
  "J1 kernel/full causal chain",
);
for (const producer of producers.values()) {
  if (producer.depends_on_step_refs.includes("step:j1-core-production-outcome")) {
    assert(
      producer.consumes_gate_ids.includes("journey-one-production-accepted"),
      `downstream J1 dependency omits outcome gate ${producer.step_ref}`,
    );
  }
}
const deadline = design.benchmark_manifest_schema.field_schemas.deadline_contract;
assert(deadline.additionalProperties === false, "deadline contract open");
assert(
  same(deadline.required.slice().sort(), Object.keys(deadline.properties).sort()),
  "deadline fields not fully typed",
);
assert(deadline.properties.timezone?.const === "America/Chicago", "deadline timezone");
assert(deadline.properties.calendar_days?.const === 30, "deadline days");
assert(
  deadline.properties.clock_origin_gate_id?.const === "foundation-assurance-minimum-accepted",
  "deadline origin gate",
);
assert(
  deadline.properties.clock_terminus_gate_id?.const === "journey-one-kernel-production-accepted",
  "deadline terminus gate",
);
assert(
  same(deadline.properties.kernel_obligation_decision_ids?.const || [], ["Q002.D1", "Q014.D1", "Q123.D1"]),
  "deadline kernel obligations",
);
assert(deadline.properties.maximum_external_blocker_pause_days?.const === 5, "deadline pause cap");
assert(
  deadline.properties.miss_consequence?.const ===
    "mark_deadline_missed_require_replan_preserve_origin_and_elapsed_continue_safe_construction_without_claiming_deadline_success",
  "deadline miss consequence",
);
const capabilityRequired = [
  "gate_id", "receipt_producer_step_ref", "capability_id", "capability_digest",
  "authority_class", "approving_partner_identity", "issuer_identity", "exact_effect_digest",
  "connector_id", "account_id", "environment", "operation", "argument_digest", "record_refs",
  "recipients_digest", "mandatory_partner_copy_identity", "content_digest",
  "attachment_manifest_digest", "purpose", "call_cap", "cost_cap_currency_micros", "issued_at",
  "expires_at", "nonce", "idempotency_key", "policy_digest", "candidate_digest",
  "consumption_rule", "status",
];
assert(
  same(design.attended_effect_capability_schema.required_fields, capabilityRequired),
  "attended capability required fields drift",
);
assert(
  design.attended_effect_capability_schema.field_schemas.status?.const === "issued_unconsumed" &&
    design.attended_effect_capability_schema.field_schemas.consumption_rule?.const ===
      "atomic_once_immediately_before_provider_call",
  "attended capability immutable issuance constants",
);
const consumptionRequired = [
  "capability_id", "capability_digest", "exact_effect_digest", "gateway_identity",
  "consumed_at", "nonce", "idempotency_key", "consumption_status",
];
assert(
  same(design.attended_effect_consumption_receipt_schema.required_fields, consumptionRequired),
  "pre-dispatch consumption required fields",
);
for (const forbidden of [
  "provider_request_ref", "provider_readback_ref", "provider_readback_digest", "provider_status",
  "external_id", "reconciliation_status",
]) {
  assert(
    !design.attended_effect_consumption_receipt_schema.required_fields.includes(forbidden) &&
      !(forbidden in design.attended_effect_consumption_receipt_schema.field_schemas),
    `pre-dispatch consumption contains provider fact ${forbidden}`,
  );
}
assert(
  design.attended_effect_consumption_receipt_schema.field_schemas.consumption_status?.const ===
    "consumed_before_provider_dispatch",
  "pre-dispatch consumption status",
);
assert(
  design.attended_effect_outcome_receipt_schema.required_fields.includes(
    "pre_dispatch_consumption_receipt_digest",
  ),
  "attended outcome missing consumption digest",
);
const minimumProducer = producers.get("step:foundation-assurance-minimum-receipt");
assert(
  minimumProducer?.consumes_gate_ids.includes("benchmark-contract-accepted"),
  "minimum receipt does not consume benchmark gate",
);
for (const decisionId of ["Q008.D1", "Q128.D1", "Q143.D1"]) {
  assert(decisions.get(decisionId)?.consumer_gates.includes("benchmark-contract-accepted"), `benchmark gate ${decisionId}`);
}
for (const decision of design.decisions.filter((item) => item.target === "representative_workflow_extension")) {
  assert(
    decision.consumer_gates.includes("attended-external-effect-contract-accepted") &&
      decision.consumer_gates.includes("attended-external-effect-capability-valid"),
    `representative attended effect gates ${decision.decision_id}`,
  );
}
assert(
  decisions.get("Q072.D1")?.target === "product_journey_1" &&
    !decisions.get("Q072.D1")?.settled_requirement.includes("Tour") &&
    decisions.get("Q072.D2")?.target === "product_journey_3" &&
    decisions.get("Q072.D2")?.consumer_gates.includes("tour-map-contract-1.2.0-accepted"),
  "Q072 core/Tour split invalid",
);
const reservedAuthorityTokens = [
  "system design", "policy", "release", "security", "destructive-migration",
  "autonomy-tier activation", "Dell's developer and release-admin authority remains deferred",
  "ordinary business actions",
];
for (const decisionId of ["Q020.D1", "Q141.D1"]) {
  const authorityText = [
    decisions.get(decisionId)?.resolution,
    decisions.get(decisionId)?.settled_requirement,
    decisions.get(decisionId)?.acceptance_predicate,
  ].join(" ");
  for (const token of reservedAuthorityTokens) {
    assert(authorityText.includes(token), `${decisionId} authority token ${token}`);
  }
  assert(!/equal verified-partner authority/i.test(authorityText), `${decisionId} uncited equal authority`);
}
assert(
  decisions.get("Q080.D1")?.target === "product_journey_1" &&
    !/owns[^.]*Tours/i.test(decisions.get("Q080.D1")?.settled_requirement || "") &&
    decisions.get("Q080.D2")?.target === "product_journey_3" &&
    decisions.get("Q080.D2")?.consumer_gates.includes("tour-map-contract-1.2.0-accepted"),
  "Q080 J1/J3 Tour split invalid",
);
const j1InertLaterSurfaceAllowlist = new Set([
  "Q005.D1", "Q059.D2", "Q069.D1", "Q072.D1", "Q076.D1", "Q080.D1", "Q083.D1", "Q124.D1",
  "Q133.D1", "Q139.D1",
]);
for (const decision of design.decisions.filter((item) => item.target === "product_journey_1")) {
  const text = `${decision.settled_requirement} ${decision.acceptance_predicate}`;
  if (/Tour|\bmap\b|Salesforce write|external send/i.test(text)) {
    assert(
      j1InertLaterSurfaceAllowlist.has(decision.decision_id),
      `J1 active later-surface behavior ${decision.decision_id}`,
    );
  }
}
assert(
  hasDependencyPath("step:partner-mail-calendar-connectors-independent-receipt", "step:j1-core-production-outcome"),
  "partner connector lacks J1 ancestry",
);
assert(
  hasDependencyPath("step:remote-meeting-recording-policy-human-acceptance-receipt", "step:journey-two-production-outcome"),
  "recording policy lacks J2 outcome ancestry",
);
assert(
  hasDependencyPath("step:representative-workflow-production-outcome", "step:journey-three-production-outcome"),
  "representative workflow lacks J3 ancestry",
);

const dispositions = Object.fromEntries(
  [...new Set(design.decisions.map((item) => item.disposition))]
    .sort()
    .map((key) => [key, design.decisions.filter((item) => item.disposition === key).length]),
);
const phases = Object.fromEntries(
  [...new Set(design.decisions.map((item) => item.phase))]
    .sort()
    .map((key) => [key, design.decisions.filter((item) => item.phase === key).length]),
);
const targetCounts = Object.fromEntries(
  [...targets.keys()].map((key) => [key, design.decisions.filter((item) => item.target === key).length]),
);
return errors;
}
function validateConstitutionRelations(design, plan) {
const errors = [];
const assert = (condition, message) => {
  if (!condition) errors.push(message);
};
const exactKeys = (value, fields) => {
  const actual = Object.keys(value).sort();
  const expected = fields.slice().sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
};
const sha256 = (value) => crypto.createHash("sha256").update(value).digest("hex");
const statementFields = [
  "product_goal",
  "baseline_comparison",
  "release_strategy",
  "rollback_strategy",
  "observability_strategy",
  "fully_shipped_definition",
  "prerequisite_policy",
];
const planFields = [
  ...statementFields,
  "non_goals",
  "architecture",
  "authority_boundaries",
  "dependency_dag",
  "planned_checks",
];

assert(exactKeys(plan, planFields), "master_plan fields mismatch");
for (const field of statementFields) {
  assert(
    typeof plan[field] === "string" && plan[field].length >= 20 && plan[field].length <= 2000,
    `${field} length ${plan[field]?.length}`,
  );
}
for (const [field, minimum, maximum] of [
  ["non_goals", 1, 12],
  ["architecture", 2, 20],
  ["authority_boundaries", 1, 12],
]) {
  assert(Array.isArray(plan[field]), `${field} must be an array`);
  assert(plan[field].length >= minimum && plan[field].length <= maximum, `${field} count`);
  for (const [index, value] of plan[field].entries()) {
    assert(
      typeof value === "string" && value.length >= 10 && value.length <= 1000,
      `${field}[${index}] length ${value?.length}`,
    );
  }
}
assert(plan.architecture.length === 20, `architecture count ${plan.architecture.length}`);
for (let index = 0; index < plan.architecture.length; index += 1) {
  assert(plan.architecture[index].startsWith(`P${String(index).padStart(2, "0")} —`), `P${index} label`);
}

assert(
  Array.isArray(plan.dependency_dag) && plan.dependency_dag.length > 0 && plan.dependency_dag.length <= 21,
  "dependency_dag count",
);
const steps = new Map();
for (const step of plan.dependency_dag) {
  assert(exactKeys(step, ["step_ref", "depends_on"]), `step fields ${step.step_ref}`);
  assert(/^step:[a-z0-9][a-z0-9:._/-]*$/.test(step.step_ref), `step ref ${step.step_ref}`);
  assert(!steps.has(step.step_ref), `duplicate step ${step.step_ref}`);
  assert(Array.isArray(step.depends_on), `depends_on ${step.step_ref}`);
  assert(!step.depends_on.includes(step.step_ref), `self edge ${step.step_ref}`);
  steps.set(step.step_ref, step);
}
for (const step of plan.dependency_dag) {
  for (const dependency of step.depends_on) {
    assert(steps.has(dependency), `unresolved dependency ${step.step_ref} -> ${dependency}`);
  }
}
const visiting = new Set();
const visited = new Set();
function visit(stepRef) {
  if (visiting.has(stepRef)) {
    errors.push(`cycle at ${stepRef}`);
    return;
  }
  if (visited.has(stepRef)) return;
  visiting.add(stepRef);
  for (const dependency of steps.get(stepRef).depends_on) visit(dependency);
  visiting.delete(stepRef);
  visited.add(stepRef);
}
for (const stepRef of steps.keys()) visit(stepRef);

assert(Array.isArray(plan.planned_checks), "planned_checks must be an array");
assert(plan.planned_checks.length > 0 && plan.planned_checks.length <= 20, "planned_checks count");
for (const [index, check] of plan.planned_checks.entries()) {
  assert(
    exactKeys(check, ["artifact", "comparator", "failure_condition"]),
    `planned_checks[${index}] fields`,
  );
  for (const field of ["artifact", "comparator", "failure_condition"]) {
    assert(
      typeof check[field] === "string" && check[field].length >= 5 && check[field].length <= 500,
      `planned_checks[${index}].${field} length ${check[field]?.length}`,
    );
  }
}

const gateZero = steps.get("step:gate-zero-read-only-outcome");
assert(gateZero, "Gate Zero step missing");
const gateZeroExpected = [
  "step:wr46-dissolution-outcome",
  "step:wr40-repository-outcome",
  "step:wr54-backup-recovery-outcome",
  "step:scheduler-active-receipt",
].sort();
assert(
  gateZero && JSON.stringify(gateZero.depends_on.slice().sort()) === JSON.stringify(gateZeroExpected),
  "Gate Zero does not directly bind the four required predecessors",
);
assert(!steps.has("step:foundation-prerequisite-join"), "forbidden synthetic Foundation join remains");
const expectedStepRefs = [
  "step:portfolio-constitution-accepted",
  "step:wr48-frontier-flowing-production-outcome",
  "step:wr46-dissolution-outcome",
  "step:wr40-repository-outcome",
  "step:wr54-backup-recovery-outcome",
  "step:scheduler-active-receipt",
  "step:gate-zero-read-only-outcome",
  "step:foundation-assurance-minimum-receipt",
  "step:j1-kernel-production-outcome",
  "step:j1-core-production-outcome",
  "step:j1-pilot-and-dell-beta-outcome",
  "step:representative-workflow-production-outcome",
  "step:journey-two-production-outcome",
  "step:journey-three-production-outcome",
  "step:foundation-control-plane-child-outcome",
  "step:assurance-fabric-child-outcome",
  "step:successor-controls-outcome",
  "step:rollout-readiness-child-outcome",
  "step:final-pre-retirement-integration-adjudication",
  "step:legacy-retirement-receipt",
  "step:final-portfolio-outcome-reconciliation",
];
assert(
  JSON.stringify([...steps.keys()].sort()) === JSON.stringify(expectedStepRefs.slice().sort()),
  "master step set mismatch",
);
for (const stepRef of expectedStepRefs) {
  assert(steps.has(stepRef), `missing distinct rollout step ${stepRef}`);
}
assert(
  JSON.stringify(steps.get("step:j1-kernel-production-outcome").depends_on) ===
    JSON.stringify(["step:foundation-assurance-minimum-receipt"]),
  "J1 kernel must directly follow the minimum",
);
assert(
  JSON.stringify(steps.get("step:j1-core-production-outcome").depends_on) ===
    JSON.stringify(["step:j1-kernel-production-outcome"]),
  "complete J1 must directly follow the J1 kernel",
);
assert(
  JSON.stringify(steps.get("step:journey-three-production-outcome").depends_on) ===
    JSON.stringify(["step:journey-two-production-outcome"]),
  "Journey 3 must directly follow Journey 2",
);
assert(
  JSON.stringify(steps.get("step:representative-workflow-production-outcome").depends_on) ===
    JSON.stringify(["step:journey-three-production-outcome"]),
  "representative workflow must directly follow Journey 3",
);
const rolloutExpected = [
  "step:j1-pilot-and-dell-beta-outcome",
  "step:representative-workflow-production-outcome",
  "step:journey-two-production-outcome",
  "step:journey-three-production-outcome",
  "step:foundation-control-plane-child-outcome",
  "step:assurance-fabric-child-outcome",
  "step:successor-controls-outcome",
].sort();
assert(
  JSON.stringify(steps.get("step:rollout-readiness-child-outcome").depends_on.slice().sort()) ===
    JSON.stringify(rolloutExpected),
  "rollout master dependencies mismatch",
);
assert(
  plan.architecture[0].includes(`sha256:${sha256(canonicalJson(design))}`),
  "P00 design-basis digest mismatch",
);
for (const token of [
  "Joe retains v5 system design", "Dell's developer and release-admin authority remains deferred",
  "ordinary business actions", "destructive-migration", "autonomy-tier activation",
]) {
  assert(plan.authority_boundaries[0].includes(token), `authority boundary missing ${token}`);
}
assert(!/equal verified-partner authority/i.test(plan.authority_boundaries[0]), "uncited equal authority remains");
for (const token of [
  "journey-one-kernel-production-accepted", "Q002.D1", "Q014.D1", "Q123.D1",
  "miss", "replan", "continued safe construction",
]) {
  assert(plan.architecture[14].includes(token), `P14 deadline token ${token}`);
}

return errors;
}

function validateReviewPacket(packet) {
  try {
    const { design, constitution } = packet || {};
    const errors = supplementalErrors(design);
    // Malformed shape must return a denial, not crash legacy relational checks.
    if (!errors.length) {
      errors.push(...validateDesignRelations(design, constitution.master_plan));
      errors.push(...validateConstitutionRelations(design, constitution.master_plan));
    }
    errors.push(...frozenDigestErrors(design, constitution));
    return { ok: errors.length === 0, errors, design_digest: digest(design),
      constitution_digest: digest(constitution),
      result_scope: "frozen review artifact integrity; no acceptance or execution authority" };
  } catch (error) {
    return { ok: false, errors: ["malformed_review_packet:" + error.message],
      result_scope: "frozen review artifact integrity; no acceptance or execution authority" };
  }
}
module.exports = { canonicalJson, digest, decisionBindingErrors, productionOrderErrors,
  supplementalErrors, frozenDigestErrors, validateReviewPacket };
if (require.main === module) {
  let result;
  try { result = validateReviewPacket(JSON.parse(require("node:fs").readFileSync(0, "utf8"))); }
  catch (error) { result = { ok: false, errors: ["invalid_json:" + error.message] }; }
  console.log(JSON.stringify(result, null, 2));
  process.exitCode = result.ok ? 0 : 1;
}
