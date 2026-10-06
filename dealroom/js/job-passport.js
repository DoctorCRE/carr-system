// Job Passport wire reader — a deterministic, read-only projection consumer.
//
// The Model Room owns no task state. It reads typed, redacted receipts already
// on the partner wire, keeps the newest complete projection for each durable
// Work Request, and makes uncertainty visible instead of filling gaps with
// transcript guesses. The controller, not this browser, validates/promotes
// canonical state before it is wrapped for the wire.

const WIRE_VERSION = "job-passport-wire.v1";
const KINDS = new Set(["execution_envelope", "progress_event", "attempt_receipt", "activation_reliability_projection", "observatory_projection", "evaluation_kernel", "eval_portfolio", "spatial_surface", "telemetry_measurement", "engineering_passport"]);
const PROGRESS = new Set(["active", "quiet", "stale", "blocked", "failed", "unknown", "verified_complete"]);
const LIFECYCLE = new Set(["succeeded", "failed", "timed_out", "cancelled", "partial", "unknown"]);
const VERIFICATION = new Set(["verified_success", "verified_failure", "partial", "unknown", "not_attempted"]);
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const DISPOSITIONS = new Set(["applied", "not_applicable", "conflicted", "stale", "missing"]);
const ENVELOPE_CORE_FIELDS = ["schema_version", "envelope_id", "work_request_id", "plan_revision", "agent_session", "issued_at", "expires_at", "request", "server_binding", "handoff", "state_binding", "phase_binding", "evaluation_context"];
const ENVELOPE_ACTIVATION_FIELDS = ["activation_binding", "reliability_policy_binding", "context_activation_ref", "runtime_profile", "execution_topology", "evaluation_plan"];

// engineering-slice-plan.v2 mirror. v1 keeps its exact accepted shape; v2 adds
// the Q046.D1 design_contract per slice and the plan-level seam/parallel-resource
// checks below. This mirrors mcp-server/src/engineering-runtime.js requirePlan /
// requireDesignContract for engineering-slice-plan.v2 exactly; v1 is untouched.
const SLICE_PLAN_VERSIONS = new Set(["engineering-slice-plan.v1", "engineering-slice-plan.v2"]);
const SLICE_PLAN_V2 = "engineering-slice-plan.v2";
const DESIGN_CONTRACT_VERSION = "engineering-design-contract.v1";
const SHORT_RISK_CLASSES = new Set(["R0", "R1", "R2", "R3"]);
const DESIGN_CONTRACT_FIELDS = ["authority", "code_model_decision", "completion", "contract_version", "dependency_rationale", "deployment", "evidence", "failure", "full_design_refs", "isolation", "rationale", "review", "routing", "seam_decision", "short_template", "tests"];
const MODEL_STEP_FIELDS = ["input_contract_ref", "output_contract_ref", "rationale", "responsibility_class", "selection_basis", "step_ref"];
const FULL_DESIGN_REF_FIELDS = ["authority_envelope_ref", "design_interview_ref", "failure_model_ref", "fixture_refs", "oracle_ref"];
const SHORT_TEMPLATE_FIELDS = ["objective_summary", "template_ref", "verification_ref"];
const RESERVED_CODE_RESPONSIBILITIES = new Set(["identity", "policy", "permissions", "state", "validation", "idempotency", "execution"]);
const TYPED_UNCERTAINTY_CLASSES = new Set(["classification", "extraction", "summarization", "ranking", "drafting", "disambiguation"]);
const SELECTION_BASIS_VALUES = new Set(["typed_uncertainty", "capability_gain", "quality_gain", "adaptability_gain", "cost"]);
const EXECUTOR_CLASSES = new Set(["deterministic_code", "attended_human", "model_assisted"]);
const AUTHORITY_ENVIRONMENTS = new Set(["local", "rehearsal", "staging", "production"]);
const VERIFICATION_LANES = new Set(["unit", "contract", "integration", "manual_qa"]);
const REVIEWER_CLASSES = new Set(["independent_agent"]);
const EVIDENCE_REDACTION_CLASSES = new Set(["metadata_only", "redacted_evidence"]);
const EVIDENCE_RETENTIONS = new Set(["ephemeral", "material_redacted"]);
const COMPLETION_VERIFIERS = new Set(["independent_review", "independent_review_and_manual_qa"]);
const SEAM_MODES = new Set(["reuse", "extend", "replace", "new_module"]);
const NEW_MODULE_JUSTIFICATIONS = new Set(["authority", "lifecycle", "failure_isolation", "multi_adapter"]);
const MEASUREMENT_BASES = new Set(["complexity_reduction", "defect_rate", "coverage", "latency", "operator_effort"]);

function object(value) { return value && typeof value === "object" && !Array.isArray(value); }
function string(value) { return typeof value === "string" && value.trim().length > 0; }
function timestamp(value) { return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value); }
function list(value) { return Array.isArray(value); }
function at(value) { const ms = Date.parse(value || ""); return Number.isFinite(ms) ? ms : 0; }
function number(value) { return Number.isInteger(value) && value >= 1; }
function exactKeys(value, keys) { return object(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key)); }
function compareUtf8(a, b) {
  const aa = new TextEncoder().encode(a); const bb = new TextEncoder().encode(b);
  for (let i = 0; i < Math.min(aa.length, bb.length); i += 1) if (aa[i] !== bb[i]) return aa[i] - bb[i];
  return aa.length - bb.length;
}
function canonicalize(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  if (object(value)) return `{${Object.keys(value).sort(compareUtf8).map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

// Small synchronous SHA-256 for the controller-sealed projection digest. The
// browser must validate the seal before painting; WebCrypto's async API would
// otherwise leave a race in the read-only render path.
function sha256(text) {
  const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const bytes = new TextEncoder().encode(text); const bitLength = bytes.length * 8; const padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6); padded.set(bytes); padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer); view.setUint32(padded.length - 4, bitLength >>> 0, false); view.setUint32(padded.length - 8, Math.floor(bitLength / 0x100000000), false);
  let h0=0x6a09e667,h1=0xbb67ae85,h2=0x3c6ef372,h3=0xa54ff53a,h4=0x510e527f,h5=0x9b05688c,h6=0x1f83d9ab,h7=0x5be0cd19;
  const rotr=(x,n)=>(x>>>n)|(x<<(32-n));
  for (let offset=0; offset<padded.length; offset+=64) {
    const w = new Uint32Array(64); for (let i=0;i<16;i++) w[i]=view.getUint32(offset+i*4,false);
    for (let i=16;i<64;i++) { const s0=rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>>3); const s1=rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>>10); w[i]=(w[i-16]+s0+w[i-7]+s1)>>>0; }
    let a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,hh=h7;
    for (let i=0;i<64;i++) { const S1=rotr(e,6)^rotr(e,11)^rotr(e,25); const ch=(e&f)^(~e&g); const t1=(hh+S1+ch+K[i]+w[i])>>>0; const S0=rotr(a,2)^rotr(a,13)^rotr(a,22); const maj=(a&b)^(a&c)^(b&c); const t2=(S0+maj)>>>0; hh=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0; }
    h0=(h0+a)>>>0;h1=(h1+b)>>>0;h2=(h2+c)>>>0;h3=(h3+d)>>>0;h4=(h4+e)>>>0;h5=(h5+f)>>>0;h6=(h6+g)>>>0;h7=(h7+hh)>>>0;
  }
  return [h0,h1,h2,h3,h4,h5,h6,h7].map((n)=>n.toString(16).padStart(8,"0")).join("");
}
function canonicalDigest(value) { return `sha256:${sha256(canonicalize(value))}`; }
function semanticEqual(a, b) { return canonicalize(a) === canonicalize(b); }

// Browser-side mirror for the additive sections. It accepts only redacted
// refs/digests and never lets a nested client payload become a visual fact.
function validActivationReliability(value) {
  if (!object(value)) return false;
  if (["raw_transcript", "prompt", "tool_payload", "raw_output"].some((field) => Object.prototype.hasOwnProperty.call(value, field))) return false;
  const activation = value.knowledge_activation;
  if (activation !== undefined) {
      if (!object(activation) || !DIGEST.test(activation.bundle_digest) || !["shadow", "canary", "live", "enforced"].includes(activation.mode)
      || !list(activation.item_dispositions) || !object(activation.closure) || activation.closure.derived_by !== "server"
      || !["open", "blocked", "closed", "not_activated"].includes(activation.closure.state)) return false;
    const binding = activation.canonical_binding;
    if (!exactKeys(binding, ["work_request_id", "work_request_version", "accepted_plan_digest", "envelope_digest", "activation_binding_ref"])
      || !validId(binding.work_request_id) || !number(binding.work_request_version)
      || !DIGEST.test(binding.accepted_plan_digest) || !DIGEST.test(binding.envelope_digest) || !validId(binding.activation_binding_ref)) return false;
    const refs = new Set();
    for (const item of activation.item_dispositions) {
      if (!object(item) || Object.keys(item).some((key) => !["item_ref", "disposition", "evidence_refs", "reason_ref", "stage_ref", "tool_ref"].includes(key))
        || !Object.prototype.hasOwnProperty.call(item, "item_ref") || !Object.prototype.hasOwnProperty.call(item, "disposition")
        || !Object.prototype.hasOwnProperty.call(item, "evidence_refs") || !Object.prototype.hasOwnProperty.call(item, "reason_ref")
        || !validId(item.item_ref) || refs.has(item.item_ref) || !DISPOSITIONS.has(item.disposition)
        || !list(item.evidence_refs) || item.evidence_refs.some((ref) => !validId(ref)) || !validId(item.reason_ref)
        || (item.disposition === "applied" && (item.evidence_refs.length === 0 || (!string(item.stage_ref) && !string(item.tool_ref))))) return false;
      refs.add(item.item_ref);
    }
  }
  const reliability = value.reliability;
  if (reliability !== undefined) {
    const required = ["route_digest", "topology_digest", "evaluation_plan_digest", "grounding_sufficiency", "deterministic_checks", "model_judgement", "human_acceptance", "trajectory", "evaluator_results", "corrections", "defects", "incidents", "downstream_outcome", "outcome_horizon", "process_metrics", "eval_candidates", "shadow_comparisons", "learning_disposition", "telemetry", "closure"];
    const environmentFields = ["environment_binding_digest", "environment_evidence"];
    const hasEnvironment = environmentFields.some((key) => Object.prototype.hasOwnProperty.call(reliability, key));
    const grounding = ["state", "evidence_refs", "required_supplied", "required_used", "required_missing", "advisory_supplied", "advisory_used", "freshness_failures", "retrieval_failures"];
    const trace = ["sequence", "stage_ref", "parent_event_ref", "decision_class", "tool_class", "result_state", "fallback_state", "guardrail_state", "latency_ms", "evidence_refs"];
    const evaluator = ["kind", "evaluator_ref", "rubric_ref", "evaluator_version", "evaluator_digest", "status", "confidence", "critical", "independence_state", "held_out_case_count", "check_refs", "dimension_refs", "evidence_refs", "judge_provenance", "calibration_evidence_refs"];
    const environmentEvidenceFields = ["binding_digest", "session_ref", "lease_state", "operation_count", "policy_refusal_refs", "security_event_refs", "cleanup_state", "cleanup_evidence_refs", "side_effect_state", "resource_usage", "evidence_refs"];
    const resourceUsageFields = ["cpu_ms", "memory_peak_mb", "disk_peak_mb", "network_egress_bytes"];
    if (!exactKeys(reliability, hasEnvironment ? [...required, ...environmentFields] : required) || ![reliability.route_digest, reliability.topology_digest, reliability.evaluation_plan_digest].every((digest) => DIGEST.test(digest))
      || !exactKeys(reliability.grounding_sufficiency, grounding) || !["sufficient", "insufficient", "unknown"].includes(reliability.grounding_sufficiency.state)
      || !grounding.slice(1).every((field) => list(reliability.grounding_sufficiency[field]) && reliability.grounding_sufficiency[field].every(string))
      || !list(reliability.deterministic_checks) || !object(reliability.model_judgement) || !object(reliability.human_acceptance)
      || !list(reliability.trajectory) || !list(reliability.evaluator_results) || reliability.evaluator_results.length === 0
      || !object(reliability.outcome_horizon) || !["mature", "immature", "unavailable", "stale", "unknown"].includes(reliability.outcome_horizon.state)
      || !object(reliability.process_metrics) || !list(reliability.telemetry) || !list(reliability.eval_candidates) || !list(reliability.shadow_comparisons)
      || !exactKeys(reliability.closure, ["state", "reasons", "derived_by"]) || reliability.closure.derived_by !== "server" || !["blocked", "insufficient_evidence", "eligible_for_human_review"].includes(reliability.closure.state) || !list(reliability.closure.reasons)
      || (hasEnvironment && (!DIGEST.test(reliability.environment_binding_digest)
        || !exactKeys(reliability.environment_evidence, environmentEvidenceFields)
        || reliability.environment_evidence.binding_digest !== reliability.environment_binding_digest
        || !validId(reliability.environment_evidence.session_ref)
        || !["active", "released", "expired", "failed", "unknown"].includes(reliability.environment_evidence.lease_state)
        || !Number.isInteger(reliability.environment_evidence.operation_count) || reliability.environment_evidence.operation_count < 0
        || !["policy_refusal_refs", "security_event_refs", "cleanup_evidence_refs", "evidence_refs"].every((field) => list(reliability.environment_evidence[field]) && reliability.environment_evidence[field].every(validId))
        || !["not_required", "pending", "verified", "failed", "unknown"].includes(reliability.environment_evidence.cleanup_state)
        || (["verified", "failed"].includes(reliability.environment_evidence.cleanup_state) && reliability.environment_evidence.cleanup_evidence_refs.length === 0)
        || !["none", "attempted", "refused", "observed", "unknown"].includes(reliability.environment_evidence.side_effect_state)
        || !exactKeys(reliability.environment_evidence.resource_usage, resourceUsageFields)
        || Object.values(reliability.environment_evidence.resource_usage).some((metric) => !Number.isInteger(metric) || metric < 0)
        || reliability.environment_evidence.evidence_refs.length === 0))) return false;
    if (reliability.model_judgement.state === "pass" && (!list(reliability.model_judgement.evidence_refs) || reliability.model_judgement.evidence_refs.length === 0)) return false;
    if (reliability.human_acceptance.actor_ref === reliability.model_judgement.judge_ref) return false;
    if (reliability.deterministic_checks.some((check) => !object(check) || !["passed", "failed", "unknown", "not_run"].includes(check.state) || (check.state === "passed" && (!list(check.evidence_refs) || check.evidence_refs.length === 0)))) return false;
    if (reliability.trajectory.some((row, index) => !exactKeys(row, trace) || row.sequence !== index + 1 || !Number.isInteger(row.latency_ms) || row.latency_ms < 0 || !list(row.evidence_refs) || row.evidence_refs.some((ref) => !string(ref)))) return false;
    if (reliability.evaluator_results.some((row) => !exactKeys(row, evaluator) || !["deterministic", "judge", "human_acceptance"].includes(row.kind) || !["passed", "failed", "blocked", "unknown", "not_run"].includes(row.status) || !["not_independent", "unknown"].includes(row.independence_state) || !DIGEST.test(row.evaluator_digest) || !Number.isInteger(row.held_out_case_count) || row.held_out_case_count < 0 || !list(row.check_refs) || !list(row.dimension_refs) || !list(row.evidence_refs) || !list(row.calibration_evidence_refs))) return false;
    if (reliability.eval_candidates.some((candidate) => !object(candidate) || candidate.state !== "proposed" || candidate.promotion_state !== "not_promoted") || reliability.shadow_comparisons.length !== 0) return false;
    if (reliability.telemetry.length !== 0 || reliability.closure.state === "eligible_for_human_review") return false;
  }
  return true;
}

/** Parse only the explicit wrapper. Other receipts stay ordinary wire traffic. */
export function parseJobPassportReceipt(body) {
  if (typeof body !== "string" || !body.trim().startsWith("{")) return { ok: false, reason: "not_json" };
  let outer;
  try { outer = JSON.parse(body); } catch { return { ok: false, reason: "malformed_json" }; }
  if (!object(outer) || Object.keys(outer).length !== 1 || !object(outer.job_passport)) return { ok: false, reason: "not_job_passport" };
  const wire = outer.job_passport;
  if (Object.keys(wire).length !== 3 || wire.schema_version !== WIRE_VERSION || !KINDS.has(wire.kind) || !object(wire.payload)) {
    return { ok: false, reason: "malformed_job_passport" };
  }
  return { ok: true, kind: wire.kind, payload: wire.payload };
}

function validProjection(value) {
  if (!object(value) || value.schema_version !== "observatory-attempt-projection.v1" || !DIGEST.test(value.projection_digest)
    || !string(value.work_request_id) || !string(value.generated_at) || !object(value.source_state)
    || !number(value.source_state.state_version) || !DIGEST.test(value.source_state.canonical_record_digest)
    || !DIGEST.test(value.source_state.plan_revision_digest) || !object(value.attempt_lane) || !object(value.attempt_lane.persistent_profile)
    || !object(value.attempt_lane.actual_staffing) || !object(value.state) || !list(value.component_map)
    || !object(value.observed_movement) || !list(value.timeline) || !list(value.evidence_refs)) return false;
  if (!string(value.attempt_lane.attempt_id) || !string(value.attempt_lane.persistent_profile.profile_id)
    || !string(value.attempt_lane.persistent_profile.display_label) || !string(value.attempt_lane.actual_staffing.surface)
    || !string(value.attempt_lane.actual_staffing.adapter_id) || !string(value.attempt_lane.actual_staffing.harness_id)
    || !string(value.attempt_lane.actual_staffing.model_id) || !PROGRESS.has(value.state.progress)
    || !LIFECYCLE.has(value.state.lifecycle) || !VERIFICATION.has(value.state.verification)) return false;
  let sequence = 0;
  return value.timeline.every((event) => object(event) && number(event.sequence) && event.sequence > sequence
    && ((sequence = event.sequence) || true) && string(event.occurred_at) && string(event.event_type)
    && list(event.evidence_refs));
}

function validEvalPortfolio(value) {
  if (!object(value) || value.schema_version !== "carr-evaluation-kernel.v1" || value.data_class !== "synthetic_only"
    || !object(value.workflow) || !string(value.workflow.workflow_id) || !object(value.policy) || value.policy.default_effect !== "deny"
    || !object(value.provenance) || !object(value.binding) || !string(value.binding.work_request_id) || !number(value.binding.state_version)
    || !DIGEST.test(value.binding.plan_revision_digest) || !DIGEST.test(value.binding.canonical_record_digest)
    || !DIGEST.test(value.binding.projection_digest) || !list(value.cases) || !list(value.results)
    || !list(value.frontier_comparisons) || !list(value.taxonomy?.failure_modes)) return false;
  const rungs = new Set(["smoke", "regression", "hill_climb", "launch"]);
  const outcomes = new Set(["passed", "failed", "blocked", "unknown"]);
  return value.cases.every((row) => object(row) && string(row.case_id) && rungs.has(row.rung) && list(row.job_stages)
    && object(row.adapter_configuration) && string(row.adapter_configuration.surface))
    && value.results.every((row) => object(row) && string(row.result_id) && string(row.case_id) && rungs.has(row.rung)
      && outcomes.has(row.status) && list(row.dimension_results) && list(row.stage_results)
      && row.dimension_results.every((dimension) => object(dimension) && string(dimension.dimension_id) && outcomes.has(dimension.status)
        && ["improved", "equivalent", "regressed", "not_compared"].includes(dimension.direction_vs_baseline)));
}

function validSpatialSurface(value) {
  if (!object(value) || value.schema_version !== "spatial-surface-projection.v1" || !DIGEST.test(value.projection_digest)
    || !object(value.canonical_binding) || !string(value.canonical_binding.work_request_id) || !number(value.canonical_binding.state_version)
    || !DIGEST.test(value.canonical_binding.canonical_record_digest) || !DIGEST.test(value.canonical_binding.source_projection_digest)
    || !list(value.nodes) || !list(value.edges) || !object(value.semantic_zoom) || !object(value.home_zone) || !list(value.list_order)) return false;
  const ids = new Set();
  for (const node of value.nodes) {
    if (!object(node) || !string(node.node_id) || ids.has(node.node_id) || !object(node.geometry) || !object(node.presentation)
      || !object(node.status) || !object(node.accessibility) || !string(node.accessibility.label) || !string(node.accessibility.non_color_status_token)) return false;
    ids.add(node.node_id);
  }
  return value.list_order.length === ids.size && value.list_order.every((id) => ids.has(id)) && ids.has(value.home_zone.home_node_id)
    && value.edges.every((edge) => object(edge) && ids.has(edge.from_node_id) && ids.has(edge.to_node_id));
}

function validTelemetryMeasurement(value) {
  if (!object(value) || value.schema_version !== "telemetry-measurement.v1" || !string(value.measurement_id)
    || !["subscription_quota", "session_tokens", "billed_cost", "elapsed_time", "lifecycle_activity", "other"].includes(value.metric_kind)
    || !string(value.unit) || !string(value.scope) || !object(value.attribution) || !string(value.attribution.attempt_id)
    || !object(value.source) || !object(value.value) || !string(value.observed_at)
    || !["fresh", "stale", "unknown"].includes(value.freshness)) return false;
  const sourceTypes = new Set(["structured_provider_event", "official_provider_api", "documented_cli_json", "deterministic_local_clock", "unavailable"]);
  const kinds = new Set(["actual", "estimate", "unavailable"]);
  if (!sourceTypes.has(value.source.type) || !Number.isInteger(value.source.priority) || !kinds.has(value.value.kind)) return false;
  if (value.source.type === "unavailable" && value.value.kind !== "unavailable") return false;
  if (value.value.kind === "unavailable") return value.value.amount === null && string(value.value.unavailable_reason);
  if (typeof value.value.amount !== "number" || !Number.isFinite(value.value.amount)) return false;
  return value.value.kind !== "estimate" || (string(value.value.estimate_method) && string(value.value.uncertainty));
}

function validId(value) { return string(value) && /^[A-Za-z][A-Za-z0-9._:-]{2,127}$/.test(value); }
function validEvidence(value, required = false) {
  return list(value) && (required ? value.length > 0 : true) && value.every((row) => exactKeys(row, ["ref", "redaction_class", "content_digest"])
    && validId(row.ref) && ["metadata_only", "redacted_evidence"].includes(row.redaction_class)
    && typeof row.content_digest === "string" && DIGEST.test(row.content_digest));
}
function sameSet(a, b) { return list(a) && list(b) && a.length === b.length && [...a].sort().every((item, index) => item === [...b].sort()[index]); }
function validBinding(value) { return exactKeys(value, ["id", "state_version", "canonical_record_digest"]) && validId(value.id) && number(value.state_version) && DIGEST.test(value.canonical_record_digest); }
function validPlanRef(value) { return exactKeys(value, ["id", "revision", "digest"]) && validId(value.id) && number(value.revision) && DIGEST.test(value.digest); }
function validVersionedMetadata(value) { return exactKeys(value, ["ref", "digest"]) && validId(value.ref) && DIGEST.test(value.digest); }
function validRuntimeMetadata(value) {
  const optional = ["profile_key", "profile_version", "provider_id", "model_id", "desk", "policy_ref", "policy_digest"];
  if (!object(value) || !Object.keys(value).every((key) => ["ref", "digest", ...optional].includes(key)) || !validVersionedMetadata({ ref: value.ref, digest: value.digest })) return false;
  if (value.profile_key !== undefined && !string(value.profile_key)) return false;
  if (value.profile_version !== undefined && (!Number.isInteger(value.profile_version) || value.profile_version < 1)) return false;
  for (const key of ["provider_id", "model_id", "desk", "policy_ref"]) if (value[key] !== undefined && !string(value[key])) return false;
  return value.policy_digest === undefined || DIGEST.test(value.policy_digest);
}
function validActivatedRuntimeMetadata(value) {
  const fields = ["ref", "digest", "profile_key", "profile_version", "provider_id", "model_id", "desk", "policy_ref", "policy_digest", "modality", "reasoning_effort_ref", "sampling_profile_ref", "context_budget", "cache_policy_ref", "knowledge_cutoff_posture", "tool_calling_mode"];
  const environmentFields = ["environment_provider_ref", "environment_provider_version", "environment_provider_digest", "environment_requirement_digest", "environment_configuration_digest", "environment_backend_kind", "environment_source_class", "environment_isolation_class", "environment_capability_refs", "environment_conformance_ref", "environment_conformance_digest", "environment_binding_digest"];
  const hasEnvironment = environmentFields.some((key) => Object.prototype.hasOwnProperty.call(value || {}, key));
  return exactKeys(value, hasEnvironment ? [...fields, ...environmentFields] : fields) && DIGEST.test(value.digest) && Number.isInteger(value.profile_version) && value.profile_version >= 1 && Number.isInteger(value.context_budget) && value.context_budget >= 1
    && ["ref", "profile_key", "provider_id", "policy_ref", "modality", "reasoning_effort_ref", "sampling_profile_ref", "cache_policy_ref", "knowledge_cutoff_posture", "tool_calling_mode"].every((key) => validId(value[key]))
    && string(value.model_id) && string(value.desk) && DIGEST.test(value.policy_digest)
    && (!hasEnvironment || (Number.isInteger(value.environment_provider_version) && value.environment_provider_version >= 1
      && [value.environment_provider_ref, value.environment_conformance_ref].every(validId)
      && [value.environment_provider_digest, value.environment_requirement_digest, value.environment_configuration_digest, value.environment_conformance_digest, value.environment_binding_digest].every((digest) => DIGEST.test(digest))
      && ["none", "local", "container", "remote", "cloud"].includes(value.environment_backend_kind)
      && ["built_in", "plugin"].includes(value.environment_source_class)
      && ["none", "host_process", "container", "microvm", "remote_host"].includes(value.environment_isolation_class)
      && list(value.environment_capability_refs) && value.environment_capability_refs.length > 0 && value.environment_capability_refs.every(validId)));
}
function validActivatedTopology(value) {
  const fields = ["ref", "digest", "kind", "harness_digest", "parallelism", "code_model_step_refs", "fallback_policy_ref", "stop_condition_refs", "context_refresh_policy_ref", "memory_policy_ref", "sandbox_ref", "guardrail_ref", "threat_model_ref"];
  return exactKeys(value, fields) && DIGEST.test(value.digest) && DIGEST.test(value.harness_digest) && ["fixed_workflow", "single_agent_loop", "multi_agent"].includes(value.kind) && ["sequential", "parallel"].includes(value.parallelism)
    && ["ref", "kind", "fallback_policy_ref", "context_refresh_policy_ref", "memory_policy_ref", "sandbox_ref", "guardrail_ref", "threat_model_ref"].every((key) => validId(value[key]))
    && [value.code_model_step_refs, value.stop_condition_refs].every((rows) => list(rows) && rows.every(validId));
}
function validActivatedEvaluationPlan(value) {
  const fields = ["ref", "digest", "lane_ref", "risk_class", "rubric_digest", "case_set_digest", "evaluator_policy_digest", "evaluator_ref", "rubric_ref", "evaluator_version", "evaluator_digest", "required_rungs", "required_deterministic_check_refs", "critical_dimensions", "human_acceptance_required", "outcome_horizon_ref", "outcome_horizon_not_before", "requirements"];
  const req = value?.requirements;
  return exactKeys(value, fields) && [value.digest, value.rubric_digest, value.case_set_digest, value.evaluator_policy_digest, value.evaluator_digest].every((digest) => DIGEST.test(digest)) && [value.ref, value.lane_ref, value.evaluator_ref, value.rubric_ref, value.evaluator_version, value.outcome_horizon_ref].every(validId) && /^R[0-6]$/.test(value.risk_class) && timestamp(value.outcome_horizon_not_before)
    && list(value.required_rungs) && value.required_rungs.every(validId) && list(value.required_deterministic_check_refs) && value.required_deterministic_check_refs.every(validId) && list(value.critical_dimensions) && value.critical_dimensions.every(validId) && typeof value.human_acceptance_required === "boolean"
    && exactKeys(req, ["required_evaluator_kinds", "minimum_held_out_case_count", "minimum_calibration_ref_count", "maximum_critical_failure_count", "maximum_critical_failure_rate", "confidence_posture", "drift_tolerance", "independent_review_required", "human_acceptance_required", "outcome_horizon_required"])
    && list(req.required_evaluator_kinds) && req.required_evaluator_kinds.every((kind) => ["deterministic", "judge", "human_acceptance"].includes(kind))
    && Number.isInteger(req.minimum_held_out_case_count) && req.minimum_held_out_case_count >= 0 && Number.isInteger(req.minimum_calibration_ref_count) && req.minimum_calibration_ref_count >= 0 && Number.isInteger(req.maximum_critical_failure_count) && req.maximum_critical_failure_count >= 0 && typeof req.maximum_critical_failure_rate === "number" && req.maximum_critical_failure_rate >= 0 && req.maximum_critical_failure_rate <= 1
    && ["none", "lower_bound_required"].includes(req.confidence_posture) && ["no_critical_regression", "bounded"].includes(req.drift_tolerance) && ["independent_review_required", "human_acceptance_required", "outcome_horizon_required"].every((key) => typeof req[key] === "boolean");
}
function validActivatedBinding(value) {
  return exactKeys(value, ["bundle_digest", "item_refs", "mode", "retrieval_policy_version"])
    && DIGEST.test(value.bundle_digest) && list(value.item_refs) && value.item_refs.every(validId)
    && ["shadow", "canary", "live", "enforced"].includes(value.mode) && string(value.retrieval_policy_version);
}
function validReliabilityPolicyBinding(value) {
  return exactKeys(value, ["policy_ref", "policy_digest", "risk_class", "mode"])
    && validId(value.policy_ref) && DIGEST.test(value.policy_digest) && /^R[0-6]$/.test(value.risk_class)
    && ["shadow", "canary", "live", "enforced"].includes(value.mode);
}
function validCanonicalReliabilityRevision(value) {
  return exactKeys(value, ["authority_fact_count", "learning_event_count", "outcome_horizon_mature"])
    && Number.isInteger(value.authority_fact_count) && value.authority_fact_count >= 0
    && Number.isInteger(value.learning_event_count) && value.learning_event_count >= 0
    && typeof value.outcome_horizon_mature === "boolean";
}
function compareCanonicalReliabilityRevision(next, prior) {
  const nextVector = [next.authority_fact_count, next.learning_event_count, Number(next.outcome_horizon_mature)];
  const priorVector = [prior.authority_fact_count, prior.learning_event_count, Number(prior.outcome_horizon_mature)];
  if (nextVector.some((part, index) => part < priorVector[index])) return -1;
  return nextVector.some((part, index) => part > priorVector[index]) ? 1 : 0;
}
function validPlannedCheck(value) { return exactKeys(value, ["check_ref", "failure_condition", "evidence_requirement"]) && validId(value.check_ref) && string(value.failure_condition) && ["redacted_evidence_required", "metadata_only_sufficient"].includes(value.evidence_requirement); }
function validExecutionEnvelope(value, passport) {
  const identity = value?.server_binding?.identity; const authority = value?.server_binding?.authority; const adapter = value?.server_binding?.adapter;
  const declared = value?.request?.declared_expectations; const state = value?.state_binding; const handoff = value?.handoff; const phase = value?.phase_binding; const evaluation = value?.evaluation_context;
  if (!object(value) || Object.keys(value).some((key) => !ENVELOPE_CORE_FIELDS.includes(key) && !ENVELOPE_ACTIVATION_FIELDS.includes(key)) || ENVELOPE_CORE_FIELDS.some((key) => !Object.prototype.hasOwnProperty.call(value, key))
    || value.schema_version !== "execution-envelope.v1" || !validId(value.envelope_id) || value.work_request_id !== passport.work_request.id || !validPlanRef(value.plan_revision)
    || !semanticEqual(value.plan_revision, passport.accepted_plan_revision) || !exactKeys(value.agent_session, ["id", "lease_expires_at"]) || !validId(value.agent_session.id) || !timestamp(value.agent_session.lease_expires_at)
    || !timestamp(value.issued_at) || !timestamp(value.expires_at) || !exactKeys(value.request, ["job_ref", "input_digest", "data_class", "allowed_actions", "declared_expectations"]) || !validId(value.request.job_ref) || !DIGEST.test(value.request.input_digest)
    || !["synthetic_only", "metadata_only"].includes(value.request.data_class) || !Array.isArray(value.request.allowed_actions) || value.request.allowed_actions.length !== 0 || !exactKeys(declared, ["plan_step_refs", "component_refs", "component_dependencies", "resource_refs"])
    || !list(declared.plan_step_refs) || !declared.plan_step_refs.every(string) || !list(declared.component_refs) || !declared.component_refs.every(string)
    || !list(declared.resource_refs) || !declared.resource_refs.every(string) || !list(declared.component_dependencies) || !declared.component_dependencies.every((edge) => exactKeys(edge, ["component_ref", "depends_on_component_ref"]) && string(edge.component_ref) && string(edge.depends_on_component_ref))
    || !exactKeys(value.server_binding, ["identity", "authority", "adapter"]) || !exactKeys(identity, ["organization_tenant_id", "sponsoring_human_id", "agent_principal_id", "runtime_principal", "personal_brain_scope", "personal_brain_version", "personal_rule_count", "derived_by", "client_mutable"])
    || !validId(identity.organization_tenant_id) || !validId(identity.sponsoring_human_id) || !validId(identity.agent_principal_id) || !validId(identity.runtime_principal) || !validId(identity.personal_brain_scope) || !validId(identity.personal_brain_version)
    || !Number.isInteger(identity.personal_rule_count) || identity.personal_rule_count < 0 || identity.derived_by !== "server_identity_resolution" || identity.client_mutable !== false
    || !exactKeys(authority, ["environment", "risk_class", "capability_profile", "capability_grant_ref", "read_only", "derived_by", "client_mutable"]) || !["local", "rehearsal", "staging", "production"].includes(authority.environment) || !/^R[0-6]$/.test(authority.risk_class)
    || !validId(authority.capability_profile) || !validId(authority.capability_grant_ref) || authority.read_only !== true || authority.derived_by !== "server_capability_resolution" || authority.client_mutable !== false
    || !exactKeys(adapter, ["surface", "adapter_id", "adapter_version", "harness_id", "harness_version", "provider_id", "model_id", "native_session_ref", "configuration_fingerprint"])
    || !["claude_desktop", "codex_desktop", "hermes_desktop", "grok_x_native"].includes(adapter.surface) || !string(adapter.adapter_id) || !string(adapter.adapter_version) || !string(adapter.harness_id) || !string(adapter.harness_version) || !string(adapter.provider_id) || !string(adapter.model_id) || !validId(adapter.native_session_ref) || !DIGEST.test(adapter.configuration_fingerprint)
    || !exactKeys(handoff, ["mode", "replaces_agent_session_id", "capability_inherited", "checkpoint_ref", "native_session_transfer"]) || !["original", "replacement"].includes(handoff.mode) || handoff.capability_inherited !== false || handoff.native_session_transfer !== "semantic_state_only"
    || (handoff.mode === "original" && (handoff.replaces_agent_session_id !== null || handoff.checkpoint_ref !== null)) || (handoff.mode === "replacement" && (!validId(handoff.replaces_agent_session_id) || !validId(handoff.checkpoint_ref) || handoff.replaces_agent_session_id === value.agent_session.id))
    || !exactKeys(state, ["state_version", "canonical_record_digest", "accepted_resource_revisions", "compare_and_swap_required"]) || !number(state.state_version) || state.state_version !== passport.work_request.state_version
    || state.canonical_record_digest !== passport.work_request.canonical_record_digest || !DIGEST.test(state.canonical_record_digest) || !list(state.accepted_resource_revisions) || state.accepted_resource_revisions.some((row) => !exactKeys(row, ["resource_ref", "revision_ref", "digest"]) || !validId(row.resource_ref) || !validId(row.revision_ref) || !DIGEST.test(row.digest)) || state.compare_and_swap_required !== true
    || !exactKeys(phase, ["phase_id", "session_affinity", "switch_conditions", "native_session_transfer"]) || !validId(phase.phase_id) || !["same_native_session_required", "same_native_session_preferred", "fresh_native_session_required"].includes(phase.session_affinity) || !list(phase.switch_conditions) || phase.switch_conditions.length === 0 || !phase.switch_conditions.every((condition) => ["verified_checkpoint", "native_session_unavailable", "phase_boundary", "capability_expired"].includes(condition)) || !phase.switch_conditions.includes("verified_checkpoint") || phase.native_session_transfer !== "semantic_state_only"
    || !exactKeys(evaluation, ["experiment_arm", "auditor_mode", "evaluation_kernel_ref", "workflow_rubric_digest", "case_set_digest"]) || !["fixed_native_pair", "same_pair_audited_state", "audited_state_routed_executors", "diverse_auditor"].includes(evaluation.experiment_arm) || !["none", "same_pair_auditor", "diverse_read_only_auditor"].includes(evaluation.auditor_mode) || !validId(evaluation.evaluation_kernel_ref) || !DIGEST.test(evaluation.workflow_rubric_digest) || !DIGEST.test(evaluation.case_set_digest)) return false;
  const hasActivation = ENVELOPE_ACTIVATION_FIELDS.some((field) => Object.prototype.hasOwnProperty.call(value, field));
  const runtime = [value.runtime_profile, value.execution_topology, value.evaluation_plan];
  if (hasActivation) {
    if (!ENVELOPE_ACTIVATION_FIELDS.every((field) => Object.prototype.hasOwnProperty.call(value, field))
      || !validActivatedBinding(value.activation_binding) || !validReliabilityPolicyBinding(value.reliability_policy_binding)
      || value.activation_binding.mode !== value.reliability_policy_binding.mode || !validId(value.context_activation_ref)
      || !validActivatedRuntimeMetadata(value.runtime_profile) || !validActivatedTopology(value.execution_topology) || !validActivatedEvaluationPlan(value.evaluation_plan)) return false;
  } else if (runtime.some((item) => item !== undefined) && (!runtime.every((item) => item !== undefined) || !validRuntimeMetadata(value.runtime_profile) || !validVersionedMetadata(value.execution_topology) || !validVersionedMetadata(value.evaluation_plan))) return false;
  return true;
}
function uniqueIdArray(value) { return list(value) && value.every(validId) && new Set(value).size === value.length; }

/** Q035.D1 classifier: mirrors DESIGN_DEPTH_PREDICATES["engineering-design-contract.v1"] server-side. */
function designDepth(slice) {
  return SHORT_RISK_CLASSES.has(slice.risk_class) && slice.concurrency_posture === "parallel_safe" && slice.manual_qa_required === false
    && slice.release_requirement === "not_required" && slice.dependency_refs.length === 0
    && slice.declared_resource_refs.length <= 1 && slice.declared_component_refs.length <= 1 && slice.declared_plan_step_refs.length <= 1
    ? "short" : "full";
}

function validSelectionBasis(value) {
  return list(value) && value.length > 0 && value.every((item) => typeof item === "string") && new Set(value).size === value.length
    && value.every((item) => SELECTION_BASIS_VALUES.has(item)) && !(value.length === 1 && value[0] === "cost");
}

function validModelJudgmentSteps(slice, decision) {
  if (!list(decision.model_judgment_steps)) return null;
  const steps = decision.model_judgment_steps;
  const seen = new Set();
  for (const step of steps) {
    if (!exactKeys(step, MODEL_STEP_FIELDS)) return null;
    if (!validId(step.step_ref) || seen.has(step.step_ref) || !slice.declared_plan_step_refs.includes(step.step_ref)) return null;
    seen.add(step.step_ref);
    if (RESERVED_CODE_RESPONSIBILITIES.has(step.responsibility_class) || !TYPED_UNCERTAINTY_CLASSES.has(step.responsibility_class)) return null;
    if (!validId(step.input_contract_ref) || !validId(step.output_contract_ref)) return null;
    if (!string(step.rationale)) return null;
    if (!validSelectionBasis(step.selection_basis)) return null;
  }
  return steps;
}

/** Validate the closed Q046.D1 slice contract for one accepted v2 slice. Mirrors requireDesignContract. */
function validDesignContract(slice) {
  const contract = slice.design_contract;
  if (!exactKeys(contract, DESIGN_CONTRACT_FIELDS)) return false;
  if (contract.contract_version !== DESIGN_CONTRACT_VERSION) return false;
  if (!string(contract.rationale) || !string(contract.dependency_rationale)) return false;

  const decision = contract.code_model_decision;
  if (!exactKeys(decision, ["model_judgment_steps", "rationale", "selection_basis"])) return false;
  if (!string(decision.rationale) || !validSelectionBasis(decision.selection_basis)) return false;
  const steps = validModelJudgmentSteps(slice, decision);
  if (steps === null) return false;

  const routing = contract.routing;
  if (!exactKeys(routing, ["adapter_ref", "executor_class", "fresh_session_required"])) return false;
  if (!EXECUTOR_CLASSES.has(routing.executor_class) || !validId(routing.adapter_ref) || routing.fresh_session_required !== true) return false;
  if (routing.executor_class === "deterministic_code" && steps.length) return false;
  if (routing.executor_class === "model_assisted" && !steps.length) return false;

  const authority = contract.authority;
  if (!exactKeys(authority, ["capability_profile", "environment", "read_only"])) return false;
  if (!validId(authority.capability_profile) || typeof authority.read_only !== "boolean" || !AUTHORITY_ENVIRONMENTS.has(authority.environment)) return false;
  if (!authority.read_only && authority.capability_profile !== "capability:engineering-repository-write") return false;

  const isolation = contract.isolation;
  if (!exactKeys(isolation, ["branch_required", "shared_resource_refs", "worktree_required"])) return false;
  if (isolation.worktree_required !== true || isolation.branch_required !== true) return false;
  if (!uniqueIdArray(isolation.shared_resource_refs)) return false;
  if (isolation.shared_resource_refs.some((ref) => !slice.declared_resource_refs.includes(ref))) return false;
  if (isolation.shared_resource_refs.length && slice.concurrency_posture === "parallel_safe") return false;

  const tests = contract.tests;
  if (!exactKeys(tests, ["planned_check_refs", "verification_lanes"])) return false;
  const plannedRefs = slice.planned_checks.map((check) => check.check_ref);
  if (!uniqueIdArray(tests.planned_check_refs) || tests.planned_check_refs.join(",") !== plannedRefs.join(",")) return false;
  if (!list(tests.verification_lanes) || !tests.verification_lanes.length || new Set(tests.verification_lanes).size !== tests.verification_lanes.length
    || tests.verification_lanes.some((lane) => !VERIFICATION_LANES.has(lane))) return false;
  if (tests.verification_lanes.includes("manual_qa") !== slice.manual_qa_required) return false;

  const review = contract.review;
  if (!exactKeys(review, ["independent_review_required", "reviewer_class"])) return false;
  if (review.independent_review_required !== true || !REVIEWER_CLASSES.has(review.reviewer_class)) return false;

  const failure = contract.failure;
  if (!exactKeys(failure, ["failure_modes"]) || !list(failure.failure_modes) || !failure.failure_modes.length) return false;
  const failureRefs = new Set();
  for (const mode of failure.failure_modes) {
    if (!exactKeys(mode, ["compensation", "detection", "failure_ref"])) return false;
    if (!validId(mode.failure_ref) || failureRefs.has(mode.failure_ref)) return false;
    failureRefs.add(mode.failure_ref);
    if (!string(mode.detection) || !string(mode.compensation)) return false;
  }

  const evidenceFacet = contract.evidence;
  if (!exactKeys(evidenceFacet, ["evidence_refs", "redaction_class", "retention"])) return false;
  if (!EVIDENCE_REDACTION_CLASSES.has(evidenceFacet.redaction_class) || !EVIDENCE_RETENTIONS.has(evidenceFacet.retention)) return false;
  if (!validEvidence(evidenceFacet.evidence_refs)) return false;
  if (evidenceFacet.evidence_refs.some((item) => item.redaction_class !== evidenceFacet.redaction_class)) return false;
  if (slice.planned_checks.some((check) => check.evidence_requirement === "redacted_evidence_required") && evidenceFacet.redaction_class !== "redacted_evidence") return false;

  const deployment = contract.deployment;
  if (!exactKeys(deployment, ["confirmation_required", "release_requirement", "rollback_ref"])) return false;
  if (deployment.release_requirement !== slice.release_requirement) return false;
  if (deployment.release_requirement === "required") {
    if (!validId(deployment.rollback_ref)) return false;
  } else if (deployment.rollback_ref !== null && !validId(deployment.rollback_ref)) return false;
  if (typeof deployment.confirmation_required !== "boolean") return false;
  if (!["R0", "R1"].includes(slice.risk_class) && deployment.confirmation_required !== true) return false;

  const completion = contract.completion;
  if (!exactKeys(completion, ["completion_predicate", "verified_by"])) return false;
  if (!string(completion.completion_predicate) || !COMPLETION_VERIFIERS.has(completion.verified_by)) return false;
  if (completion.verified_by !== (slice.manual_qa_required ? "independent_review_and_manual_qa" : "independent_review")) return false;

  const seam = contract.seam_decision;
  if (!exactKeys(seam, ["measurement", "mode", "new_module_justification", "replaced_seam_refs", "residual_authority_refs", "target_seam_ref"])) return false;
  if (!SEAM_MODES.has(seam.mode) || !validId(seam.target_seam_ref)) return false;
  if (!exactKeys(seam.measurement, ["basis", "note"]) || !MEASUREMENT_BASES.has(seam.measurement.basis) || !string(seam.measurement.note)) return false;
  if (!uniqueIdArray(seam.replaced_seam_refs) || !uniqueIdArray(seam.residual_authority_refs)) return false;
  if (seam.mode === "new_module") {
    if (!NEW_MODULE_JUSTIFICATIONS.has(seam.new_module_justification)) return false;
  } else if (seam.new_module_justification !== null) return false;
  if (seam.mode === "replace") {
    if (!seam.replaced_seam_refs.length || seam.replaced_seam_refs.includes(seam.target_seam_ref)) return false;
    if (seam.residual_authority_refs.length) return false;
  } else if (seam.replaced_seam_refs.length || seam.residual_authority_refs.length) return false;

  const depth = designDepth(slice);
  if (depth === "full") {
    if (contract.short_template !== null) return false;
    const refs = contract.full_design_refs;
    if (!exactKeys(refs, FULL_DESIGN_REF_FIELDS)) return false;
    for (const field of ["design_interview_ref", "authority_envelope_ref", "failure_model_ref", "oracle_ref"]) if (!validId(refs[field])) return false;
    if (!uniqueIdArray(refs.fixture_refs) || !refs.fixture_refs.length) return false;
  } else {
    if (contract.full_design_refs !== null) return false;
    const template = contract.short_template;
    if (!exactKeys(template, SHORT_TEMPLATE_FIELDS)) return false;
    if (!validId(template.template_ref) || !validId(template.verification_ref) || !string(template.objective_summary)) return false;
  }
  return true;
}

/** Every slice a slice depends on, directly or transitively. Mirrors transitiveDependencies server-side. */
function transitiveDependencies(plan) {
  const direct = new Map(plan.slices.map((row) => [row.slice_ref, row.dependency_refs || []]));
  const resolved = new Map();
  const visit = (ref, stack) => {
    if (resolved.has(ref)) return resolved.get(ref);
    if (stack.has(ref)) return new Set();
    stack.add(ref);
    const closure = new Set();
    for (const dependency of direct.get(ref) || []) {
      closure.add(dependency);
      for (const item of visit(dependency, stack)) closure.add(item);
    }
    stack.delete(ref);
    resolved.set(ref, closure);
    return closure;
  };
  for (const ref of direct.keys()) visit(ref, new Set());
  return resolved;
}

/** Refuse duplicate seam authority and half-replacement across one v2 plan. Mirrors requireSeamAuthority. */
function validSeamAuthority(plan) {
  const owners = new Map(); const retired = new Map();
  for (const slice of plan.slices) {
    const seam = slice.design_contract.seam_decision;
    if (["new_module", "replace"].includes(seam.mode)) {
      if (owners.has(seam.target_seam_ref)) return false;
      owners.set(seam.target_seam_ref, slice.slice_ref);
    }
    for (const seamRef of seam.replaced_seam_refs) {
      if (retired.has(seamRef)) return false;
      retired.set(seamRef, slice.slice_ref);
    }
  }
  return plan.slices.every((slice) => {
    const seam = slice.design_contract.seam_decision;
    return !(["reuse", "extend"].includes(seam.mode) && retired.has(seam.target_seam_ref));
  });
}

/** Refuse two concurrently admissible parallel_safe slices claiming one declared resource. Mirrors requireParallelResourceIsolation. */
function validParallelResourceIsolation(plan) {
  const ordered = transitiveDependencies(plan);
  const owners = new Map();
  for (const slice of plan.slices) {
    if (slice.concurrency_posture !== "parallel_safe") continue;
    for (const resourceRef of new Set(slice.declared_resource_refs)) {
      for (const other of owners.get(resourceRef) || []) {
        if (ordered.get(slice.slice_ref)?.has(other) || ordered.get(other)?.has(slice.slice_ref)) continue;
        return false;
      }
      owners.set(resourceRef, [...(owners.get(resourceRef) || []), slice.slice_ref]);
    }
  }
  return true;
}

function validEngineeringPlan(value) {
  if (!exactKeys(value, ["schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slices"]) || !SLICE_PLAN_VERSIONS.has(value.schema_version)
    || !validBinding(value.work_request) || !validPlanRef(value.accepted_plan_revision) || !DIGEST.test(value.plan_digest) || !list(value.slices) || value.slices.length === 0) return false;
  const isV2 = value.schema_version === SLICE_PLAN_V2;
  const refs = new Set(), ordinals = new Set();
  for (const slice of value.slices) {
    const requiredKeys = ["slice_ref", "ordinal", "objective", "definition_of_done", "dependency_refs", "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs", "baseline_evidence_refs", "planned_checks", "scope_boundary", "forbidden_change_refs", "concurrency_posture", "manual_qa_required", "risk_class", "release_requirement"];
    if (isV2) requiredKeys.push("design_contract");
    if (!exactKeys(slice, requiredKeys)
      || !validId(slice.slice_ref) || !number(slice.ordinal) || refs.has(slice.slice_ref) || ordinals.has(slice.ordinal)
      || !string(slice.objective) || !string(slice.definition_of_done) || !list(slice.dependency_refs) || !slice.dependency_refs.every(validId)
      || !list(slice.declared_resource_refs) || !slice.declared_resource_refs.every(validId) || !list(slice.declared_component_refs) || !slice.declared_component_refs.every(validId)
      || !list(slice.declared_plan_step_refs) || !slice.declared_plan_step_refs.every(validId) || !validEvidence(slice.baseline_evidence_refs)
      || !list(slice.planned_checks) || slice.planned_checks.length === 0 || !slice.planned_checks.every(validPlannedCheck) || new Set(slice.planned_checks.map((check) => check.check_ref)).size !== slice.planned_checks.length
      || !string(slice.scope_boundary) || !list(slice.forbidden_change_refs) || !slice.forbidden_change_refs.every(validId)
      || !["parallel_safe", "serial_after_dependencies", "exclusive_resource"].includes(slice.concurrency_posture)
      || typeof slice.manual_qa_required !== "boolean" || !/^R[0-6]$/.test(slice.risk_class) || !["required", "not_required"].includes(slice.release_requirement)) return false;
    if (isV2 && !validDesignContract(slice)) return false;
    refs.add(slice.slice_ref); ordinals.add(slice.ordinal);
  }
  if (!value.slices.every((slice) => slice.dependency_refs.every((dep) => refs.has(dep)))) return false;
  const visit = (ref, visiting = new Set(), visited = new Set()) => { if (visiting.has(ref)) return false; if (visited.has(ref)) return true; visiting.add(ref); const slice = value.slices.find((row) => row.slice_ref === ref); if (!slice || !slice.dependency_refs.every((dep) => visit(dep, visiting, visited))) return false; visiting.delete(ref); visited.add(ref); return true; };
  if (![...refs].every((ref) => visit(ref))) return false;
  if (isV2 && (!validSeamAuthority(value) || !validParallelResourceIsolation(value))) return false;
  const copy = structuredClone(value); delete copy.plan_digest;
  return canonicalDigest(copy) === value.plan_digest;
}
function validReceipt(value, plan, receiptRefs, attemptRefs) {
  if (!exactKeys(value, ["schema_version", "envelope_digest", "attempt_id", "slice_ref", "plan_digest", "attribution", "planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs", "checks", "outcome", "artifact_refs", "evidence_refs", "deviations", "source_evidence", "reset_reconstruction", "executor_claim", "independent_verification_required"])
    || value.schema_version !== "engineering-slice-receipt.v1" || !DIGEST.test(value.envelope_digest) || !validId(value.attempt_id) || !validId(value.slice_ref) || receiptRefs.has(value.slice_ref) || attemptRefs.has(value.attempt_id) || value.plan_digest !== plan.plan_digest) return false;
  const slice = plan.slices.find((row) => row.slice_ref === value.slice_ref); if (!slice || !exactKeys(value.attribution, ["actor_ref", "session_ref", "adapter_ref"]) || !validId(value.attribution.actor_ref) || !validId(value.attribution.session_ref) || !validId(value.attribution.adapter_ref)) return false;
  for (const field of ["planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs", "artifact_refs"]) if (!list(value[field]) || !value[field].every(validId)) return false;
  if (!sameSet(value.planned_resource_refs, slice.declared_resource_refs) || !sameSet(value.planned_component_refs, slice.declared_component_refs)) return false;
  if (!list(value.checks) || value.checks.length !== slice.planned_checks.length) return false;
  const planned = new Map(slice.planned_checks.map((check) => [check.check_ref, check])); const seen = new Set();
  for (const check of value.checks) {
    if (!exactKeys(check, ["check_ref", "state", "evidence_refs"]) || !validId(check.check_ref) || seen.has(check.check_ref) || !planned.has(check.check_ref) || !["passed", "failed", "blocked", "not_run"].includes(check.state) || !validEvidence(check.evidence_refs, check.state === "passed") || (check.state === "passed" && !check.evidence_refs.some((evidence) => evidence.redaction_class === (planned.get(check.check_ref).evidence_requirement === "redacted_evidence_required" ? "redacted_evidence" : "metadata_only")))) return false;
    seen.add(check.check_ref);
  }
  const deviationRefs = new Set(); if (!list(value.deviations) || !value.deviations.every((deviation) => exactKeys(deviation, ["deviation_ref", "category", "reason", "impact", "plan_revision_required", "evidence_refs", "out_of_scope_resource_refs", "out_of_scope_component_refs", "review_state"]) && validId(deviation.deviation_ref) && !deviationRefs.has(deviation.deviation_ref) && string(deviation.category) && string(deviation.reason) && string(deviation.impact) && typeof deviation.plan_revision_required === "boolean" && validEvidence(deviation.evidence_refs) && deviation.evidence_refs.length > 0 && list(deviation.out_of_scope_resource_refs) && deviation.out_of_scope_resource_refs.every(validId) && list(deviation.out_of_scope_component_refs) && deviation.out_of_scope_component_refs.every(validId) && ["unreviewed", "reviewed", "resolved"].includes(deviation.review_state) && (deviationRefs.add(deviation.deviation_ref), true))) return false;
  const allowedResources = new Set(slice.declared_resource_refs.concat(value.deviations.filter((deviation) => deviation.review_state === "resolved").flatMap((deviation) => deviation.out_of_scope_resource_refs))); const allowedComponents = new Set(slice.declared_component_refs.concat(value.deviations.filter((deviation) => deviation.review_state === "resolved").flatMap((deviation) => deviation.out_of_scope_component_refs)));
  if (!value.actual_resource_refs.every((ref) => allowedResources.has(ref)) || !value.actual_component_refs.every((ref) => allowedComponents.has(ref)) || !["claimed_complete", "failed", "blocked", "reopened"].includes(value.outcome) || !validEvidence(value.evidence_refs, value.outcome === "claimed_complete") || (value.outcome === "claimed_complete" && (value.artifact_refs.length === 0 || value.checks.some((check) => check.state !== "passed")))) return false;
  if (!exactKeys(value.source_evidence, ["worktree_ref", "branch_ref", "source_sha", "evidence_refs"]) || !validId(value.source_evidence.worktree_ref) || !validId(value.source_evidence.branch_ref) || !string(value.source_evidence.source_sha) || !validEvidence(value.source_evidence.evidence_refs)) return false;
  if (!exactKeys(value.reset_reconstruction, ["fresh_session", "inherited_transcript_used", "reconstruction_free", "remediation_action"]) || value.reset_reconstruction.fresh_session !== true || value.reset_reconstruction.inherited_transcript_used !== false || typeof value.reset_reconstruction.reconstruction_free !== "boolean" || (value.reset_reconstruction.reconstruction_free ? !(value.reset_reconstruction.remediation_action === null || string(value.reset_reconstruction.remediation_action)) : !string(value.reset_reconstruction.remediation_action))) return false;
  return exactKeys(value.executor_claim, ["claim_state", "claimed_by", "claimed_at"]) && value.executor_claim.claim_state === "executor_claim" && validId(value.executor_claim.claimed_by) && string(value.executor_claim.claimed_at) && value.independent_verification_required === true;
}
function validReviewerFacts(value, receipts) {
  if (!list(value)) return false; const bySlice = new Map(receipts.map((receipt) => [receipt.slice_ref, receipt])); const seen = new Set();
  return value.every((fact) => exactKeys(fact, ["slice_ref", "attempt_id", "reviewer_ref", "session_ref", "state", "evidence_refs", "is_independent", "reviewed_deviation_refs", "resolved_deviation_refs"]) && validId(fact.slice_ref) && !seen.has(fact.slice_ref) && bySlice.has(fact.slice_ref) && fact.attempt_id === bySlice.get(fact.slice_ref).attempt_id && validId(fact.attempt_id) && validId(fact.reviewer_ref) && validId(fact.session_ref) && fact.reviewer_ref !== bySlice.get(fact.slice_ref).attribution.actor_ref && fact.session_ref !== bySlice.get(fact.slice_ref).attribution.session_ref && fact.is_independent === true && ["passed", "failed", "blocked"].includes(fact.state) && validEvidence(fact.evidence_refs, fact.state === "passed") && list(fact.reviewed_deviation_refs) && fact.reviewed_deviation_refs.every(validId) && sameSet(fact.reviewed_deviation_refs, bySlice.get(fact.slice_ref).deviations.map((deviation) => deviation.deviation_ref)) && list(fact.resolved_deviation_refs) && fact.resolved_deviation_refs.every(validId) && fact.resolved_deviation_refs.every((ref) => fact.reviewed_deviation_refs.includes(ref)) && (fact.state !== "passed" || (sameSet(fact.resolved_deviation_refs, fact.reviewed_deviation_refs) && bySlice.get(fact.slice_ref).deviations.every((deviation) => deviation.review_state === "resolved")) ) && (seen.add(fact.slice_ref), true));
}
function validQAFacts(value, receipts) { if (!list(value)) return false; const known = new Set(receipts.map((receipt) => receipt.slice_ref)); const seen = new Set(); return value.every((fact) => exactKeys(fact, ["slice_ref", "state", "evidence_refs", "note"]) && validId(fact.slice_ref) && known.has(fact.slice_ref) && !seen.has(fact.slice_ref) && ["passed", "failed", "blocked"].includes(fact.state) && validEvidence(fact.evidence_refs, fact.state === "passed") && string(fact.note) && (seen.add(fact.slice_ref), true)); }
function validDisposition(value, states) { return exactKeys(value, ["state", "evidence_refs", "note"]) && states.has(value.state) && validEvidence(value.evidence_refs, ["complete", "passed", "released", "resolved", "proposed"].includes(value.state)) && string(value.note); }
function validLearning(value) { return exactKeys(value, ["state", "route", "evidence_refs", "note"]) && ["unresolved", "proposed", "rejected", "nothing_durable"].includes(value.state) && (value.route === null || ["regression_test", "gate_or_validator", "decision_record", "skill_or_workflow", "memory_or_rule_candidate", "incident_finding", "speculative_finding", "nothing_durable"].includes(value.route)) && (value.state === "unresolved" ? value.route === null : value.route !== null) && validEvidence(value.evidence_refs, ["proposed", "rejected"].includes(value.state)) && string(value.note); }
function derivedOperator(slices, receipts, qa) { const evidence = new Map(receipts.flatMap((receipt) => receipt.evidence_refs.map((item) => [item.ref, item]))); return { what_changed: slices.filter((slice) => slice.state === "verified_complete").map((slice) => slice.slice_ref), why: "derived from accepted plan and typed receipts", evidence_refs: [...evidence.keys()].sort().map((key) => evidence.get(key)), deviations: [...new Set(receipts.flatMap((receipt) => receipt.deviations.map((deviation) => deviation.deviation_ref)))].sort(), remaining_risk: slices.filter((slice) => slice.state !== "verified_complete").map((slice) => slice.slice_ref), manual_qa_items: slices.filter((slice) => slice.manual_qa_required && !qa.has(slice.slice_ref)).map((slice) => slice.slice_ref) }; }
function validEngineeringPassport(value) {
  if (!exactKeys(value, ["schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slice_plan", "execution_envelopes", "slices", "receipts", "reviewer_facts", "qa_facts", "operator_receipt", "closure", "closure_state", "stale_conflict", "projection_digest"]) || value.schema_version !== "engineering-passport.v1" || !validBinding(value.work_request) || !validPlanRef(value.accepted_plan_revision) || !DIGEST.test(value.plan_digest) || !validEngineeringPlan(value.slice_plan) || value.slice_plan.plan_digest !== value.plan_digest || !semanticEqual(value.slice_plan.work_request, value.work_request) || !semanticEqual(value.slice_plan.accepted_plan_revision, value.accepted_plan_revision) || !["blocked", "complete"].includes(value.closure_state) || !exactKeys(value.stale_conflict, ["state", "reason"]) || !["none", "stale", "conflict", "uncertain"].includes(value.stale_conflict.state) || (value.stale_conflict.state === "none" ? value.stale_conflict.reason !== null : !string(value.stale_conflict.reason))) return false;
  if (!list(value.execution_envelopes)) return false;
  const envelopeDigests = new Set();
  if (!value.execution_envelopes.every((envelope) => validExecutionEnvelope(envelope, value) && !envelopeDigests.has(canonicalDigest(envelope)) && (envelopeDigests.add(canonicalDigest(envelope)), true))) return false;
  const expectedOrder = [...value.slice_plan.slices].sort((a, b) => a.ordinal - b.ordinal); if (!list(value.slices) || value.slices.length !== expectedOrder.length || value.slices.some((slice, index) => !object(slice) || slice.slice_ref !== expectedOrder[index].slice_ref || slice.ordinal !== expectedOrder[index].ordinal)) return false;
  const states = new Set(["eligible", "blocked", "claimed", "reopened", "verified_complete"]); const receipts = []; const receiptRefs = new Set(); const attemptRefs = new Set(); if (!list(value.receipts) || !value.receipts.every((receipt) => validReceipt(receipt, value.slice_plan, receiptRefs, attemptRefs) && (receiptRefs.add(receipt.slice_ref), attemptRefs.add(receipt.attempt_id), receipts.push(receipt), true))) return false;
  const receiptEnvelopeDigests = new Set(receipts.map((receipt) => receipt.envelope_digest));
  if ((receipts.length === 0 && envelopeDigests.size !== 0) || (receipts.length > 0 && (receiptEnvelopeDigests.size !== envelopeDigests.size || [...envelopeDigests].some((digest) => !receiptEnvelopeDigests.has(digest)))) || !receipts.every((receipt) => envelopeDigests.has(receipt.envelope_digest))) return false;
  const receiptBySlice = new Map(receipts.map((receipt) => [receipt.slice_ref, receipt])); const reviewers = list(value.reviewer_facts) ? value.reviewer_facts : []; if (!validReviewerFacts(reviewers, receipts)) return false; const reviewerBy = new Map(reviewers.map((fact) => [fact.slice_ref, fact])); if (!validQAFacts(value.qa_facts, receipts)) return false; const qaBy = new Map(value.qa_facts.map((fact) => [fact.slice_ref, fact]));
  const projected = value.slices; if (!projected.every((slice) => exactKeys(slice, ["slice_ref", "ordinal", "dependency_refs", "state", "planned_check_refs", "deviation_refs", "manual_qa_required", "release_requirement"]) && list(slice.dependency_refs) && slice.dependency_refs.every(validId) && states.has(slice.state) && list(slice.planned_check_refs) && JSON.stringify(slice.planned_check_refs) === JSON.stringify(expectedOrder.find((row) => row.slice_ref === slice.slice_ref).planned_checks.map((check) => check.check_ref)) && list(slice.deviation_refs) && slice.deviation_refs.every(validId) && typeof slice.manual_qa_required === "boolean" && ["required", "not_required"].includes(slice.release_requirement))) return false;
  const expectedEligible = new Set(expectedOrder.filter((slice) => !receiptBySlice.has(slice.slice_ref) && slice.dependency_refs.every((dep) => reviewerBy.has(dep) && reviewerBy.get(dep).state === "passed" && receiptBySlice.get(dep)?.outcome === "claimed_complete")).map((slice) => slice.slice_ref));
  if (!projected.every((slice) => { const source = expectedOrder.find((row) => row.slice_ref === slice.slice_ref); const receipt = receiptBySlice.get(slice.slice_ref); const review = reviewerBy.get(slice.slice_ref); const qa = qaBy.get(slice.slice_ref); const expected = !receipt ? (expectedEligible.has(slice.slice_ref) ? "eligible" : "blocked") : review?.state === "passed" && (!source.manual_qa_required || qa?.state === "passed") ? "verified_complete" : receipt.outcome === "failed" || receipt.outcome === "reopened" || qa?.state === "failed" ? "reopened" : "claimed"; return JSON.stringify(slice.dependency_refs) === JSON.stringify(source.dependency_refs) && slice.state === expected && JSON.stringify(slice.deviation_refs) === JSON.stringify((receipt?.deviations || []).map((deviation) => deviation.deviation_ref)) && slice.manual_qa_required === source.manual_qa_required && slice.release_requirement === source.release_requirement; })) return false;
  if (!exactKeys(value.operator_receipt, ["what_changed", "why", "evidence_refs", "deviations", "remaining_risk", "manual_qa_items"]) || !list(value.operator_receipt.what_changed) || !list(value.operator_receipt.deviations) || !list(value.operator_receipt.remaining_risk) || !list(value.operator_receipt.manual_qa_items) || !validEvidence(value.operator_receipt.evidence_refs) || !string(value.operator_receipt.why) || JSON.stringify(value.operator_receipt) !== JSON.stringify(derivedOperator(projected, receipts, qaBy))) return false;
  if (!exactKeys(value.closure, ["work", "proof", "explanation", "release", "learning"]) || !validDisposition(value.closure.work, new Set(["unresolved", "complete"])) || !validDisposition(value.closure.proof, new Set(["unresolved", "complete"])) || !validDisposition(value.closure.explanation, new Set(["unresolved", "complete"])) || !validDisposition(value.closure.release, new Set(["unresolved", "released", "not_required"])) || !validLearning(value.closure.learning)) return false;
  const complete = value.stale_conflict.state === "none" && projected.length > 0 && receipts.length === projected.length && projected.every((slice) => slice.state === "verified_complete") && receipts.every((receipt) => receipt.outcome === "claimed_complete" && receipt.artifact_refs.length > 0 && receipt.evidence_refs.length > 0) && projected.every((slice) => reviewerBy.get(slice.slice_ref)?.state === "passed") && projected.every((slice) => !slice.manual_qa_required || qaBy.get(slice.slice_ref)?.state === "passed") && value.closure.work.state === "complete" && value.closure.proof.state === "complete" && value.closure.explanation.state === "complete" && (value.closure.release.state === "released" || (value.closure.release.state === "not_required" && projected.every((slice) => slice.release_requirement === "not_required"))) && ["proposed", "rejected", "nothing_durable"].includes(value.closure.learning.state) && value.closure.learning.route !== null && receipts.every((receipt) => receipt.deviations.every((deviation) => !deviation.plan_revision_required && deviation.review_state === "resolved" && reviewerBy.get(receipt.slice_ref).resolved_deviation_refs.includes(deviation.deviation_ref)));
  if ((value.closure_state === "complete") !== complete) return false; const copy = structuredClone(value); delete copy.projection_digest; return canonicalDigest(copy) === value.projection_digest;
}

function compareProjection(a, b) {
  const av = a.source_state.state_version;
  const bv = b.source_state.state_version;
  if (av !== bv) return av - bv;
  // Same version but a different canonical digest is a conflict, never a
  // winner. A genuine later wire sequence may only refresh the same digest.
  if (a.source_state.canonical_record_digest !== b.source_state.canonical_record_digest) return null;
  return at(a.generated_at) - at(b.generated_at);
}

function statusFor(projection) {
  const observed = projection.observed_movement;
  if (projection.state.verification === "verified_success" && projection.state.progress === "verified_complete") return "verified_complete";
  if (projection.state.lifecycle === "failed" || projection.state.progress === "failed") return "failed";
  if (projection.state.progress === "blocked") return "blocked";
  if (projection.state.progress === "stale") return "stale";
  if (projection.state.progress === "quiet") return "quiet";
  if (projection.state.lifecycle === "partial" || projection.state.progress === "unknown" || projection.state.verification === "partial" || projection.state.verification === "unknown") return "unknown_partial";
  if (Array.isArray(observed.deviation_candidates) && observed.deviation_candidates.length) return "deviation_candidate";
  return "aligned";
}

/**
 * Derive the feature-gated cards from the existing receipt stream. Malformed,
 * mismatched, and stale data never replaces a newer projection; conflict is a
 * visible state, not a silent tie-breaker. Auxiliary typed facts are retained
 * for wire accounting but only a controller-built projection paints a card.
 */
export function deriveJobPassports(turns, { now = Date.now() } = {}) {
  const rows = new Map();
  const rejected = [];
  const typedCounts = { execution_envelope: 0, progress_event: 0, attempt_receipt: 0, activation_reliability_projection: 0, observatory_projection: 0, evaluation_kernel: 0, eval_portfolio: 0, spatial_surface: 0, telemetry_measurement: 0 };
  const portfolios = new Map();
  const surfaces = new Map();
  const telemetryByAttempt = new Map();
  const engineering = new Map();
  const attemptFacts = new Map();
  const canonicalReliability = new Map();
  const canonicalReliabilityConflicts = new Map();
  for (const turn of turns || []) {
    if (turn?.kind !== "receipt") continue;
    const parsed = parseJobPassportReceipt(turn.body);
    if (!parsed.ok) continue;
    if (parsed.kind === "engineering_passport") {
      typedCounts.engineering_passport = (typedCounts.engineering_passport || 0) + 1;
      if (!validEngineeringPassport(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_engineering_passport" }); continue; }
      const prior = engineering.get(parsed.payload.work_request.id);
      if (prior && prior.payload.work_request.state_version === parsed.payload.work_request.state_version
        && prior.payload.work_request.canonical_record_digest !== parsed.payload.work_request.canonical_record_digest) {
        prior.conflict = true;
        rejected.push({ seq: Number(turn.seq) || 0, reason: "conflicting_engineering_passport" });
      } else if (!prior || parsed.payload.work_request.state_version > prior.payload.work_request.state_version
        || (parsed.payload.work_request.state_version === prior.payload.work_request.state_version && (Number(turn.seq) || 0) > prior.seq)) {
        engineering.set(parsed.payload.work_request.id, { payload: parsed.payload, seq: Number(turn.seq) || 0, conflict: false });
      } else if (prior && parsed.payload.work_request.state_version < prior.payload.work_request.state_version) {
        rejected.push({ seq: Number(turn.seq) || 0, reason: "stale_engineering_passport" });
      }
      continue;
    }
    typedCounts[parsed.kind] += 1;
    if (parsed.kind === "activation_reliability_projection") {
      const value = parsed.payload; const binding = value?.canonical_binding;
      if (!object(value) || !exactKeys(value, ["canonical_binding", "canonical_revision", "learning", "telemetry", "reliability"]) || !validCanonicalReliabilityRevision(value.canonical_revision) || !object(binding)
        || !exactKeys(binding, ["work_request_id", "work_request_version", "accepted_plan_digest", "envelope_digest", "attempt_id", "activation_binding_ref"])
        || !validId(binding.work_request_id) || !number(binding.work_request_version) || !DIGEST.test(binding.accepted_plan_digest) || !DIGEST.test(binding.envelope_digest) || !validId(binding.attempt_id) || !validId(binding.activation_binding_ref)
        || !object(value.learning) || !["unavailable", "proposed", "triaged", "accepted", "retired", "none"].includes(value.learning.lifecycle)
        || !Array.isArray(value.learning.candidate_refs) || value.learning.candidate_refs.some((ref) => !validId(ref)) || !Array.isArray(value.telemetry)
        || !object(value.reliability) || !["blocked", "insufficient_evidence", "eligible_for_human_review"].includes(value.reliability.state) || !Array.isArray(value.reliability.reasons) || value.reliability.reasons.some((reason) => !validId(reason))) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_canonical_activation_reliability" }); continue; }
      const key = `${binding.attempt_id}|${binding.work_request_id}|${binding.work_request_version}|${binding.accepted_plan_digest}|${binding.envelope_digest}|${binding.activation_binding_ref}`;
      const prior = canonicalReliability.get(key);
      const conflictRevision = canonicalReliabilityConflicts.get(key);
      if (conflictRevision) {
        const conflictOrder = compareCanonicalReliabilityRevision(value.canonical_revision, conflictRevision);
        if (conflictOrder > 0) { canonicalReliability.set(key, value); canonicalReliabilityConflicts.delete(key); }
        else rejected.push({ seq: Number(turn.seq) || 0, reason: conflictOrder < 0 ? "stale_canonical_activation_reliability" : "conflicting_canonical_activation_reliability" });
      } else if (!prior) canonicalReliability.set(key, value);
      else if (compareCanonicalReliabilityRevision(value.canonical_revision, prior.canonical_revision) > 0) canonicalReliability.set(key, value);
      else if (compareCanonicalReliabilityRevision(value.canonical_revision, prior.canonical_revision) < 0) rejected.push({ seq: Number(turn.seq) || 0, reason: "stale_canonical_activation_reliability" });
      else if (!semanticEqual(prior, value)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "conflicting_canonical_activation_reliability" }); canonicalReliability.set(key, null); canonicalReliabilityConflicts.set(key, prior.canonical_revision); }
      continue;
    }
    if (parsed.kind === "attempt_receipt") {
      if (!validActivationReliability(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_activation_reliability" }); continue; }
      const binding = parsed.payload.knowledge_activation?.canonical_binding;
      if (string(parsed.payload.attempt_id) && binding && (parsed.payload.knowledge_activation || parsed.payload.reliability)) {
        const key = `${parsed.payload.attempt_id}|${binding.work_request_id}|${binding.work_request_version}|${binding.accepted_plan_digest}|${binding.envelope_digest}|${binding.activation_binding_ref}`;
        const prior = attemptFacts.get(key);
        if (prior && JSON.stringify(prior) !== JSON.stringify(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "conflicting_attempt_activation_fact" }); attemptFacts.set(key, null); }
        else if (!prior) attemptFacts.set(key, parsed.payload);
      }
      continue;
    }
    if (parsed.kind === "evaluation_kernel" || parsed.kind === "eval_portfolio") {
      if (!validEvalPortfolio(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_eval_portfolio" }); continue; }
      const prior = portfolios.get(parsed.payload.binding.work_request_id);
      if (!prior || (Number(turn.seq) || 0) > prior.seq) portfolios.set(parsed.payload.binding.work_request_id, { payload: parsed.payload, seq: Number(turn.seq) || 0 });
      continue;
    }
    if (parsed.kind === "spatial_surface") {
      if (!validSpatialSurface(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_spatial_surface" }); continue; }
      const binding = parsed.payload.canonical_binding;
      const prior = surfaces.get(binding.work_request_id);
      if (prior && binding.state_version < prior.payload.canonical_binding.state_version) { rejected.push({ seq: Number(turn.seq) || 0, reason: "stale_spatial_surface" }); continue; }
      if (prior && binding.state_version === prior.payload.canonical_binding.state_version && binding.canonical_record_digest !== prior.payload.canonical_binding.canonical_record_digest) { rejected.push({ seq: Number(turn.seq) || 0, reason: "conflicting_spatial_surface" }); continue; }
      if (!prior || (Number(turn.seq) || 0) > prior.seq) surfaces.set(binding.work_request_id, { payload: parsed.payload, seq: Number(turn.seq) || 0 });
      continue;
    }
    if (parsed.kind === "telemetry_measurement") {
      if (!validTelemetryMeasurement(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_telemetry_measurement" }); continue; }
      const attemptId = parsed.payload.attribution.attempt_id;
      const prior = telemetryByAttempt.get(attemptId) || new Map();
      const existing = prior.get(parsed.payload.metric_kind);
      if (!existing || at(parsed.payload.observed_at) > at(existing.payload.observed_at) || (at(parsed.payload.observed_at) === at(existing.payload.observed_at) && (Number(turn.seq) || 0) > existing.seq)) prior.set(parsed.payload.metric_kind, { payload: parsed.payload, seq: Number(turn.seq) || 0 });
      telemetryByAttempt.set(attemptId, prior);
      continue;
    }
    if (parsed.kind !== "observatory_projection") continue;
    if (!validProjection(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_projection" }); continue; }
    const incoming = parsed.payload;
    const prior = rows.get(incoming.work_request_id);
    if (!prior) { rows.set(incoming.work_request_id, { projection: incoming, seq: Number(turn.seq) || 0, conflict: false }); continue; }
    const comparison = compareProjection(prior.projection, incoming);
    if (comparison === null) {
      prior.conflict = true;
      rejected.push({ seq: Number(turn.seq) || 0, reason: "same_version_conflict" });
    } else if (comparison < 0 || (comparison === 0 && (Number(turn.seq) || 0) > prior.seq)) {
      rows.set(incoming.work_request_id, { projection: incoming, seq: Number(turn.seq) || 0, conflict: false });
    } else {
      rejected.push({ seq: Number(turn.seq) || 0, reason: "stale_projection" });
    }
  }
  const passports = [...rows.values()].map((row) => {
    const projection = row.projection;
    const observedAt = Math.max(at(projection.generated_at), ...projection.timeline.map((event) => at(event.occurred_at)));
    const portfolio = portfolios.get(projection.work_request_id)?.payload;
    const portfolioBound = portfolio && portfolio.binding.state_version === projection.source_state.state_version
      && portfolio.binding.canonical_record_digest === projection.source_state.canonical_record_digest
      && portfolio.binding.projection_digest === projection.projection_digest;
    if (portfolio && !portfolioBound) rejected.push({ seq: portfolios.get(projection.work_request_id).seq, reason: "mismatched_eval_portfolio" });
    const spatial = surfaces.get(projection.work_request_id)?.payload;
    const spatialBound = spatial && spatial.canonical_binding.state_version === projection.source_state.state_version
      && spatial.canonical_binding.canonical_record_digest === projection.source_state.canonical_record_digest
      && spatial.canonical_binding.source_projection_digest === projection.projection_digest;
    if (spatial && !spatialBound) rejected.push({ seq: surfaces.get(projection.work_request_id).seq, reason: "mismatched_spatial_surface" });
    const engineeringCandidate = engineering.get(projection.work_request_id);
    let engineeringPassport = null;
    if (engineeringCandidate) {
      const binding = engineeringCandidate.payload;
      const exactBinding = binding.work_request.state_version === projection.source_state.state_version
        && binding.work_request.canonical_record_digest === projection.source_state.canonical_record_digest
        && binding.accepted_plan_revision.digest === projection.source_state.plan_revision_digest;
      if (engineeringCandidate.conflict) rejected.push({ seq: engineeringCandidate.seq, reason: "conflicting_engineering_passport" });
      else if (binding.work_request.state_version < projection.source_state.state_version) rejected.push({ seq: engineeringCandidate.seq, reason: "stale_engineering_passport" });
      else if (!exactBinding) rejected.push({ seq: engineeringCandidate.seq, reason: "mismatched_engineering_passport" });
      else engineeringPassport = binding;
    }
    return {
      ...projection, wire_seq: row.seq, conflict: row.conflict, status: row.conflict ? "unknown_partial" : statusFor(projection),
      observed_at: observedAt || now, freshness: projection.state.progress === "stale" ? "stale" : "current_as_observed",
      eval_portfolio: portfolioBound ? portfolio : null,
      spatial_surface: spatialBound ? spatial : null,
      telemetry_measurements: [...(telemetryByAttempt.get(projection.attempt_lane.attempt_id)?.values() || [])].map((row) => row.payload),
      engineering_passport: engineeringPassport,
      activation_reliability: (() => {
        const candidates = [...attemptFacts.values()].filter((fact) => fact && (() => {
          const binding = fact.knowledge_activation.canonical_binding;
          return fact.attempt_id === projection.attempt_lane.attempt_id
            && binding.work_request_id === projection.work_request_id
            && binding.work_request_version === projection.source_state.state_version
            && binding.accepted_plan_digest === projection.source_state.plan_revision_digest
            && fact.result?.job_ref === `job:${projection.work_request_id}`;
        })());
        if (candidates.length !== 1) return null;
        const binding = candidates[0].knowledge_activation.canonical_binding;
        const key = `${candidates[0].attempt_id}|${binding.work_request_id}|${binding.work_request_version}|${binding.accepted_plan_digest}|${binding.envelope_digest}|${binding.activation_binding_ref}`;
        const canonical = canonicalReliability.get(key);
        return { knowledge_activation: candidates[0].knowledge_activation || null, reliability: candidates[0].reliability || null, canonical: canonical || null };
      })(),
    };
  }).sort((a, b) => b.wire_seq - a.wire_seq);
  return { enabled: passports.length > 0, passports, rejected, typedCounts };
}

export const JOB_PASSPORT_STATUS_LABEL = {
  aligned: "Aligned", deviation_candidate: "Deviation candidate", blocked: "Blocked", quiet: "Quiet", stale: "Stale",
  failed: "Failed", unknown_partial: "Unknown / partial", verified_complete: "Independently verified complete",
};

export function jobPassportStatusLabel(status) { return JOB_PASSPORT_STATUS_LABEL[status] || "Unknown / partial"; }
