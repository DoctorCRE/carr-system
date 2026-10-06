// Durable Engineering Passport runtime seam.
//
// This module is deliberately a projection/admission adapter over the existing
// Work Request, accepted sourced plan, capability session and ops.job ledgers.
// It does not create a second queue, authority source, transcript store, or
// model identity.  Codex is the only executable adapter in this first slice;
// Claude is refused explicitly until a fresh-native-session launcher exists.

import { sha256 } from "./sha256.js";
import { organizationTenantForActor } from "./identity.js";
import {
  NO_CEREMONIAL_MERGE_DECISION,
  NO_CEREMONIAL_MERGE_DECISION_TITLE,
} from "./source-merge-policy.js";

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ID = /^[A-Za-z][A-Za-z0-9._:-]{2,127}$/;
const OWNERSHIP_SESSION_REF = /^ownership:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const EXECUTION_HOST_REF = /^cloudflare-workers:[A-Za-z0-9][A-Za-z0-9._:-]{2,95}$/;
const OUTCOMES = new Set(["claimed_complete", "failed", "blocked", "reopened"]);
export const ENGINEERING_REPOSITORY_ACTIONS = Object.freeze([
  "repository:create-worktree",
  "repository:create-branch",
  "repository:write-declared-scope",
  "repository:run-checks",
  "repository:commit",
  "repository:push-branch",
  "repository:open-pr",
]);

const canonicalize = value => {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object")
    return Object.keys(value).sort().reduce((out, key) => {
      if (value[key] !== undefined) out[key] = canonicalize(value[key]);
      return out;
    }, {});
  return value;
};

export function canonicalDigest(value) {
  return `sha256:${sha256(JSON.stringify(canonicalize(value)))}`;
}

function error(ToolError, payload) { throw new ToolError(payload); }
function text(value, field, ToolError) {
  if (typeof value !== "string" || !value.trim()) error(ToolError, { error: "engineering_field_required", field });
  return value.trim();
}
function id(value, field, ToolError) {
  const result = text(value, field, ToolError);
  if (!ID.test(result)) error(ToolError, { error: "engineering_identifier_invalid", field });
  return result;
}
// The same closed ID regex, applied to the value exactly as the producer wrote
// it.  id() above tests text()'s TRIMMED value and then hands the caller that
// trimmed copy, but every plan call site discards the return and keeps the raw
// string, so " slice:one " passed admission and was then sealed, stored and
// compared under an identity nothing else recognises.  The portable validator
// (engineering_passport._str(identifier=True)) and the SQL validators have
// always matched the raw value, so this is the identifier contract already in
// force at the other boundaries, applied here with the same regex.  A padded
// identifier is refused, never trimmed into an accepted identity: rewriting it
// would silently change the sealed plan_digest content.
function exactId(value, field, ToolError) {
  if (typeof value !== "string" || !value.trim()) error(ToolError, { error: "engineering_field_required", field });
  if (!ID.test(value)) error(ToolError, { error: "engineering_identifier_invalid", field });
  return value;
}
function digest(value, field, ToolError) {
  if (typeof value !== "string" || !DIGEST.test(value)) error(ToolError, { error: "engineering_digest_invalid", field });
  return value;
}
function evidence(value, field, ToolError, identifier = id) {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join(",") !== "content_digest,redaction_class,ref") error(ToolError, { error: "engineering_evidence_invalid", field });
  identifier(value.ref, `${field}.ref`, ToolError);
  digest(value.content_digest, `${field}.content_digest`, ToolError);
  if (!["metadata_only", "redacted_evidence"].includes(value.redaction_class)) error(ToolError, { error: "engineering_evidence_invalid", field });
  return value;
}
function uuid(value, field, ToolError) {
  const result = text(value, field, ToolError);
  if (!UUID.test(result)) error(ToolError, { error: "engineering_uuid_invalid", field });
  return result;
}
function exactAuthorityFree(args, ToolError) {
  const forbidden = ["tenant", "organization_tenant_id", "sponsor", "partner", "actor", "identity",
    "authority", "capability", "runtime", "model", "provider", "surface", "adapter", "native_session_ref"];
  const found = forbidden.filter(key => Object.hasOwn(args || {}, key));
  if (found.length) error(ToolError, { error: "caller_authority_selector_forbidden", fields: found });
}

// The only JavaScript seam for migration0450 ownership effects.  Its input is
// deliberately not a tool schema: callers cannot supply tenant, actor,
// session, host, principal, or a lease token.  The Worker supplies an already
// sealed envelope/attempt/job binding, the authenticated controller binding
// supplies the durable capability-agent session and live job lease, and
// Cloudflare deployment metadata supplies the host.  The checked-out
// connection is from the issuer-only pool: ordinary writer/jobs connections
// cannot SET ROLE to the capability role or execute either closed function.
export async function withTrustedCanonicalOwnershipContext(c, actor, binding, ToolError, operation) {
  if (typeof operation !== "function") throw new TypeError("canonical ownership operation is required");
  const fields = ["accepted_plan_id", "agent_session_id", "attempt", "envelope_id", "expires_at",
    "idempotency_key", "job_lease_token", "work_request_id"];
  if (!binding || typeof binding !== "object" || Array.isArray(binding) ||
      Object.keys(binding).sort().join(",") !== fields.join(","))
    error(ToolError, { error: "canonical_ownership_binding_invalid" });
  const workRequestId = uuid(binding.work_request_id, "canonical_ownership.work_request_id", ToolError);
  const acceptedPlanId = uuid(binding.accepted_plan_id, "canonical_ownership.accepted_plan_id", ToolError);
  const agentSessionId = uuid(binding.agent_session_id, "canonical_ownership.agent_session_id", ToolError);
  const envelopeId = uuid(binding.envelope_id, "canonical_ownership.envelope_id", ToolError);
  const idempotencyKey = uuid(binding.idempotency_key, "canonical_ownership.idempotency_key", ToolError);
  const jobLeaseToken = uuid(binding.job_lease_token, "canonical_ownership.job_lease_token", ToolError);
  if (!Number.isSafeInteger(binding.attempt) || binding.attempt < 1)
    error(ToolError, { error: "canonical_ownership_attempt_invalid" });
  const expiry = new Date(binding.expires_at);
  if (Number.isNaN(expiry.getTime()) || expiry.getTime() <= Date.now())
    error(ToolError, { error: "canonical_ownership_expiry_invalid" });
  const runtimeSessionRef = `session:${agentSessionId}`;
  const executionHost = actor?.execution_host_id;
  const organizationTenantId = organizationTenantForActor(actor);
  if (!actor?.slug || !organizationTenantId || !EXECUTION_HOST_REF.test(executionHost || ""))
    error(ToolError, { error: "canonical_ownership_server_context_unavailable" });
  await c.query("set local role carr_ownership_issuer /* engineering-runtime:issuer-capability */");
  await c.query(
    "select set_config('carr.organization_tenant_id',$1::text,true), " +
    "set_config('carr.acting_actor_slug',$2::text,true), " +
    "set_config('carr.receipt_session_ref',$3::text,true), " +
    "set_config('carr.execution_host_id',$4::text,true), " +
    "set_config('carr.engineering_job_lease_token',$5::text,true) " +
    "/* engineering-runtime:canonical-ownership-server-binding */",
    [organizationTenantId, actor.slug, runtimeSessionRef, executionHost, jobLeaseToken],
  );
  const minted = (await c.query(
    `select ops.mint_canonical_ownership_runtime_session($1::uuid,$2::uuid,$3::uuid,$4::integer,$5::text,$6::text,$7::timestamptz,$8::uuid) as binding
       /* engineering-runtime:mint-canonical-ownership-context */`,
    [workRequestId, acceptedPlanId, envelopeId, binding.attempt, runtimeSessionRef, executionHost,
      expiry.toISOString(), idempotencyKey],
  )).rows[0]?.binding;
  if (!minted?.ok || !OWNERSHIP_SESSION_REF.test(minted.ownership_session_ref || "") ||
      minted.organization_tenant_id !== organizationTenantId ||
      minted.acting_actor_slug !== actor.slug || minted.execution_host_ref !== executionHost)
    error(ToolError, { error: minted?.error || "canonical_ownership_context_mint_refused" });
  await c.query(
    "select set_config('carr.organization_tenant_id',$1::text,true), " +
    "set_config('carr.acting_actor_slug',$2::text,true), " +
    "set_config('carr.ownership_session_id',$3::text,true), " +
    "set_config('carr.execution_host_id',$4::text,true) /* engineering-runtime:canonical-ownership-context */",
    [minted.organization_tenant_id, minted.acting_actor_slug, minted.ownership_session_ref, minted.execution_host_ref],
  );
  const context = (await c.query(
    "select ops.canonical_ownership_trusted_context() as context /* engineering-runtime:canonical-ownership-context-readback */",
  )).rows[0]?.context;
  if (!context?.ok || context.ownership_session_ref !== minted.ownership_session_ref ||
      context.organization_tenant_id !== minted.organization_tenant_id ||
      context.acting_actor_slug !== minted.acting_actor_slug || context.execution_host_ref !== minted.execution_host_ref)
    error(ToolError, { error: context?.error || "canonical_ownership_context_refused" });
  return await operation(context);
}

// --- V5-F03 deep-module execution contract -----------------------------------
//
// engineering-slice-plan.v1 keeps its exact accepted shape, refusals and
// canonical digest.  The Q046.D1 machine-readable slice contract, the
// Q016.D1/Q029.D1 code-versus-model bindings and the Q035.D1 design-depth
// classifier arrive as the explicit successor engineering-slice-plan.v2 so no
// established v1 producer is silently reinterpreted, and this stays the one
// slice-contract authority rather than a second parallel validator.  The
// portable tools/room-bridge/engineering_passport.py validator implements the
// identical predicate for engineering-slice-plan.v2: for v2 the two accept and
// refuse exactly the same inputs.
//
// DELIBERATE LEGACY v1 DIVERGENCES.  The portable validator has always refused
// duplicate ordinals, dependency cycles and whitespace-padded identifiers for
// every plan version; this server validator historically accepted all three,
// and plans registered under it are append-only.  requirePlan re-runs against
// the STORED plan row on every read path (sourcePlan, closureProjection,
// controllerPlan), so newly refusing those shapes for v1 would not correct a
// stored plan -- it would strand one, making an already registered passport
// unreadable with nothing able to amend the immutable row.  All three checks
// are therefore enforced for engineering-slice-plan.v2 only, and a pre-existing
// v1 plan keeps its exact previous read behavior.
//
// THE READ GATE IS THE DECLARED PLAN VERSION, NOT THE ROW'S AGE.  requirePlan
// is one predicate over one input and cannot tell a fresh registration from a
// stored row -- the read paths hand it the stored plan and registration hands it
// the caller's -- so refusing these three shapes for v1 inside requirePlan would
// make the same plan registerable and then unreadable, or readable and then
// unregisterable, depending on which side moved.  requirePlan therefore keeps
// accepting v1 exactly as before, and every stored v1 plan keeps its read,
// admission and receipt behavior.
//
// NEW v1 REGISTRATIONS ARE RETIRED AT THE REGISTRATION DOORS, NOT HERE (V5-F03,
// 2026-09-25).  A v1 plan carries no design_contract, so registering one today
// would skip the per-slice rationale, code/model allocation and seam decision
// that V5-F03 requires of every slice.  That narrowing is a registration
// policy, so it lives only where a plan first enters storage:
// requireRegistrablePlanVersion below, called from the
// register-engineering-slice-plan handler, and the database's
// ops.engineering_register_slice_plan (migrations/0616_f03_retire_legacy_slice_plan_registration.sql),
// which covers a direct carr_writer call.  The two doors refuse the same thing,
// so nothing is registerable-but-unreadable: a v1 plan either was stored before
// the cutoff and reads as it always did, or cannot be stored at all.  A
// producer registers the successor version.  These remain documented
// divergences, not parity: the two validators are not interchangeable on
// legacy v1 duplicate ordinals, cycles or padded identifiers in stored rows.
//
// EXACT IDENTIFIERS FOR v2.  A v2 plan admits every identifier exactly as the
// producer wrote it (exactId).  id() validated text()'s trimmed copy while the
// plan kept the raw string, so " slice:one " registered here yet was refused by
// the portable and SQL validators, and the identity sealed into plan_digest was
// not the identity the regex had approved.  The regex itself is unchanged and
// no new version is introduced; a padded identifier is refused rather than
// normalised, because rewriting one would silently change sealed content.

export const ENGINEERING_SLICE_PLAN_VERSIONS = Object.freeze([
  "engineering-slice-plan.v1", "engineering-slice-plan.v2",
]);
const SLICE_PLAN_V2 = "engineering-slice-plan.v2";
// Versions a NEW registration may carry.  Stored plans are read under
// ENGINEERING_SLICE_PLAN_VERSIONS; see the V5-F03 note above.
export const ENGINEERING_SLICE_PLAN_REGISTRABLE_VERSIONS = Object.freeze([SLICE_PLAN_V2]);

/**
 * Registration-only version gate.  Never call this from a read path: a stored
 * v1 plan must keep validating under requirePlan exactly as before.
 */
export function requireRegistrablePlanVersion(plan, ToolError) {
  if (!ENGINEERING_SLICE_PLAN_REGISTRABLE_VERSIONS.includes(plan?.schema_version))
    error(ToolError, {
      error: "engineering_slice_plan_version_not_registrable", schema_version: plan?.schema_version ?? null,
      registrable: [...ENGINEERING_SLICE_PLAN_REGISTRABLE_VERSIONS],
      resolution: "new plans register as engineering-slice-plan.v2 with a design_contract on every slice; stored v1 plans stay readable",
    });
  return plan;
}
export const ENGINEERING_DESIGN_CONTRACT_VERSION = "engineering-design-contract.v1";

const CONCURRENCY_POSTURES = new Set(["parallel_safe", "serial_after_dependencies", "exclusive_resource"]);
const RELEASE_REQUIREMENTS = new Set(["required", "not_required"]);
const EVIDENCE_REQUIREMENTS = new Set(["redacted_evidence_required", "metadata_only_sufficient"]);

// Q035.D1 classifier inputs.  planned_checks is deliberately absent: every
// declared check stays mandatory and adding verification can never move a slice
// from SHORT to FULL.  Free text is never parsed, because interpreting prose
// would recreate model judgment and an unaccepted taxonomy.
const DESIGN_DEPTH_INPUT_FIELDS = Object.freeze([
  "risk_class", "concurrency_posture", "manual_qa_required", "release_requirement",
  "dependency_refs", "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs",
]);
const DESIGN_DEPTH_COUNTED_ARRAYS = Object.freeze([
  "dependency_refs", "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs",
]);
const SHORT_RISK_CLASSES = new Set(["R0", "R1", "R2", "R3"]);

// The accepted Q035.D1 predicate is frozen to the design-contract version that
// sealed it.  requirePlan re-runs against the STORED append-only plan row on
// every read path (sourcePlan, closureProjection, controllerPlan), so a
// predicate that quietly changed underneath an immutable plan would reclassify
// already sealed work and make its passport unreadable with nothing able to
// amend the row.  A different boundary therefore ships as an explicit successor
// contract version with its own entry here, exactly as engineering-slice-plan
// ships v2 beside v1; the v1 entry below never moves.
const DESIGN_DEPTH_PREDICATES = Object.freeze({
  "engineering-design-contract.v1": row =>
    SHORT_RISK_CLASSES.has(row.risk_class) &&
    row.concurrency_posture === "parallel_safe" &&
    row.manual_qa_required === false &&
    row.release_requirement === "not_required" &&
    row.dependency_count === 0 &&
    row.declared_resource_count <= 1 &&
    row.declared_component_count <= 1 &&
    row.declared_plan_step_count <= 1,
});
export const ENGINEERING_DESIGN_DEPTH_PREDICATE_VERSIONS =
  Object.freeze(Object.keys(DESIGN_DEPTH_PREDICATES));

// The agent may never hand the classifier its own answer.  The closed field set
// already refuses unknown keys; this named refusal makes the bypass explicit.
const SELF_LABEL_FIELDS = new Set([
  "design_depth", "depth", "template", "template_kind", "complexity", "complexity_class",
  "simple", "is_simple", "classification", "classifier_override", "bypass",
]);

const DESIGN_CONTRACT_FIELDS = Object.freeze([
  "authority", "code_model_decision", "completion", "contract_version", "dependency_rationale",
  "deployment", "evidence", "failure", "full_design_refs", "isolation", "rationale", "review",
  "routing", "seam_decision", "short_template", "tests",
]);
const MODEL_STEP_FIELDS = Object.freeze([
  "input_contract_ref", "output_contract_ref", "rationale", "responsibility_class",
  "selection_basis", "step_ref",
]);
// THE ORACLE AND FIXTURE REFS ARE A DECLARATION, NOT A BINDING, and that is a
// decision rather than an omission.  All five names below are checked for shape
// and uniqueness and NOTHING RESOLVES THEM: no code here asks whether
// oracle_ref names a real oracle or whether a fixture_ref names a fixture that
// exists.  A FULL slice may therefore seal an oracle nobody can run.
//
// WHY NOT RESOLVE THEM HERE.  requirePlan is one pure predicate over one input
// and takes no connection, and it re-runs against the STORED append-only plan
// row on every read path (sourcePlan, closureProjection, controllerPlan).  A
// resolver would make an already registered passport unreadable the day its
// referent was renamed, retired or moved, with nothing able to amend the
// immutable row -- the same stranding the v1 divergences above refuse, except
// arriving later and from outside the plan.  Resolution binds a moment; a
// sealed plan binds forever, so the two cannot be the same check.
//
// WHY NOT RESOLVE THEM ELSEWHERE EITHER, yet.  There is no registry to resolve
// against.  The only oracle registry in this tree is the A00 kernel's closed
// MINIMUM_REQUIRED_MEMBERS set (benchmark-minimum.v5.js), whose refs are
// gate-producer oracles bound to producer roles; a slice's oracle_ref lives in
// a different namespace and no producer, table or manifest enumerates it.
// Inventing one here would be a second authority over what an oracle is, which
// is the excluded-scope item this slice must not violate.
//
// WHAT THAT LEAVES.  A FULL slice must NAME its oracle and at least one
// fixture, exactly, uniquely, and permanently: the refs are sealed into
// plan_digest, so a plan cannot be re-pointed at a friendlier oracle after
// acceptance without breaking its own seal, and a reviewer reading the plan is
// told precisely which oracle the producer claimed.  Binding those names to
// resolvable objects needs a registry with an owner and a retirement rule, and
// that is deliberately OUT OF SCOPE here; nothing below implements it.
const FULL_DESIGN_REF_FIELDS = Object.freeze([
  "authority_envelope_ref", "design_interview_ref", "failure_model_ref", "fixture_refs", "oracle_ref",
]);
const SHORT_TEMPLATE_FIELDS = Object.freeze(["objective_summary", "template_ref", "verification_ref"]);

// Q016.D1: deterministic code owns these outright; a model judgment step that
// claims one of them is refused rather than reviewed.
const RESERVED_CODE_RESPONSIBILITIES = new Set([
  "identity", "policy", "permissions", "state", "validation", "idempotency", "execution",
]);
// THE ONE TYPED-UNCERTAINTY AUTHORITY, and the reason it is exported rather
// than private.  A second consumer -- the V5-F04 model-routing adapter -- has
// to speak this same closed vocabulary when it validates a typed proposal, and
// it restated the six names instead, under a comment promising the two were one
// set with nothing holding them there.  That is the "duplicate authorities"
// shape this slice's own excluded scope refuses, so the vocabulary is written
// once here and imported there.  The frozen list is the authority; the Set
// below is derived from it and stays private, because membership is all this
// module needs and an exported Set would be a mutable second handle on it.
export const ENGINEERING_TYPED_UNCERTAINTY_CLASSES = Object.freeze([
  "classification", "extraction", "summarization", "ranking", "drafting", "disambiguation",
]);
const TYPED_UNCERTAINTY_CLASSES = new Set(ENGINEERING_TYPED_UNCERTAINTY_CLASSES);
// Q029.D1: cost may appear alongside a capability reason but never alone.
const SELECTION_BASIS_VALUES = new Set([
  "typed_uncertainty", "capability_gain", "quality_gain", "adaptability_gain", "cost",
]);
const EXECUTOR_CLASSES = new Set(["deterministic_code", "attended_human", "model_assisted"]);
const AUTHORITY_ENVIRONMENTS = new Set(["local", "rehearsal", "staging", "production"]);
const VERIFICATION_LANES = new Set(["unit", "contract", "integration", "manual_qa"]);
// review-engineering-slice is the only reviewer provider this seam has, and it
// records one independent automation actor's typed fact.  Nothing anywhere
// checks that a reviewer is a human, so accepting independent_human would seal
// a requirement into an immutable plan that no code can ever satisfy or refuse.
// The unsupported class is refused until a human-review provider exists, the
// same way Claude execution is refused until its launcher exists.
const REVIEWER_CLASSES = new Set(["independent_agent"]);
const EVIDENCE_REDACTION_CLASSES = new Set(["metadata_only", "redacted_evidence"]);
const EVIDENCE_RETENTIONS = new Set(["ephemeral", "material_redacted"]);
const COMPLETION_VERIFIERS = new Set(["independent_review", "independent_review_and_manual_qa"]);
// Q063.D1 / Q122.D1: extend a proven deep module, or replace it cleanly; a new
// module needs a real seam and no plan may create two owners for one seam.
const SEAM_MODES = new Set(["reuse", "extend", "replace", "new_module"]);
const NEW_MODULE_JUSTIFICATIONS = new Set(["authority", "lifecycle", "failure_isolation", "multi_adapter"]);
const MEASUREMENT_BASES = new Set([
  "complexity_reduction", "defect_rate", "coverage", "latency", "operator_effort",
]);

function isIdentifierArray(value) {
  return Array.isArray(value) && value.every(item => typeof item === "string" && ID.test(item));
}

function refuseSelfLabel(value, field, sliceRef, ToolError) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  const found = Object.keys(value).filter(key => SELF_LABEL_FIELDS.has(key)).sort();
  if (found.length)
    error(ToolError, { error: "engineering_design_depth_self_label_forbidden", field, slice_ref: sliceRef, fields: found });
}

/**
 * Return the exact bound Q035.D1 classifier inputs for one accepted slice.
 *
 * Cardinality is the only thing read from the counted closed arrays, and the
 * planned check count is never returned, so extra verification can never be
 * penalised by the classifier.
 */
export function designDepthInputs(slice, ToolError) {
  if (!slice || typeof slice !== "object" || Array.isArray(slice))
    error(ToolError, { error: "engineering_design_depth_input_invalid" });
  refuseSelfLabel(slice, "slice", slice.slice_ref, ToolError);
  for (const field of DESIGN_DEPTH_INPUT_FIELDS)
    if (!Object.hasOwn(slice, field))
      error(ToolError, { error: "engineering_design_depth_input_missing", field, slice_ref: slice.slice_ref });
  if (!/^R[0-6]$/.test(slice.risk_class) || !CONCURRENCY_POSTURES.has(slice.concurrency_posture) ||
      typeof slice.manual_qa_required !== "boolean" || !RELEASE_REQUIREMENTS.has(slice.release_requirement))
    error(ToolError, { error: "engineering_design_depth_input_invalid", slice_ref: slice.slice_ref });
  const counts = {};
  for (const field of DESIGN_DEPTH_COUNTED_ARRAYS) {
    if (!isIdentifierArray(slice[field]))
      error(ToolError, { error: "engineering_design_depth_input_invalid", field, slice_ref: slice.slice_ref });
    counts[field] = slice[field].length;
  }
  return Object.freeze({
    risk_class: slice.risk_class,
    concurrency_posture: slice.concurrency_posture,
    manual_qa_required: slice.manual_qa_required,
    release_requirement: slice.release_requirement,
    dependency_count: counts.dependency_refs,
    declared_resource_count: counts.declared_resource_refs,
    declared_component_count: counts.declared_component_refs,
    declared_plan_step_count: counts.declared_plan_step_refs,
  });
}

/**
 * The deterministic Q035.D1 design-depth classifier.
 *
 * SHORT requires every accepted condition: R0-R3, parallel-safe, no manual QA,
 * no release requirement, zero dependencies, and at most one declared resource,
 * component and plan step.  Every other valid combination is FULL, and an
 * invalid slice never reaches this function at all.
 *
 * SHORT changes design-template depth only.  It grants no action authority,
 * waives no R0-R6 operating gate, reduces no verification and activates no
 * effect; ordinary attended source delivery still follows real effects.
 *
 * The predicate is selected by the slice's own sealed contract_version, so a
 * stored contract keeps the exact predicate it was accepted under.
 */
export function classifyDesignDepth(slice, ToolError, contractVersion = ENGINEERING_DESIGN_CONTRACT_VERSION) {
  const row = designDepthInputs(slice, ToolError);
  const predicate = Object.hasOwn(DESIGN_DEPTH_PREDICATES, contractVersion)
    ? DESIGN_DEPTH_PREDICATES[contractVersion] : null;
  if (!predicate)
    error(ToolError, {
      error: "engineering_design_depth_predicate_unsupported", slice_ref: slice.slice_ref,
      contract_version: contractVersion ?? null, supported: [...ENGINEERING_DESIGN_DEPTH_PREDICATE_VERSIONS],
    });
  return predicate(row) ? "short" : "full";
}

function requireSelectionBasis(value, field, sliceRef, ToolError) {
  const fail = reason => error(ToolError, { error: reason, field, slice_ref: sliceRef });
  if (!Array.isArray(value) || !value.length || value.some(item => typeof item !== "string") ||
      new Set(value).size !== value.length || value.some(item => !SELECTION_BASIS_VALUES.has(item)))
    fail("engineering_design_contract_field_invalid");
  if (value.length === 1 && value[0] === "cost") fail("engineering_design_cost_only_selection");
  return value;
}

function requireModelJudgmentSteps(slice, decision, ToolError) {
  const sliceRef = slice.slice_ref;
  const fail = (reason, field) => error(ToolError, { error: reason, field, slice_ref: sliceRef });
  const steps = decision.model_judgment_steps;
  if (!Array.isArray(steps)) fail("engineering_design_contract_field_invalid", "code_model_decision.model_judgment_steps");
  const seen = new Set();
  for (const step of steps) {
    if (!exactObject(step, MODEL_STEP_FIELDS)) fail("engineering_design_model_step_invalid", "model_judgment_steps");
    if (typeof step.step_ref !== "string" || !ID.test(step.step_ref) || seen.has(step.step_ref) ||
        !slice.declared_plan_step_refs.includes(step.step_ref))
      fail("engineering_design_model_step_invalid", "model_judgment_steps.step_ref");
    seen.add(step.step_ref);
    if (RESERVED_CODE_RESPONSIBILITIES.has(step.responsibility_class))
      error(ToolError, {
        error: "engineering_design_model_step_reserved_responsibility", slice_ref: sliceRef,
        field: "model_judgment_steps.responsibility_class", responsibility_class: step.responsibility_class,
        resolution: "deterministic code owns identity, policy, permissions, state, validation, idempotency and execution",
      });
    if (!TYPED_UNCERTAINTY_CLASSES.has(step.responsibility_class))
      fail("engineering_design_model_step_invalid", "model_judgment_steps.responsibility_class");
    for (const field of ["input_contract_ref", "output_contract_ref"])
      if (typeof step[field] !== "string" || !ID.test(step[field]))
        fail("engineering_design_model_step_invalid", `model_judgment_steps.${field}`);
    if (!nonEmptyText(step.rationale)) fail("engineering_design_model_step_invalid", "model_judgment_steps.rationale");
    requireSelectionBasis(step.selection_basis, "model_judgment_steps.selection_basis", sliceRef, ToolError);
  }
  return steps;
}

function requireDesignDepthMaterial(slice, contract, ToolError) {
  const sliceRef = slice.slice_ref;
  const depth = classifyDesignDepth(slice, ToolError, contract.contract_version);
  const fail = field => error(ToolError, {
    error: "engineering_design_depth_material_invalid", field, slice_ref: sliceRef, design_depth: depth,
  });
  if (depth === "full") {
    if (contract.short_template !== null) fail("short_template");
    const refs = contract.full_design_refs;
    if (!exactObject(refs, FULL_DESIGN_REF_FIELDS)) fail("full_design_refs");
    for (const field of ["design_interview_ref", "authority_envelope_ref", "failure_model_ref", "oracle_ref"])
      if (typeof refs[field] !== "string" || !ID.test(refs[field])) fail(`full_design_refs.${field}`);
    if (!isUniqueIdentifierArray(refs.fixture_refs) || !refs.fixture_refs.length) fail("full_design_refs.fixture_refs");
    return depth;
  }
  if (contract.full_design_refs !== null) fail("full_design_refs");
  const template = contract.short_template;
  if (!exactObject(template, SHORT_TEMPLATE_FIELDS)) fail("short_template");
  for (const field of ["template_ref", "verification_ref"])
    if (typeof template[field] !== "string" || !ID.test(template[field])) fail(`short_template.${field}`);
  if (!nonEmptyText(template.objective_summary)) fail("short_template.objective_summary");
  return depth;
}

/** Validate the closed Q046.D1 slice contract for one accepted v2 slice. */
function requireDesignContract(slice, ToolError) {
  const sliceRef = slice.slice_ref;
  const contract = slice.design_contract;
  const fail = (reason, field) => error(ToolError, { error: reason, field, slice_ref: sliceRef });
  const bad = field => fail("engineering_design_contract_field_invalid", field);
  refuseSelfLabel(contract, "design_contract", sliceRef, ToolError);
  if (!exactObject(contract, DESIGN_CONTRACT_FIELDS)) fail("engineering_design_contract_invalid", "design_contract");
  if (contract.contract_version !== ENGINEERING_DESIGN_CONTRACT_VERSION)
    fail("engineering_design_contract_version_invalid", "contract_version");
  for (const field of ["rationale", "dependency_rationale"]) if (!nonEmptyText(contract[field])) bad(field);

  const decision = contract.code_model_decision;
  if (!exactObject(decision, ["model_judgment_steps", "rationale", "selection_basis"])) bad("code_model_decision");
  if (!nonEmptyText(decision.rationale)) bad("code_model_decision.rationale");
  requireSelectionBasis(decision.selection_basis, "code_model_decision.selection_basis", sliceRef, ToolError);
  const steps = requireModelJudgmentSteps(slice, decision, ToolError);

  const routing = contract.routing;
  if (!exactObject(routing, ["adapter_ref", "executor_class", "fresh_session_required"])) bad("routing");
  if (!EXECUTOR_CLASSES.has(routing.executor_class)) bad("routing.executor_class");
  if (typeof routing.adapter_ref !== "string" || !ID.test(routing.adapter_ref)) bad("routing.adapter_ref");
  if (routing.fresh_session_required !== true) bad("routing.fresh_session_required");
  if (routing.executor_class === "deterministic_code" && steps.length) bad("routing.executor_class");
  if (routing.executor_class === "model_assisted" && !steps.length) bad("routing.executor_class");

  const authority = contract.authority;
  if (!exactObject(authority, ["capability_profile", "environment", "read_only"])) bad("authority");
  if (typeof authority.capability_profile !== "string" || !ID.test(authority.capability_profile)) bad("authority.capability_profile");
  if (typeof authority.read_only !== "boolean") bad("authority.read_only");
  if (!AUTHORITY_ENVIRONMENTS.has(authority.environment)) bad("authority.environment");
  if (!authority.read_only && authority.capability_profile !== "capability:engineering-repository-write")
    bad("authority.capability_profile");

  const isolation = contract.isolation;
  if (!exactObject(isolation, ["branch_required", "shared_resource_refs", "worktree_required"])) bad("isolation");
  if (isolation.worktree_required !== true || isolation.branch_required !== true) bad("isolation.worktree_required");
  if (!isUniqueIdentifierArray(isolation.shared_resource_refs)) bad("isolation.shared_resource_refs");
  if (isolation.shared_resource_refs.some(ref => !slice.declared_resource_refs.includes(ref)))
    bad("isolation.shared_resource_refs");
  if (isolation.shared_resource_refs.length && slice.concurrency_posture === "parallel_safe")
    bad("isolation.shared_resource_refs");

  const tests = contract.tests;
  if (!exactObject(tests, ["planned_check_refs", "verification_lanes"])) bad("tests");
  const plannedRefs = slice.planned_checks.map(check => check.check_ref);
  if (!isUniqueIdentifierArray(tests.planned_check_refs) ||
      tests.planned_check_refs.join(",") !== plannedRefs.join(",")) bad("tests.planned_check_refs");
  if (!Array.isArray(tests.verification_lanes) || !tests.verification_lanes.length ||
      new Set(tests.verification_lanes).size !== tests.verification_lanes.length ||
      tests.verification_lanes.some(lane => !VERIFICATION_LANES.has(lane))) bad("tests.verification_lanes");
  if (tests.verification_lanes.includes("manual_qa") !== slice.manual_qa_required) bad("tests.verification_lanes");

  const review = contract.review;
  if (!exactObject(review, ["independent_review_required", "reviewer_class"])) bad("review");
  if (review.independent_review_required !== true) bad("review.independent_review_required");
  if (!REVIEWER_CLASSES.has(review.reviewer_class)) bad("review.reviewer_class");

  const failure = contract.failure;
  if (!exactObject(failure, ["failure_modes"])) bad("failure");
  if (!Array.isArray(failure.failure_modes) || !failure.failure_modes.length) bad("failure.failure_modes");
  const failureRefs = new Set();
  for (const mode of failure.failure_modes) {
    if (!exactObject(mode, ["compensation", "detection", "failure_ref"])) bad("failure.failure_modes");
    if (typeof mode.failure_ref !== "string" || !ID.test(mode.failure_ref) || failureRefs.has(mode.failure_ref))
      bad("failure.failure_modes.failure_ref");
    failureRefs.add(mode.failure_ref);
    for (const field of ["detection", "compensation"])
      if (!nonEmptyText(mode[field])) bad(`failure.failure_modes.${field}`);
  }

  const evidenceFacet = contract.evidence;
  if (!exactObject(evidenceFacet, ["evidence_refs", "redaction_class", "retention"])) bad("evidence");
  if (!EVIDENCE_REDACTION_CLASSES.has(evidenceFacet.redaction_class)) bad("evidence.redaction_class");
  if (!EVIDENCE_RETENTIONS.has(evidenceFacet.retention)) bad("evidence.retention");
  if (!isTypedEvidenceArray(evidenceFacet.evidence_refs)) bad("evidence.evidence_refs");
  if (evidenceFacet.evidence_refs.some(item => item.redaction_class !== evidenceFacet.redaction_class))
    bad("evidence.evidence_refs");
  if (slice.planned_checks.some(check => check.evidence_requirement === "redacted_evidence_required") &&
      evidenceFacet.redaction_class !== "redacted_evidence") bad("evidence.redaction_class");

  const deployment = contract.deployment;
  if (!exactObject(deployment, ["confirmation_required", "release_requirement", "rollback_ref"])) bad("deployment");
  if (deployment.release_requirement !== slice.release_requirement) bad("deployment.release_requirement");
  if (deployment.release_requirement === "required"
    ? (typeof deployment.rollback_ref !== "string" || !ID.test(deployment.rollback_ref))
    : (deployment.rollback_ref !== null && (typeof deployment.rollback_ref !== "string" || !ID.test(deployment.rollback_ref))))
    bad("deployment.rollback_ref");
  if (typeof deployment.confirmation_required !== "boolean") bad("deployment.confirmation_required");
  if (!["R0", "R1"].includes(slice.risk_class) && deployment.confirmation_required !== true)
    bad("deployment.confirmation_required");

  const completion = contract.completion;
  if (!exactObject(completion, ["completion_predicate", "verified_by"])) bad("completion");
  if (!nonEmptyText(completion.completion_predicate)) bad("completion.completion_predicate");
  if (!COMPLETION_VERIFIERS.has(completion.verified_by)) bad("completion.verified_by");
  if (completion.verified_by !== (slice.manual_qa_required ? "independent_review_and_manual_qa" : "independent_review"))
    bad("completion.verified_by");

  const seam = contract.seam_decision;
  if (!exactObject(seam, [
    "measurement", "mode", "new_module_justification", "replaced_seam_refs",
    "residual_authority_refs", "target_seam_ref",
  ])) fail("engineering_design_seam_invalid", "seam_decision");
  if (!SEAM_MODES.has(seam.mode)) fail("engineering_design_seam_invalid", "seam_decision.mode");
  if (typeof seam.target_seam_ref !== "string" || !ID.test(seam.target_seam_ref))
    fail("engineering_design_seam_invalid", "seam_decision.target_seam_ref");
  if (!exactObject(seam.measurement, ["basis", "note"]) || !MEASUREMENT_BASES.has(seam.measurement.basis) ||
      !nonEmptyText(seam.measurement.note)) fail("engineering_design_seam_invalid", "seam_decision.measurement");
  if (!isUniqueIdentifierArray(seam.replaced_seam_refs)) fail("engineering_design_seam_invalid", "seam_decision.replaced_seam_refs");
  if (!isUniqueIdentifierArray(seam.residual_authority_refs)) fail("engineering_design_seam_invalid", "seam_decision.residual_authority_refs");
  if (seam.mode === "new_module") {
    if (!NEW_MODULE_JUSTIFICATIONS.has(seam.new_module_justification))
      error(ToolError, {
        error: "engineering_design_seam_invalid", slice_ref: sliceRef, field: "seam_decision.new_module_justification",
        resolution: "create a module only for a real authority, lifecycle, failure-isolation or multi-adapter seam",
      });
  } else if (seam.new_module_justification !== null) {
    fail("engineering_design_seam_invalid", "seam_decision.new_module_justification");
  }
  if (seam.mode === "replace") {
    if (!seam.replaced_seam_refs.length || seam.replaced_seam_refs.includes(seam.target_seam_ref))
      fail("engineering_design_seam_invalid", "seam_decision.replaced_seam_refs");
    if (seam.residual_authority_refs.length)
      fail("engineering_design_seam_half_replacement", "seam_decision.residual_authority_refs");
  } else {
    if (seam.replaced_seam_refs.length) fail("engineering_design_seam_invalid", "seam_decision.replaced_seam_refs");
    if (seam.residual_authority_refs.length)
      fail("engineering_design_seam_duplicate_authority", "seam_decision.residual_authority_refs");
  }

  return requireDesignDepthMaterial(slice, contract, ToolError);
}

/** Refuse duplicate seam authority and half-replacement across one plan. */
function requireSeamAuthority(plan, ToolError) {
  const owners = new Map();
  const retired = new Map();
  for (const slice of plan.slices) {
    const seam = slice.design_contract.seam_decision;
    if (["new_module", "replace"].includes(seam.mode)) {
      if (owners.has(seam.target_seam_ref))
        error(ToolError, {
          error: "engineering_design_seam_duplicate_authority", seam_ref: seam.target_seam_ref,
          slice_ref: slice.slice_ref, owning_slice_ref: owners.get(seam.target_seam_ref),
        });
      owners.set(seam.target_seam_ref, slice.slice_ref);
    }
    for (const seamRef of seam.replaced_seam_refs) {
      if (retired.has(seamRef))
        error(ToolError, {
          error: "engineering_design_seam_duplicate_authority", seam_ref: seamRef,
          slice_ref: slice.slice_ref, owning_slice_ref: retired.get(seamRef),
        });
      retired.set(seamRef, slice.slice_ref);
    }
  }
  for (const slice of plan.slices) {
    const seam = slice.design_contract.seam_decision;
    if (["reuse", "extend"].includes(seam.mode) && retired.has(seam.target_seam_ref))
      error(ToolError, {
        error: "engineering_design_seam_half_replacement", seam_ref: seam.target_seam_ref,
        slice_ref: slice.slice_ref, retiring_slice_ref: retired.get(seam.target_seam_ref),
      });
  }
}

/**
 * Refuse a dependency cycle, including a slice that depends on itself.
 *
 * The portable validator has always refused cycles
 * (engineering_passport._assert_acyclic); the server validator did not, so a
 * plan the two disagreed about could register.  A cycle also makes closure
 * unreachable by construction: dependenciesSatisfied can never be met for any
 * slice on the cycle, so the immutable plan would pin them at blocked forever.
 *
 * This is a NEW server-side refusal, so requirePlan runs it for
 * engineering-slice-plan.v2 only.  An append-only v1 plan this validator already
 * accepted is revalidated on every read; refusing it now would strand it rather
 * than fix it.  Such a v1 plan keeps its exact previous behavior: readable, and
 * blocked forever on the cycle, exactly as before.
 */
function requireAcyclicDependencies(plan, ToolError) {
  const graph = new Map(plan.slices.map(row => [row.slice_ref, row.dependency_refs || []]));
  const visiting = new Set();
  const visited = new Set();
  const visit = ref => {
    if (visiting.has(ref)) error(ToolError, { error: "engineering_slice_dependency_cycle", slice_ref: ref });
    if (visited.has(ref)) return;
    visiting.add(ref);
    for (const dependency of graph.get(ref) || []) visit(dependency);
    visiting.delete(ref);
    visited.add(ref);
  };
  for (const ref of graph.keys()) visit(ref);
}

/** Every slice a slice depends on, directly or transitively. */
function transitiveDependencies(plan) {
  const direct = new Map(plan.slices.map(row => [row.slice_ref, row.dependency_refs || []]));
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

/**
 * Refuse two concurrently admissible slices that claim the same declared
 * resource.
 *
 * declared_resource_refs are the mutable resources the envelope binds by
 * revision under compare_and_swap_required, not free-form references, and a
 * parallel_safe slice has already had to state isolation.shared_resource_refs
 * as empty -- "nothing I touch is shared".  Two such slices naming one resource
 * are therefore two contradictory statements sealed inside one plan, and both
 * become eligible at once.  A dependency edge between them removes the
 * contradiction outright, because the dependent slice cannot be admitted until
 * the other is verified complete, so ordered work is left alone.  Nothing here
 * restricts a serial or exclusive posture, which is how contention is meant to
 * be declared, and v1 plans keep their exact previous behavior.
 */
function requireParallelResourceIsolation(plan, ToolError) {
  const ordered = transitiveDependencies(plan);
  const owners = new Map();
  for (const slice of plan.slices) {
    if (slice.concurrency_posture !== "parallel_safe") continue;
    // declared_resource_refs is not required to be unique, and one slice
    // repeating its own resource is not contention with anyone.
    for (const resourceRef of new Set(slice.declared_resource_refs)) {
      for (const other of owners.get(resourceRef) || []) {
        if (ordered.get(slice.slice_ref)?.has(other) || ordered.get(other)?.has(slice.slice_ref)) continue;
        error(ToolError, {
          error: "engineering_design_parallel_resource_conflict", resource_ref: resourceRef,
          slice_ref: slice.slice_ref, conflicting_slice_ref: other,
          resolution: "two parallel-safe slices cannot both own one declared resource; order them with a dependency, or declare the contention with a serial_after_dependencies or exclusive_resource posture",
        });
      }
      owners.set(resourceRef, [...(owners.get(resourceRef) || []), slice.slice_ref]);
    }
  }
}

export function requirePlan(plan, ToolError) {
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) error(ToolError, { error: "engineering_slice_plan_invalid" });
  if (Object.keys(plan).sort().join(",") !== "accepted_plan_revision,plan_digest,schema_version,slices,work_request") error(ToolError, { error: "engineering_slice_plan_unknown_field" });
  for (const key of ["schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slices"])
    if (!(key in plan)) error(ToolError, { error: "engineering_slice_plan_missing_field", field: key });
  if (!ENGINEERING_SLICE_PLAN_VERSIONS.includes(plan.schema_version))
    error(ToolError, {
      error: "engineering_slice_plan_schema_invalid", schema_version: plan.schema_version ?? null,
      supported: [...ENGINEERING_SLICE_PLAN_VERSIONS],
    });
  // Successor plans admit identifiers exactly as written; a legacy v1 plan keeps
  // the trimming acceptance it was registered under, because every read path
  // revalidates the stored append-only row.  See the divergence note above.
  const identifier = plan.schema_version === SLICE_PLAN_V2 ? exactId : id;
  const binding = plan.work_request;
  if (!binding || typeof binding !== "object" || Array.isArray(binding) || Object.keys(binding).sort().join(",") !== "canonical_record_digest,id,state_version")
    error(ToolError, { error: "engineering_slice_plan_work_binding_invalid" });
  identifier(binding.id, "work_request.id", ToolError); digest(binding.canonical_record_digest, "work_request.canonical_record_digest", ToolError);
  if (!Number.isInteger(binding.state_version) || binding.state_version < 1) error(ToolError, { error: "engineering_slice_plan_state_version_invalid" });
  const revision = plan.accepted_plan_revision;
  if (!revision || typeof revision !== "object" || Array.isArray(revision) || Object.keys(revision).sort().join(",") !== "digest,id,revision")
    error(ToolError, { error: "engineering_slice_plan_revision_invalid" });
  identifier(revision.id, "accepted_plan_revision.id", ToolError); digest(revision.digest, "accepted_plan_revision.digest", ToolError);
  if (!Number.isInteger(revision.revision) || revision.revision < 1) error(ToolError, { error: "engineering_slice_plan_revision_invalid" });
  digest(plan.plan_digest, "plan_digest", ToolError);
  if (!Array.isArray(plan.slices) || plan.slices.length < 1) error(ToolError, { error: "engineering_slice_plan_empty" });
  const refs = new Set();
  const ordinals = new Set();
  for (const slice of plan.slices) {
    const required = ["baseline_evidence_refs", "concurrency_posture", "declared_component_refs", "declared_plan_step_refs", "declared_resource_refs", "definition_of_done", "dependency_refs", "forbidden_change_refs", "manual_qa_required", "objective", "ordinal", "planned_checks", "release_requirement", "risk_class", "scope_boundary", "slice_ref"];
    if (plan.schema_version === SLICE_PLAN_V2) required.push("design_contract");
    required.sort();
    refuseSelfLabel(slice, "slice", slice?.slice_ref, ToolError);
    if (!slice || typeof slice !== "object" || Array.isArray(slice) || Object.keys(slice).sort().join(",") !== required.join(",")) error(ToolError, { error: "engineering_slice_schema_invalid", slice_ref: slice?.slice_ref });
    identifier(slice.slice_ref, "slice_ref", ToolError);
    if (!Number.isInteger(slice.ordinal) || slice.ordinal < 1 || typeof slice.objective !== "string" || !slice.objective.trim() || typeof slice.definition_of_done !== "string" || !slice.definition_of_done.trim() || typeof slice.scope_boundary !== "string" || !slice.scope_boundary.trim()) error(ToolError, { error: "engineering_slice_fields_invalid", slice_ref: slice.slice_ref });
    if (!CONCURRENCY_POSTURES.has(slice.concurrency_posture) || !/^R[0-6]$/.test(slice.risk_class) || !RELEASE_REQUIREMENTS.has(slice.release_requirement) || typeof slice.manual_qa_required !== "boolean") error(ToolError, { error: "engineering_slice_enum_invalid", slice_ref: slice.slice_ref });
    if (refs.has(slice.slice_ref)) error(ToolError, { error: "engineering_slice_duplicate", slice_ref: slice.slice_ref });
    refs.add(slice.slice_ref);
    // The portable validator has always required unique ordinals; without this
    // the two validators disagreed about the same plan.  Like the cycle check,
    // this is a new server-side refusal and therefore binds the successor
    // version only: an append-only v1 plan already accepted with duplicate
    // ordinals must stay readable on every read path.
    if (plan.schema_version === SLICE_PLAN_V2 && ordinals.has(slice.ordinal))
      error(ToolError, { error: "engineering_slice_ordinal_duplicate", slice_ref: slice.slice_ref, ordinal: slice.ordinal });
    ordinals.add(slice.ordinal);
    if (!Array.isArray(slice.dependency_refs) || slice.dependency_refs.some(ref => !refs.has(ref) && !plan.slices.some(candidate => candidate.slice_ref === ref)))
      error(ToolError, { error: "engineering_slice_dependency_unknown", slice_ref: slice.slice_ref });
    for (const field of ["baseline_evidence_refs", "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs", "forbidden_change_refs", "dependency_refs"])
      if (!Array.isArray(slice[field])) error(ToolError, { error: "engineering_slice_array_invalid", field, slice_ref: slice.slice_ref });
    for (const [index, item] of slice.baseline_evidence_refs.entries()) evidence(item, `baseline_evidence_refs[${index}]`, ToolError, identifier);
    for (const field of ["declared_resource_refs", "declared_component_refs", "declared_plan_step_refs", "forbidden_change_refs", "dependency_refs"])
      for (const [index, item] of slice[field].entries()) identifier(item, `${field}[${index}]`, ToolError);
    const checkRefs = new Set();
    if (!Array.isArray(slice.planned_checks) || slice.planned_checks.length < 1 || slice.planned_checks.some(check => !check || typeof check !== "object" || Object.keys(check).sort().join(",") !== "check_ref,evidence_requirement,failure_condition" || !identifier(check.check_ref, "planned_checks.check_ref", ToolError) || checkRefs.has(check.check_ref) || !checkRefs.add(check.check_ref) || typeof check.failure_condition !== "string" || !check.failure_condition.trim() || !EVIDENCE_REQUIREMENTS.has(check.evidence_requirement))) error(ToolError, { error: "engineering_slice_checks_invalid", slice_ref: slice.slice_ref });
    if (plan.schema_version === SLICE_PLAN_V2) requireDesignContract(slice, ToolError);
  }
  if (plan.schema_version === SLICE_PLAN_V2) {
    requireAcyclicDependencies(plan, ToolError);
    requireSeamAuthority(plan, ToolError);
    requireParallelResourceIsolation(plan, ToolError);
  }
  if (canonicalDigest(Object.fromEntries(Object.entries(plan).filter(([key]) => key !== "plan_digest"))) !== plan.plan_digest)
    error(ToolError, { error: "engineering_slice_plan_digest_mismatch" });
  return plan;
}

function sourceParts(source, ToolError) {
  if (!source || typeof source !== "object" || !source.work_request || !source.accepted_plan)
    error(ToolError, { error: "engineering_admission_source_missing" });
  const work = source.work_request;
  const plan = source.accepted_plan;
  id(work.id, "work_request.id", ToolError);
  if (!Number.isInteger(Number(work.version)) || Number(work.version) < 1)
    error(ToolError, { error: "engineering_work_request_version_invalid" });
  digest(work.canonical_record_digest, "work_request.canonical_record_digest", ToolError);
  id(plan.plan_ref, "accepted_plan.plan_ref", ToolError);
  digest(plan.digest, "accepted_plan.digest", ToolError);
  return { work, plan };
}

// A successor may deliberately preserve an old accepted-plan row for audit and
// amendment-predecessor reads while making its executable contract permanently
// unavailable.  Keep that distinction at the ingress boundary: a historical
// passport remains readable, but no admission, claim, receipt append, or merge
// path can accidentally turn the retained row back into an executable plan.
//
// 0532a is the authority for the exact stale-contract decision.  This adapter
// only consumes its server-derived projection; it never infers retirement from
// a Work Request label, a plan name, or a bootstrap/Gate-A record.
function requireExecutableSource(source, ToolError) {
  if (source?.execution_authorized === false) {
    error(ToolError, {
      error: "engineering_execution_contract_retired",
      reason: typeof source.execution_refusal_reason === "string"
        ? source.execution_refusal_reason : "server-derived accepted-plan execution fence",
    });
  }
}

function sourcePlanRow(facts, source, ToolError) {
  const row = (facts.slice_plans || []).find(item =>
    item.accepted_plan_id === source.plan.record_id && item.accepted_plan_hash === source.plan.digest);
  if (!row) error(ToolError, { error: "engineering_slice_plan_not_registered", accepted_plan: source.plan.plan_ref });
  return row;
}

function planMatchesCurrentSource(plan, source) {
  return plan.work_request.id === source.work.id &&
    plan.work_request.state_version === Number(source.work.version) &&
    plan.work_request.canonical_record_digest === source.work.canonical_record_digest &&
    plan.accepted_plan_revision.id === source.plan.plan_ref &&
    plan.accepted_plan_revision.revision === Number(source.plan.revision) &&
    plan.accepted_plan_revision.digest === source.plan.digest;
}

function sourcePlan(facts, source, ToolError) {
  const row = sourcePlanRow(facts, source, ToolError);
  const plan = requirePlan(row.plan, ToolError);
  if (!planMatchesCurrentSource(plan, source)) error(ToolError, { error: "engineering_slice_plan_currentness_mismatch" });
  return plan;
}

function sliceFor(plan, sliceRef, ToolError) {
  const row = plan.slices.find(item => item.slice_ref === sliceRef);
  if (!row) error(ToolError, { error: "engineering_slice_not_found", slice_ref: sliceRef });
  return row;
}

function receiptLedgerRows(facts) {
  return (facts?.receipts || []).filter(row => row && typeof row === "object" &&
    typeof row.id === "string" && typeof row.envelope_id === "string");
}

function canonicalReceiptRows(facts) {
  return receiptLedgerRows(facts).filter(row => row.receipt && typeof row.receipt === "object" && !Array.isArray(row.receipt));
}

function canonicalReviewerRows(facts) {
  return (facts?.reviewer_facts || []).filter(row => row && typeof row === "object" &&
    typeof row.id === "string" && typeof row.receipt_id === "string" &&
    row.fact && typeof row.fact === "object" && !Array.isArray(row.fact));
}

function canonicalEnvelopeRows(facts) {
  return (facts?.envelopes || []).filter(row => row && typeof row === "object" && typeof row.id === "string");
}

function exactObject(value, fields) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...fields].sort().join(","));
}

function nonEmptyText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isTypedEvidenceRef(value) {
  return exactObject(value, ["content_digest", "redaction_class", "ref"]) &&
    typeof value.ref === "string" && ID.test(value.ref) &&
    typeof value.content_digest === "string" && DIGEST.test(value.content_digest) &&
    ["metadata_only", "redacted_evidence"].includes(value.redaction_class);
}

function isUniqueIdentifierArray(value) {
  return Array.isArray(value) && value.every(item => typeof item === "string" && ID.test(item)) &&
    new Set(value).size === value.length;
}

function isTypedEvidenceArray(value, nonEmpty = false) {
  return Array.isArray(value) && (!nonEmpty || value.length > 0) && value.every(isTypedEvidenceRef);
}

function isCanonicalReviewerSessionRef(value) {
  return typeof value === "string" && value.startsWith("session:") && ID.test(value);
}

function sameIdentifierSet(left, right) {
  return isUniqueIdentifierArray(left) && isUniqueIdentifierArray(right) &&
    left.length === right.length && left.every(item => right.includes(item));
}

function isCanonicalReceiptCheck(value, requirePassed = false) {
  return exactObject(value, ["check_ref", "evidence_refs", "state"]) &&
    typeof value.check_ref === "string" && ID.test(value.check_ref) &&
    ["passed", "failed", "blocked", "not_run"].includes(value.state) &&
    isTypedEvidenceArray(value.evidence_refs, value.state === "passed") &&
    (!requirePassed || value.state === "passed");
}

function isCanonicalReceiptChecks(value, requirePassed = false) {
  return Array.isArray(value) && value.length > 0 && value.every(check =>
    isCanonicalReceiptCheck(check, requirePassed)) &&
    isUniqueIdentifierArray(value.map(check => check.check_ref));
}

function isCanonicalReceiptDeviation(value, requireResolved = false) {
  return exactObject(value, [
    "category", "deviation_ref", "evidence_refs", "impact", "out_of_scope_component_refs",
    "out_of_scope_resource_refs", "plan_revision_required", "reason", "review_state",
  ]) && typeof value.deviation_ref === "string" && ID.test(value.deviation_ref) &&
    ["category", "reason", "impact"].every(field => nonEmptyText(value[field])) &&
    typeof value.plan_revision_required === "boolean" &&
    isTypedEvidenceArray(value.evidence_refs) &&
    isUniqueIdentifierArray(value.out_of_scope_resource_refs) &&
    isUniqueIdentifierArray(value.out_of_scope_component_refs) &&
    ["unreviewed", "reviewed", "resolved"].includes(value.review_state) &&
    (!requireResolved || (value.review_state === "resolved" && value.plan_revision_required === false));
}

function isCanonicalReceiptDeviations(value, requireResolved = false) {
  return Array.isArray(value) && value.every(deviation =>
    isCanonicalReceiptDeviation(deviation, requireResolved)) &&
    isUniqueIdentifierArray(value.map(deviation => deviation.deviation_ref));
}

function canonicalReceiptDeviationRefs(receipt) {
  const deviations = receipt?.deviations;
  if (!isCanonicalReceiptDeviations(deviations)) return null;
  const refs = deviations.map(deviation => deviation?.deviation_ref);
  if (!isUniqueIdentifierArray(refs)) return null;
  return refs;
}

function resolvedReceiptDeviationRefs(receipt) {
  return isCanonicalReceiptDeviations(receipt?.deviations, true) ? canonicalReceiptDeviationRefs(receipt) : null;
}

function isCanonicalSourceEvidence(value) {
  return exactObject(value, ["branch_ref", "evidence_refs", "source_sha", "worktree_ref"]) &&
    ["worktree_ref", "branch_ref"].every(field => typeof value[field] === "string" && ID.test(value[field])) &&
    nonEmptyText(value.source_sha) && isTypedEvidenceArray(value.evidence_refs);
}

function isCanonicalResetReconstruction(value) {
  return exactObject(value, ["fresh_session", "inherited_transcript_used", "reconstruction_free", "remediation_action"]) &&
    value.fresh_session === true && value.inherited_transcript_used === false &&
    typeof value.reconstruction_free === "boolean" &&
    (value.reconstruction_free
      ? (value.remediation_action === null || nonEmptyText(value.remediation_action))
      : nonEmptyText(value.remediation_action));
}

function isCanonicalExecutorClaim(value) {
  return exactObject(value, ["claim_state", "claimed_at", "claimed_by"]) &&
    value.claim_state === "executor_claim" && typeof value.claimed_by === "string" && ID.test(value.claimed_by) &&
    nonEmptyText(value.claimed_at);
}

function isCanonicalReceiptAttribution(value, envelope) {
  const expected = envelope?.envelope;
  const actorRef = expected?.server_binding?.identity?.agent_principal_id;
  const sessionRef = expected?.agent_session?.id;
  const adapterRef = expected?.server_binding?.adapter?.adapter_id;
  return exactObject(value, ["actor_ref", "adapter_ref", "session_ref"]) &&
    [actorRef, sessionRef, adapterRef].every(item => typeof item === "string" && ID.test(item)) &&
    value.actor_ref === actorRef && value.session_ref === sessionRef && value.adapter_ref === adapterRef;
}

function isCanonicalReceiptScope(receipt, slice) {
  if (!slice || !sameIdentifierSet(receipt.planned_resource_refs, slice.declared_resource_refs) ||
      !sameIdentifierSet(receipt.planned_component_refs, slice.declared_component_refs) ||
      !isCanonicalReceiptChecks(receipt.checks)) return false;
  const expectedChecks = slice.planned_checks || [];
  const expectedCheckRefs = expectedChecks.map(check => check?.check_ref);
  if (!sameIdentifierSet(receipt.checks.map(check => check.check_ref), expectedCheckRefs)) return false;
  if (receipt.checks.some(check => check.state === "passed" && !check.evidence_refs.some(evidence =>
    evidence.redaction_class === (expectedChecks.find(planned => planned.check_ref === check.check_ref)?.evidence_requirement === "redacted_evidence_required"
      ? "redacted_evidence" : "metadata_only")))) return false;
  const resolvedDeviations = receipt.deviations.filter(deviation => deviation.review_state === "resolved");
  const resolvedResourceRefs = new Set(resolvedDeviations.flatMap(deviation => deviation.out_of_scope_resource_refs));
  const resolvedComponentRefs = new Set(resolvedDeviations.flatMap(deviation => deviation.out_of_scope_component_refs));
  return receipt.actual_resource_refs.every(ref => slice.declared_resource_refs.includes(ref) || resolvedResourceRefs.has(ref)) &&
    receipt.actual_component_refs.every(ref => slice.declared_component_refs.includes(ref) || resolvedComponentRefs.has(ref));
}

function isCanonicalReceiptPayload(row, envelope, plan, slice) {
  const receipt = row?.receipt;
  const requiredFields = [
    "actual_component_refs", "actual_resource_refs", "artifact_refs", "attribution", "attempt_id", "checks",
    "deviations", "envelope_digest", "evidence_refs", "executor_claim", "independent_verification_required",
    "outcome", "plan_digest", "planned_component_refs", "planned_resource_refs", "reset_reconstruction",
    "schema_version", "slice_ref", "source_evidence",
  ];
  return exactObject(receipt, requiredFields) && row.receipt_digest === canonicalDigest(receipt) &&
    receipt.schema_version === "engineering-slice-receipt.v1" &&
    typeof receipt.attempt_id === "string" && ID.test(receipt.attempt_id) &&
    receipt.attempt_id === row.attempt_id && receipt.outcome === row.outcome &&
    receipt.slice_ref === row.slice_ref && receipt.slice_ref === slice?.slice_ref && receipt.plan_digest === plan?.plan_digest &&
    receipt.envelope_digest === envelope?.envelope_digest && OUTCOMES.has(receipt.outcome) &&
    envelope?.envelope?.envelope_id === `env:${envelope.id}` &&
    envelope.envelope?.request?.job_ref === `job:${envelope.job_id}` &&
    envelope.envelope?.agent_session?.id === `session:${envelope.agent_session_id}` &&
    receipt.independent_verification_required === true &&
    row.executor_actor_active === true && typeof row.executor_actor_slug === "string" && ID.test(row.executor_actor_slug) &&
    isUniqueIdentifierArray(receipt.planned_resource_refs) && isUniqueIdentifierArray(receipt.actual_resource_refs) &&
    isUniqueIdentifierArray(receipt.planned_component_refs) && isUniqueIdentifierArray(receipt.actual_component_refs) &&
    isUniqueIdentifierArray(receipt.artifact_refs) && isTypedEvidenceArray(receipt.evidence_refs) &&
    isCanonicalReceiptDeviations(receipt.deviations) &&
    isCanonicalSourceEvidence(receipt.source_evidence) && isCanonicalResetReconstruction(receipt.reset_reconstruction) &&
    isCanonicalExecutorClaim(receipt.executor_claim) && receipt.executor_claim.claimed_by === row.executor_actor_slug &&
    isCanonicalReceiptAttribution(receipt.attribution, envelope) &&
    isCanonicalReceiptScope(receipt, slice);
}

function isCanonicalCompletedReceipt(row, envelope, plan, slice) {
  const receipt = row?.receipt;
  return isCanonicalReceiptPayload(row, envelope, plan, slice) && receipt.outcome === "claimed_complete" &&
    receipt.artifact_refs.length > 0 && isTypedEvidenceArray(receipt.evidence_refs, true) &&
    isCanonicalReceiptChecks(receipt.checks, true) && isCanonicalReceiptDeviations(receipt.deviations, true);
}

function isCanonicalReviewerFact(row, receiptRow, receiptDeviationRefs, requirePassed = false) {
  const fact = row?.fact;
  const slug = row?.reviewer_actor_slug;
  const validReviewerRef = typeof slug === "string" && ID.test(slug) &&
    [slug, `actor:${slug}`, `reviewer:${slug}`].includes(fact?.reviewer_ref);
  return exactObject(fact, [
    "attempt_id", "evidence_refs", "is_independent", "resolved_deviation_refs",
    "reviewed_deviation_refs", "reviewer_ref", "session_ref", "slice_ref", "state",
  ]) && row?.contract_version === "engineering-review.v1" && row?.reviewer_actor_active === true && validReviewerRef &&
    typeof row.reviewer_actor_id === "string" && row.reviewer_actor_id !== receiptRow.executor_actor_id &&
    fact.attempt_id === receiptRow.attempt_id && fact.slice_ref === receiptRow.slice_ref &&
    ["passed", "failed", "blocked"].includes(fact.state) && row.state === fact.state && fact.is_independent === true &&
    isTypedEvidenceArray(fact.evidence_refs, fact.state === "passed") && isCanonicalReviewerSessionRef(fact.session_ref) &&
    typeof row.reviewer_session_ref === "string" && row.reviewer_session_ref === fact.session_ref &&
    row.reviewer_session_ref !== receiptRow.receipt?.attribution?.session_ref && isCanonicalReviewerSessionRef(row.reviewer_session_ref) &&
    sameIdentifierSet(fact.reviewed_deviation_refs, receiptDeviationRefs) &&
    isUniqueIdentifierArray(fact.resolved_deviation_refs) && fact.resolved_deviation_refs.every(ref => receiptDeviationRefs.includes(ref)) &&
    (!requirePassed || (fact.state === "passed" && sameIdentifierSet(fact.resolved_deviation_refs, receiptDeviationRefs)));
}

function compareCanonicalGeneration(left, right) {
  const leftCreated = Date.parse(String(left?.created_at || ""));
  const rightCreated = Date.parse(String(right?.created_at || ""));
  const leftEpoch = Number.isFinite(leftCreated) ? leftCreated : Number.NEGATIVE_INFINITY;
  const rightEpoch = Number.isFinite(rightCreated) ? rightCreated : Number.NEGATIVE_INFINITY;
  return (leftEpoch === rightEpoch ? 0 : (leftEpoch < rightEpoch ? -1 : 1)) ||
    String(left?.id || "").localeCompare(String(right?.id || ""));
}

function latestReceiptForSlice(facts, source, plan, sliceRef, ToolError) {
  const slicePlan = sourcePlanRow(facts, source, ToolError);
  const slice = plan.slices.find(item => item?.slice_ref === sliceRef);
  if (slicePlan.plan?.plan_digest !== plan.plan_digest || typeof slicePlan.id !== "string" || !slice) return null;
  const workRequestId = source.work.id.replace(/^wr:/, "");
  const envelopes = canonicalEnvelopeRows(facts);
  const lineage = envelopes.filter(envelope => envelope.work_request_id === workRequestId &&
    envelope.slice_plan_id === slicePlan.id && envelope.slice_ref === sliceRef);
  const leaves = lineage.filter(envelope => !envelopes.some(successor =>
    successor.supersedes_envelope_id === envelope.id));
  // A receipt belongs to the one unsuperseded lineage leaf or it cannot carry
  // closure/dependency authority. Missing-receipt successors and malformed
  // forks therefore fence an older reviewed pass immediately.
  if (leaves.length !== 1) return null;
  const leaf = leaves[0];
  const matches = receiptLedgerRows(facts).filter(row => {
    return row.work_request_id === workRequestId && row.slice_ref === sliceRef &&
      row.envelope_id === leaf.id;
  });
  return matches.sort(compareCanonicalGeneration).at(-1) || null;
}

function exactPassedReviewForReceipt(facts, source, receiptRow, sliceRef) {
  const planRow = (facts?.slice_plans || []).find(row => row?.accepted_plan_id === source?.plan?.record_id &&
    row?.accepted_plan_hash === source?.plan?.digest);
  const plan = planRow?.plan;
  const slice = plan?.slices?.find(item => item?.slice_ref === sliceRef);
  const envelope = canonicalEnvelopeRows(facts).find(row => row.id === receiptRow?.envelope_id);
  const workRequestId = source.work.id.replace(/^wr:/, "");
  if (!receiptRow || !slice || receiptRow.work_request_id !== workRequestId || receiptRow.slice_ref !== sliceRef ||
      envelope?.id !== receiptRow.envelope_id || envelope.work_request_id !== workRequestId ||
      envelope.slice_plan_id !== planRow?.id || envelope.slice_ref !== sliceRef ||
      !isCanonicalCompletedReceipt(receiptRow, envelope, plan, slice)) return null;
  const receiptSessionRef = receiptRow.receipt?.attribution?.session_ref;
  const receiptDeviationRefs = resolvedReceiptDeviationRefs(receiptRow.receipt);
  if (typeof receiptRow.executor_actor_id !== "string" || typeof receiptSessionRef !== "string" ||
      !ID.test(receiptSessionRef) || !receiptDeviationRefs) return null;
  const matches = canonicalReviewerRows(facts).filter(row => {
    const fact = row.fact;
    return row.receipt_id === receiptRow.id && row.work_request_id === workRequestId &&
      row.slice_ref === sliceRef && row.state === "passed" &&
      typeof row.reviewer_actor_id === "string" && row.reviewer_actor_id !== receiptRow.executor_actor_id &&
      isCanonicalReviewerFact(row, receiptRow, receiptDeviationRefs, true) &&
      typeof row.reviewer_session_ref === "string" &&
      row.reviewer_session_ref === fact.session_ref && row.reviewer_session_ref !== receiptSessionRef &&
      isCanonicalReviewerSessionRef(row.reviewer_session_ref);
  });
  return matches.sort(compareCanonicalGeneration).at(-1) || null;
}

function dependenciesSatisfied(facts, source, plan, slice, ToolError) {
  const passed = new Set((slice.dependency_refs || []).filter(ref => {
    const receipt = latestReceiptForSlice(facts, source, plan, ref, ToolError);
    return Boolean(exactPassedReviewForReceipt(facts, source, receipt, ref));
  }));
  const missing = (slice.dependency_refs || []).filter(ref => !passed.has(ref));
  if (missing.length) error(ToolError, { error: "engineering_dependencies_not_verified", slice_ref: slice.slice_ref, missing_dependencies: missing });
  return true;
}

/**
 * The portfolio ancestor and predecessor check.
 *
 * WHETHER THIS APPLIES IS NOT THE CALLER'S TO SAY. The route is decided by
 * trusted stored source: ops.portfolio_descendant_binding verifies every current
 * accepted revision BEFORE it looks a node up, raises when one fails integrity,
 * and answers governed=false only when every accepted portfolio is intact and
 * none of them names this slice. There is deliberately no argument that turns
 * the check off.
 *
 * THE CHILD'S PLAN MUST BE THE PLAN BEING ADMITTED. A child carries the accepted
 * source binding that governs it. If that is not the exact accepted plan this
 * admission is running under, the ancestor does not authorize this work -- it
 * authorizes different work that happens to share a name. An earlier draft
 * returned the binding unchanged in that case, so a child bound to one accepted
 * plan silently governed admission under another.
 *
 * PREDECESSORS ARE CHECKED AGAINST THEIR OWN BINDING. Each predecessor carries
 * the child and accepted plan that govern IT. Proof is consumed only from that
 * exact plan: a passed receipt for a same-named slice under a different plan is
 * not proof of this predecessor, and treating it as proof is how a cross-plan
 * name collision becomes an admission.
 *
 * This slice deliberately builds NO portfolio outcome-receipt system: the
 * catalog excludes future outcome receipts and product execution from S00. So a
 * governed node whose predecessors have no existing, validly bound Engineering
 * proof is not admissible until that provider exists, and it refuses before any
 * job, session or envelope is written rather than admitting with a false flag.
 */
export async function portfolioAncestorBinding(c, facts, source, plan, sliceRef, ToolError) {
  const binding = (await c.query(
    "select ops.portfolio_descendant_binding($1::text) as binding", [sliceRef],
  )).rows[0]?.binding;
  if (!binding || binding.governed !== true) return null;

  // The ancestor must be complete before it can govern anything. Every field
  // below is inside the accepted digest, so a binding missing one is a binding
  // the partner never accepted.
  for (const field of ["portfolio_ref", "portfolio_revision_id", "accepted_digest",
    "child_ref", "child_version", "child_digest", "authority_class", "effect_class",
    "data_class", "budget_identity", "budget_ceiling", "model_floor", "recovery_ref",
    "terminal_predicate"]) {
    if (binding[field] === undefined || binding[field] === null) {
      error(ToolError, {
        error: "engineering_portfolio_ancestor_incomplete",
        slice_ref: sliceRef, missing_field: field,
      });
    }
  }

  const admittedPlanRef = source?.plan?.plan_ref ?? null;
  if (binding.child_accepted_plan_ref !== admittedPlanRef) {
    error(ToolError, {
      error: "engineering_portfolio_child_plan_mismatch",
      slice_ref: sliceRef,
      portfolio_ref: binding.portfolio_ref,
      child_ref: binding.child_ref,
      child_accepted_plan_ref: binding.child_accepted_plan_ref,
      admitted_plan_ref: admittedPlanRef,
      resolution: "the accepted child must be bound to the exact accepted plan this admission runs under; an unbound or differently bound child authorizes no admission",
    });
  }

  const predecessors = binding.predecessors || [];
  const verified = [];
  const unmet = [];
  for (const predecessor of predecessors) {
    const nodeRef = predecessor?.node_ref;
    // Proof may only come from the predecessor's OWN accepted plan. When that is
    // a different plan than the one being admitted, this transaction's facts
    // cannot speak to it at all, so it is unmet rather than assumed.
    if (!nodeRef || predecessor.accepted_plan_ref !== admittedPlanRef) {
      unmet.push({ node_ref: nodeRef ?? null, reason: "predecessor_plan_not_admitted",
        accepted_plan_ref: predecessor?.accepted_plan_ref ?? null });
      continue;
    }
    let review = null;
    let receipt = null;
    try {
      receipt = latestReceiptForSlice(facts, source, plan, nodeRef, ToolError);
      review = receipt && exactPassedReviewForReceipt(facts, source, receipt, nodeRef);
    } catch {
      review = null;
    }
    if (review) verified.push({ node_ref: nodeRef, child_ref: predecessor.child_ref,
      accepted_plan_ref: predecessor.accepted_plan_ref, attempt_id: receipt.attempt_id });
    else unmet.push({ node_ref: nodeRef, reason: "no_passed_independent_proof",
      accepted_plan_ref: predecessor.accepted_plan_ref });
  }
  if (unmet.length) {
    error(ToolError, {
      error: "engineering_portfolio_predecessor_proof_missing",
      slice_ref: sliceRef,
      portfolio_ref: binding.portfolio_ref,
      child_ref: binding.child_ref,
      unmet_predecessors: unmet,
      resolution: "an accepted-source predecessor is admissible only against an existing passed independent Engineering proof bound to that predecessor's own accepted plan; this slice builds no portfolio outcome-receipt provider",
    });
  }

  return Object.freeze({
    portfolio_ref: binding.portfolio_ref,
    portfolio_revision_id: binding.portfolio_revision_id,
    accepted_digest: binding.accepted_digest,
    child_ref: binding.child_ref,
    child_version: binding.child_version,
    child_digest: binding.child_digest,
    child_accepted_plan_ref: binding.child_accepted_plan_ref,
    node_ref: binding.node_ref,
    authority_class: binding.authority_class,
    effect_class: binding.effect_class,
    data_class: binding.data_class,
    budget_identity: binding.budget_identity,
    budget_ceiling: binding.budget_ceiling,
    model_floor: binding.model_floor,
    recovery_ref: binding.recovery_ref,
    terminal_predicate: binding.terminal_predicate,
    predecessors: Object.freeze(predecessors.map(item => Object.freeze({ ...item }))),
    // Reaching here means every predecessor was consumed from a passed
    // independent proof bound to its own accepted plan.
    verified_predecessors: Object.freeze(verified),
  });
}

function nowIso() { return new Date().toISOString().replace(/\.\d{3}Z$/, "Z"); }

function exactFutureInstant(value) {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(value)) return false;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) && new Date(parsed).toISOString().replace(/\.\d{3}Z$/, "Z") === value && parsed > Date.now();
}

function canonicalInstant(value) {
  const parsed = value instanceof Date ? value.getTime() : Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString().replace(/\.\d{3}Z$/, "Z") : null;
}

const ENGINEERING_SESSION_SOURCE = "0".repeat(40);
const ENGINEERING_SESSION_WORKTREE = "engineering:server-admission";
const ENGINEERING_CLAIM_LEASE_SECONDS = 960;

function hasDispatchRunway(value, minimumSeconds = ENGINEERING_CLAIM_LEASE_SECONDS) {
  const instant = Date.parse(value);
  return Number.isFinite(instant) && instant >= Date.now() + minimumSeconds * 1000;
}

function priorEnvelopeSessionId(priorEnvelope) {
  const fromRow = priorEnvelope?.agent_session_id;
  const fromEnvelope = priorEnvelope?.envelope?.agent_session?.id;
  const fromJson = typeof fromEnvelope === "string" && /^session:([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i.exec(fromEnvelope)?.[1];
  if (!fromJson || (fromRow && fromRow !== fromJson)) return null;
  return fromRow || fromJson;
}

function isExactEngineeringAdmissionSession(session, sliceRef, executorId) {
  return session?.executor_actor_id === executorId &&
    (session.state === "claimed" || session.state === "in_progress") &&
    session.scope_ref === `slice:${sliceRef}` &&
    session.worktree_ref === ENGINEERING_SESSION_WORKTREE &&
    session.source_commit_sha === ENGINEERING_SESSION_SOURCE;
}

// The exact execution binding this seam issues.  buildCodexEnvelope emits these
// values, the dispatch gate below re-checks them, and admission compares an
// accepted v2 design contract against them.  One statement of the actual
// authority, so the contract and the envelope cannot drift apart silently.
export const ENGINEERING_SERVER_EXECUTION_BINDING = Object.freeze({
  environment: "rehearsal",
  capability_profile: "capability:engineering-repository-write",
  read_only: false,
  adapter_ref: "adapter:codex-desktop",
});
// Codex is the only executable adapter here and runCodexSlice dispatches to it
// unconditionally, so an accepted contract routed to an attended human names an
// executor this seam cannot produce.
const ADMISSIBLE_EXECUTOR_CLASSES = new Set(["deterministic_code", "model_assisted"]);

/**
 * Refuse a v2 slice whose accepted contract contradicts the binding admission
 * is about to issue.
 *
 * routing and authority are validated at registration, sealed inside
 * plan_digest and rendered to operators through the passport, but nothing
 * downstream ever read them: the envelope's environment, capability profile,
 * write posture and adapter are server-derived constants.  A slice whose
 * accepted contract says read-only production work executed by a human was
 * therefore executed write-enabled in rehearsal by an automation model, with
 * both statements recorded as true.
 *
 * This compares and refuses only.  It never selects an executor, never widens
 * or reissues an envelope to match a contract, and grants nothing: a contract
 * that does not describe the existing binding simply cannot be admitted.  The
 * check is deliberately absent from the read paths, because refusing there
 * would make an already sealed passport unreadable.
 */
function requireServerExecutionBinding(plan, slice, ToolError) {
  if (plan.schema_version !== SLICE_PLAN_V2) return;
  const { routing, authority } = slice.design_contract;
  const fail = (field, declared, issued) => error(ToolError, {
    error: "engineering_design_contract_binding_mismatch", slice_ref: slice.slice_ref, field,
    declared_by_contract: declared === undefined ? null : declared, server_binding: issued,
    resolution: "the accepted design contract must describe the execution binding this server issues; admission refuses rather than reissuing authority to match a contract",
  });
  if (!ADMISSIBLE_EXECUTOR_CLASSES.has(routing.executor_class))
    fail("routing.executor_class", routing.executor_class, [...ADMISSIBLE_EXECUTOR_CLASSES]);
  if (routing.adapter_ref !== ENGINEERING_SERVER_EXECUTION_BINDING.adapter_ref)
    fail("routing.adapter_ref", routing.adapter_ref, ENGINEERING_SERVER_EXECUTION_BINDING.adapter_ref);
  for (const field of ["environment", "capability_profile", "read_only"])
    if (authority[field] !== ENGINEERING_SERVER_EXECUTION_BINDING[field])
      fail(`authority.${field}`, authority[field], ENGINEERING_SERVER_EXECUTION_BINDING[field]);
}

/**
 * The dispatch-side gate: does this row carry the exact write binding this seam
 * issues?
 *
 * environment is part of that binding, is emitted by buildCodexEnvelope, and is
 * already compared against an accepted v2 contract at admission
 * (requireServerExecutionBinding).  Reading every other field of the documented
 * binding while ignoring this one let an envelope naming a different environment
 * dispatch as if it were the rehearsal envelope the server actually issues.
 */
export function isCurrentRepositoryWriteEnvelope(row) {
  const envelope = row?.envelope;
  return Boolean(envelope && envelope.schema_version === "execution-envelope.v1" &&
    exactFutureInstant(envelope.expires_at) &&
    exactFutureInstant(envelope.agent_session?.lease_expires_at) &&
    envelope.server_binding?.authority?.environment === ENGINEERING_SERVER_EXECUTION_BINDING.environment &&
    envelope.server_binding?.authority?.read_only === ENGINEERING_SERVER_EXECUTION_BINDING.read_only &&
    envelope.server_binding?.authority?.capability_profile === ENGINEERING_SERVER_EXECUTION_BINDING.capability_profile &&
    envelope.server_binding?.identity?.agent_principal_id === "agent:codex" &&
    envelope.server_binding?.identity?.runtime_principal === "runtime:codex" &&
    envelope.server_binding?.adapter?.adapter_id === ENGINEERING_SERVER_EXECUTION_BINDING.adapter_ref &&
    JSON.stringify(envelope.request?.allowed_actions) === JSON.stringify(ENGINEERING_REPOSITORY_ACTIONS));
}

function isDispatchableCurrentEnvelope(row, agentSessionLeaseExpiresAt) {
  const envelope = row?.envelope;
  const issued = Date.parse(envelope?.issued_at);
  const expiry = Date.parse(envelope?.expires_at);
  return isCurrentRepositoryWriteEnvelope(row) &&
    envelope.envelope_id === `env:${row?.id}` &&
    envelope.work_request_id === `wr:${row?.work_request_id}` &&
    envelope.request?.job_ref === `job:${row?.job_id}` &&
    envelope.issued_at === canonicalInstant(row?.issued_at) &&
    envelope.expires_at === canonicalInstant(row?.expires_at) &&
    Number.isFinite(issued) && Number.isFinite(expiry) && expiry > issued && expiry - issued <= 30 * 60 * 1000 &&
    hasDispatchRunway(envelope.expires_at, 930) &&
    envelope.agent_session.lease_expires_at === envelope.expires_at &&
    envelope.agent_session.lease_expires_at === agentSessionLeaseExpiresAt &&
    envelope.agent_session.id === `session:${row?.agent_session_id}`;
}

export function buildCodexEnvelope({ source, plan, slice, jobId, sessionId, actor, envelopeId = globalThis.crypto.randomUUID(), expiresAt = null, replacesEnvelope = null, portfolioBinding = null }) {
  const issue = nowIso();
  const expiry = expiresAt || new Date(Date.parse(issue) + 30 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const resources = slice.declared_resource_refs || [];
  const envelope = {
    schema_version: "execution-envelope.v1",
    envelope_id: `env:${envelopeId}`,
    work_request_id: source.work.id,
    plan_revision: { id: source.plan.plan_ref, revision: Number(source.plan.revision), digest: source.plan.digest },
    agent_session: { id: `session:${sessionId}`, lease_expires_at: expiry },
    issued_at: issue, expires_at: expiry,
    state_binding: {
      state_version: Number(source.work.version),
      canonical_record_digest: source.work.canonical_record_digest,
      accepted_resource_revisions: resources.map(resource_ref => ({ resource_ref, revision_ref: `revision:${source.work.version}`, digest: canonicalDigest({ resource_ref, version: source.work.version }) })),
      compare_and_swap_required: true,
    },
    phase_binding: { phase_id: `phase:${slice.slice_ref}`, session_affinity: "fresh_native_session_required", switch_conditions: ["verified_checkpoint", "phase_boundary"], native_session_transfer: "semantic_state_only" },
    // Present ONLY for a slice the accepted portfolio governs. An ungoverned
    // envelope keeps its exact previous shape and therefore its exact previous
    // digest, so ordinary attended source work is unaffected byte for byte.
    ...(portfolioBinding ? { portfolio_binding: portfolioBinding } : {}),
    evaluation_context: {
      experiment_arm: "audited_state_routed_executors", auditor_mode: "diverse_read_only_auditor",
      evaluation_kernel_ref: "kernel:engineering-passport-v1", workflow_rubric_digest: plan.plan_digest,
      case_set_digest: canonicalDigest({ work_request: source.work.id, slice: slice.slice_ref }),
    },
    request: {
      job_ref: `job:${jobId}`, input_digest: canonicalDigest({ work_request: source.work.id, plan: plan.plan_digest, slice: slice.slice_ref }),
      data_class: "metadata_only", allowed_actions: [...ENGINEERING_REPOSITORY_ACTIONS],
      declared_expectations: {
        plan_step_refs: slice.declared_plan_step_refs || [], component_refs: slice.declared_component_refs || [],
        resource_refs: resources, component_dependencies: [],
      },
    },
    server_binding: {
      identity: {
        organization_tenant_id: "tenant:carr-internal", sponsoring_human_id: actor.sponsoring_human_slug ? `human:${actor.sponsoring_human_slug}` : "human:joe",
        agent_principal_id: "agent:codex", runtime_principal: "runtime:codex", personal_brain_scope: "brain:shared",
        personal_brain_version: "brain:shared-v1", personal_rule_count: 0, derived_by: "server_identity_resolution", client_mutable: false,
      },
      authority: { environment: ENGINEERING_SERVER_EXECUTION_BINDING.environment, risk_class: slice.risk_class || "R1", capability_profile: ENGINEERING_SERVER_EXECUTION_BINDING.capability_profile, capability_grant_ref: `grant:engineering-codex-repository-v1:${sessionId}`, read_only: ENGINEERING_SERVER_EXECUTION_BINDING.read_only, derived_by: "server_capability_resolution", client_mutable: false },
      adapter: { surface: "codex_desktop", adapter_id: ENGINEERING_SERVER_EXECUTION_BINDING.adapter_ref, adapter_version: "v1", harness_id: "harness:codex", harness_version: "v1", provider_id: "provider:openai", model_id: "model:codex", native_session_ref: `native:codex:${sessionId}`, configuration_fingerprint: canonicalDigest({ adapter: "codex", model: "codex" }) },
    },
    handoff: replacesEnvelope ? {
      mode: "replacement", replaces_agent_session_id: replacesEnvelope.agent_session.id,
      capability_inherited: false,
      checkpoint_ref: `checkpoint:authority-reissue:${replacesEnvelope.envelope_id.replace(/^env:/, "")}`,
      native_session_transfer: "semantic_state_only",
    } : { mode: "original", replaces_agent_session_id: null, capability_inherited: false, checkpoint_ref: null, native_session_transfer: "semantic_state_only" },
  };
  return envelope;
}

export function validateReceiptBinding(receipt, envelope, slice, actor, ToolError) {
  if (!receipt || typeof receipt !== "object") error(ToolError, { error: "engineering_receipt_invalid" });
  if (receipt.schema_version !== "engineering-slice-receipt.v1") error(ToolError, { error: "engineering_receipt_schema_invalid" });
  for (const field of ["attribution", "planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs", "checks", "artifact_refs", "evidence_refs", "deviations", "source_evidence"])
    if (!(field in receipt)) error(ToolError, { error: "engineering_receipt_missing_field", field });
  if (!Array.isArray(receipt.checks) || receipt.checks.length < 1 || !Array.isArray(receipt.evidence_refs) || !Array.isArray(receipt.deviations))
    error(ToolError, { error: "engineering_receipt_typed_fields_invalid" });
  if (receipt.envelope_digest !== envelope.envelope_digest) error(ToolError, { error: "engineering_receipt_envelope_mismatch" });
  if (receipt.slice_ref !== slice.slice_ref || receipt.plan_digest !== slice.plan_digest) error(ToolError, { error: "engineering_receipt_slice_binding_mismatch" });
  if (!OUTCOMES.has(receipt.outcome) || receipt.independent_verification_required !== true) error(ToolError, { error: "engineering_receipt_outcome_invalid" });
  if (receipt.reset_reconstruction?.fresh_session !== true || receipt.reset_reconstruction?.inherited_transcript_used !== false) error(ToolError, { error: "engineering_receipt_fresh_session_required" });
  if (receipt.executor_claim?.claimed_by !== actor.slug) error(ToolError, { error: "engineering_receipt_executor_mismatch" });
  return receipt;
}

export function closureProjection(facts, ToolError) {
  const source = sourceParts(facts.source, ToolError);
  const plan = requirePlan(sourcePlanRow(facts, source, ToolError).plan, ToolError);
  const planCurrent = planMatchesCurrentSource(plan, source);
  // Facts deliberately retain their canonical ledger wrappers.  Flattening a
  // receipt loses its immutable receipt_id and lets an unrelated review with a
  // coincident attempt label satisfy a later generation.
  const envelopes = canonicalEnvelopeRows(facts);
  const envelopesById = new Map(envelopes.map(row => [row.id, row]));
  const receiptRows = canonicalReceiptRows(facts).filter(row => {
    const envelope = envelopesById.get(row.envelope_id);
    const slice = plan.slices.find(item => item?.slice_ref === row.slice_ref);
    return Boolean(slice && isCanonicalReceiptPayload(row, envelope, plan, slice));
  });
  const reviewerRows = canonicalReviewerRows(facts).filter(row => {
    const receiptRow = receiptRows.find(receipt => receipt.id === row.receipt_id);
    const receiptDeviationRefs = receiptRow && canonicalReceiptDeviationRefs(receiptRow.receipt);
    return Boolean(receiptRow && receiptDeviationRefs &&
      isCanonicalReviewerFact(row, receiptRow, receiptDeviationRefs));
  });
  const latest = new Map(plan.slices.map(slice => [slice.slice_ref,
    latestReceiptForSlice(facts, source, plan, slice.slice_ref, ToolError)]));
  const states = plan.slices.map(slice => {
    const receiptRow = latest.get(slice.slice_ref) || null;
    const receipt = receiptRows.find(row => row.id === receiptRow?.id)?.receipt || null;
    const reviewRow = planCurrent ? exactPassedReviewForReceipt(facts, source, receiptRow, slice.slice_ref) : null;
    const dependenciesVerified = planCurrent && (slice.dependency_refs || []).every(ref =>
      Boolean(exactPassedReviewForReceipt(facts, source, latest.get(ref), ref)));
    const state = !receiptRow ? (dependenciesVerified ? "eligible" : "blocked")
      : reviewRow ? "verified_complete"
        : receiptRow.outcome === "failed" || receiptRow.outcome === "reopened" ? "reopened" : "claimed";
    return { slice_ref: slice.slice_ref, ordinal: slice.ordinal, dependency_refs: slice.dependency_refs || [], state,
      planned_check_refs: (slice.planned_checks || []).map(check => check.check_ref), deviation_refs: (receipt?.deviations || []).map(deviation => typeof deviation === "string" ? deviation : deviation.deviation_ref).filter(Boolean),
      manual_qa_required: slice.manual_qa_required, release_requirement: slice.release_requirement };
  });
  const complete = planCurrent && states.every(row => row.state === "verified_complete");
  const currentReceiptRows = [...latest.values()].map(row =>
    receiptRows.find(receipt => receipt.id === row?.id)).filter(Boolean);
  const currentReceiptIds = new Set(currentReceiptRows.map(row => row.id));
  const currentReviewerRows = reviewerRows.filter(row => currentReceiptIds.has(row.receipt_id));
  const evidence = receiptRows.flatMap(row => row.receipt?.evidence_refs || []).filter(item => item && typeof item === "object");
  const unresolved = states.filter(row => row.state !== "verified_complete").map(row => row.slice_ref);
  const disposition = (state, note) => ({ state, evidence_refs: evidence, note });
  const projection = {
    schema_version: "engineering-passport.v1",
    work_request: plan.work_request,
    accepted_plan_revision: plan.accepted_plan_revision,
    plan_digest: plan.plan_digest,
    slice_plan: plan,
    execution_envelopes: envelopes.map(row => row.envelope || row),
    slices: states,
    current_receipts: currentReceiptRows.map(row => row.receipt),
    current_reviewer_facts: currentReviewerRows.map(row => row.fact),
    receipts: receiptRows.map(row => row.receipt),
    reviewer_facts: reviewerRows.map(row => row.fact),
    qa_facts: [],
    operator_receipt: { what_changed: [], why: "derived from the accepted plan and typed execution evidence", evidence_refs: evidence, deviations: [], remaining_risk: unresolved, manual_qa_items: [] },
    closure: {
      work: disposition(complete ? "complete" : "unresolved", complete ? "all planned slices have a bound receipt and independent pass" : "one or more planned slices remain unresolved"),
      proof: disposition(complete ? "complete" : "unresolved", complete ? "all receipts are independently reviewed" : "receipts are executor claims until independently reviewed"),
      explanation: disposition(complete ? "complete" : "unresolved", "derived from canonical persisted facts"),
      release: disposition(complete ? "complete" : "unresolved", complete ? "all required slices are verified" : "release remains closed until closure is complete"),
      learning: { state: "unresolved", route: null, evidence_refs: evidence, note: "learning remains a proposal/disposition seam" },
    },
    closure_state: complete ? "complete" : "blocked",
    stale_conflict: planCurrent ? { state: "none", reason: null } :
      { state: "stale", reason: "current Work Request or accepted plan no longer matches the registered slice plan" },
  };
  projection.projection_digest = canonicalDigest(projection);
  return projection;
}

export async function runCodexSlice({ dispatchEnvelope, desk, envelope, task }) {
  if (envelope?.server_binding?.adapter?.surface !== "codex_desktop")
    throw new Error("engineering adapter unsupported: only codex_desktop is enabled in v1");
  if (typeof dispatchEnvelope !== "function") throw new Error("engineering Codex adapter requires dispatchEnvelope");
  return dispatchEnvelope(desk, envelope, task, { fresh: true });
}

export async function resolveSourceMergeAuthority(c, args, ToolError) {
  const decisionId = String(args.decision_id || "").replace(/^decision:/, "");
  if (`decision:${decisionId}` !== NO_CEREMONIAL_MERGE_DECISION || !UUID.test(decisionId))
    error(ToolError, { error: "source_merge_decision_not_allowed" });
  const work = args.work_request === undefined || args.work_request === null
    ? null : text(args.work_request, "work_request", ToolError);
  const headSha = text(args.head_sha, "head_sha", ToolError);
  if (!/^[0-9a-f]{40}$/.test(headSha) || !Number.isInteger(args.pr_number) || args.pr_number < 1)
    error(ToolError, { error: "source_merge_pr_identity_invalid" });
  const projection = (await c.query(
    `select ops.source_merge_authority_projection($1::uuid,$2::text,$3::text,$4::integer) authority
       /* engineering-runtime:source-merge-authority */`,
    [decisionId, work, headSha, args.pr_number])).rows[0]?.authority;
  if (!projection?.ok || !projection.authority || !projection.passport_facts?.source)
    error(ToolError, { error: projection?.error || "source_merge_authority_projection_refused" });
  const authority = projection.authority;
  if (authority.schema_version !== "source-merge-authority.v1" ||
      authority.derived_by !== "source-merge-authority-projection" ||
      authority.decision?.title !== NO_CEREMONIAL_MERGE_DECISION_TITLE ||
      authority.decision?.decision_ref !== NO_CEREMONIAL_MERGE_DECISION ||
      authority.decision?.sponsoring_human_slug !== "joe" || authority.exact_head_sha !== headSha ||
      authority.pr_number !== args.pr_number)
    error(ToolError, { error: "source_merge_authority_projection_mismatch" });
  const passport = closureProjection(projection.passport_facts, ToolError);
  requireExecutableSource(projection.passport_facts.source, ToolError);
  if (passport.closure_state !== "complete" || passport.stale_conflict?.state !== "none")
    error(ToolError, { error: "source_merge_passport_not_closed" });
  if (passport.slices.some(slice => slice.manual_qa_required === true))
    error(ToolError, { error: "source_merge_manual_qa_requires_human" });
  if (!Array.isArray(authority.authorized_path_claims) || !authority.authorized_path_claims.length ||
      typeof authority.scope_digest !== "string" || typeof authority.scope_ref !== "string")
    error(ToolError, { error: "source_merge_path_authority_missing" });
  if (!Array.isArray(authority.assurance_bindings) || !authority.assurance_bindings.length)
    error(ToolError, { error: "source_merge_assurance_lineage_incomplete" });
  return {
    ...authority,
    work_request: passport.work_request,
    accepted_plan_revision: passport.accepted_plan_revision,
    passport,
  };
}

// These functions are intentionally usable by both an MCP adapter (writer
// transaction) and the supervised worker (jobs transaction).  They return
// only server-derived bindings; callers never select identity, authority,
// provider, model, or native session continuity.
/**
 * The V5-F02 census reader, loaded lazily.
 *
 * WHY LAZILY. program-controller-census.v5.js imports
 * engineering-program-controller.v5.js, which imports ENGINEERING_REPOSITORY_ACTIONS
 * from THIS file. A static import here would close that into a module cycle
 * whose evaluation order decides whether a frozen constant is defined. A lazy
 * import inside the door resolves after every module has finished evaluating,
 * so there is no order to get wrong. It is cached, so the cost is paid once.
 */
const PROGRAM_CONTROLLER_READ_FAILURES = new Set([
  "DEPENDENCY_UNAVAILABLE", "FRESHNESS_UNKNOWN", "TENANT_SCOPE_REFUSED",
  "AUTHORIZATION_REFUSED", "INTERNAL_ERROR",
]);
let programControllerCensusModule = null;
async function programControllerCensus() {
  if (programControllerCensusModule === null)
    programControllerCensusModule = await import("./program-controller-census.v5.js");
  return programControllerCensusModule;
}

/**
 * The slice-admission decision, or `null` when the records the decision is made
 * of are not there.
 *
 * A TYPED DEPENDENCY FAILURE IS NOT A DECISION, and it is deliberately not
 * turned into one. The evaluator refuses on FACTS; "there is no recorded lease
 * for this slice yet" is the reader saying it has nothing to decide from, and
 * manufacturing a refusal out of it would be this module inventing the very
 * kind of fact the controller refuses to let anyone invent. Nothing is recorded
 * and the door continues exactly as it did before the seam existed. Only a real
 * refusal — an evaluator answer whose decision is not `allow` — stops a slice.
 *
 * `programRef` is the Work Request's own SERVER-DERIVED typed reference from
 * the passport facts (`wr:<uuid>`), never args.work_request: the caller names a
 * Work Request, the passport decides which one it is.
 */
async function programControllerAdmissionDecision(c, programRef, sliceRef) {
  const census = await programControllerCensus();
  try {
    const answer = await census.evaluateSliceAdmissionForSlice({ client: c, sliceRef, programRef });
    return { decision: answer.decision, reason_id: answer.reason_id,
      blocking_check: answer.blocking_check, decided_at: new Date().toISOString(), answer };
  } catch (error) {
    // Only the census reader's OWN typed read failures are "nothing to decide
    // from". A V5BoundaryError is a contract violation and still throws.
    if (!PROGRAM_CONTROLLER_READ_FAILURES.has(error?.code)) throw error;
    return null;
  }
}

/**
 * The resume decision, or `null` when the slice has no checkpoint row at all and
 * on the same typed read failure, for the same reason.
 */
async function programControllerResumeDecision(c, sliceRef) {
  const census = await programControllerCensus();
  try {
    const answer = await census.evaluateCheckpointResumeForSlice({ client: c, sliceRef });
    return answer === null ? null : { decision: answer.decision, reason_id: answer.reason_id,
      blocking_check: answer.blocking_check, decided_at: new Date().toISOString(), answer };
  } catch (error) {
    if (!PROGRAM_CONTROLLER_READ_FAILURES.has(error?.code)) throw error;
    return null;
  }
}

/**
 * Record one refused decision through the single privileged writer. The door
 * runs as carr_writer, which 0517 grants EXECUTE limited to this one fact kind,
 * so this is the only fact the door can mint — it cannot give itself a lease, a
 * width row or a receipt.
 */
async function recordProgramControllerRefusal(c, idempotencyKey, sliceRef, decision) {
  await c.query(
    "select ops.record_program_controller_fact('admission_refusal', $1::uuid, $2::jsonb) as fact",
    [idempotencyKey, JSON.stringify({
      slice_ref: sliceRef, reason_id: decision.reason_id, blocking_check: decision.blocking_check,
      decided_at: decision.decided_at,
      decision_digest: canonicalDigest(decision.answer ?? {
        refused_without_answer: true, reason_id: decision.reason_id,
        dependency_code: decision.dependency_code ?? null }),
    })]);
}

export async function admitEngineeringSlice(c, actor, args, ToolError, writeEvent) {
  if (typeof writeEvent !== "function") throw new TypeError("engineering admission requires an event writer");
  exactAuthorityFree(args, ToolError);
  uuid(args.idempotency_key, "idempotency_key", ToolError);
  const workRequest = text(args.work_request, "work_request", ToolError);
  const sliceRef = id(args.slice_ref, "slice_ref", ToolError);
  const sourceResult = await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRequest]);
  if (!sourceResult.rows.length || !sourceResult.rows[0].facts?.source) error(ToolError, { error: "engineering_work_request_not_found_or_not_ready" });
  let facts = sourceResult.rows[0].facts;
  let source = sourceParts(facts.source, ToolError);
  requireExecutableSource(facts.source, ToolError);
  let plan = sourcePlan(facts, source, ToolError);
  let slice = sliceFor(plan, sliceRef, ToolError);
  // The accepted contract must describe the binding this admission issues; a
  // contradiction refuses here, before any lock, job, session or envelope.
  requireServerExecutionBinding(plan, slice, ToolError);
  dependenciesSatisfied(facts, source, plan, slice, ToolError);
  // Portfolio governance is decided by trusted stored source, not by the
  // caller: a slice no accepted portfolio names comes back null and ordinary
  // attended source work is unaffected.
  const portfolioBinding = await portfolioAncestorBinding(c, facts, source, plan, sliceRef, ToolError);
  const locatorSourceDigest = canonicalDigest(facts.source);
  let priorEnvelopes = (facts.envelopes || [])
    .filter(row => row.slice_ref === sliceRef && row.accepted_plan_id === source.plan.record_id)
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  let priorEnvelope = priorEnvelopes.at(-1) || null;
  let priorSessionId = null;
  let priorBinding = null;
  let session = null;
  let executor = null;
  let priorSessionIsActive = false;

  // Locator reads above are intentionally unlocked. Existing mutable authority
  // is acquired in the global session -> exact actor -> lineage order before
  // any admission serialization or replacement decision.
  if (priorEnvelope) {
    const priorEnvelopeId = uuid(priorEnvelope.id, "prior_envelope.id", ToolError);
    const priorJobId = uuid(priorEnvelope.job_id, "prior_envelope.job_id", ToolError);
    priorBinding = (await c.query(
      `select e.id, e.job_id, e.work_request_id, e.accepted_plan_id, e.slice_plan_id,
              e.slice_ref, e.agent_session_id, e.envelope, j.state as job_state
         from ops.engineering_execution_envelope e
         join ops.job j on j.id=e.job_id
        where e.id=$1::uuid and e.job_id=$2::uuid`,
      [priorEnvelopeId, priorJobId],
    )).rows[0];
    if (!priorBinding || priorBinding.slice_ref !== sliceRef ||
        priorBinding.work_request_id !== source.work.id.replace(/^wr:/, "") ||
        priorBinding.accepted_plan_id !== source.plan.record_id ||
        (priorEnvelope.slice_plan_id && priorEnvelope.slice_plan_id !== priorBinding.slice_plan_id))
      error(ToolError, { error: "engineering_prior_envelope_binding_invalid", envelope_id: priorEnvelopeId });
    const factSessionId = priorEnvelopeSessionId(priorEnvelope);
    const rowSessionId = priorEnvelopeSessionId({ agent_session_id: priorBinding.agent_session_id, envelope: priorBinding.envelope });
    if (!factSessionId || !rowSessionId || factSessionId !== rowSessionId)
      error(ToolError, { error: "engineering_session_conflict", envelope_id: priorEnvelopeId });
    priorSessionId = rowSessionId;
    session = (await c.query(
      `select id, work_request_id, executor_actor_id, state, lease_expires_at, scope_ref, worktree_ref, source_commit_sha
         from ops.capability_agent_session
        where id=$1::uuid for update`,
      [priorSessionId],
    )).rows[0];
    priorSessionIsActive = ["claimed", "in_progress"].includes(session?.state);
    // Completed/cancelled sessions remain immutable, but are still valid
    // provenance for a fresh envelope generation on the same slice lineage.
    const priorSessionIsTerminal = ["completed", "cancelled"].includes(session?.state);
    if (!session || session.id !== priorSessionId ||
        session.work_request_id !== source.work.id.replace(/^wr:/, "") ||
        (!priorSessionIsActive && !priorSessionIsTerminal) ||
        session.scope_ref !== `slice:${sliceRef}` ||
        session.worktree_ref !== ENGINEERING_SESSION_WORKTREE ||
        session.source_commit_sha !== ENGINEERING_SESSION_SOURCE)
      error(ToolError, { error: "engineering_session_conflict", envelope_id: priorEnvelopeId });
    executor = (await c.query(
      "select id, slug from actor where id=$1::uuid and slug='codex' and active and kind='automation' for share",
      [session.executor_actor_id],
    )).rows[0];
    if (!executor || executor.id !== session.executor_actor_id)
      error(ToolError, { error: "engineering_codex_actor_not_provisioned" });
  } else {
    const openSessions = (await c.query(
      `select id, work_request_id, executor_actor_id, state, lease_expires_at, scope_ref, worktree_ref, source_commit_sha
         from ops.capability_agent_session
        where work_request_id=$1::uuid and state not in ('completed','cancelled')
        order by created_at desc for update`,
      [source.work.id.replace(/^wr:/, "")],
    )).rows;
    if (openSessions.length > 1) error(ToolError, { error: "engineering_session_conflict" });
    session = openSessions[0] || null;
    executor = (await c.query(
      session
        ? "select id, slug from actor where id=$1::uuid and slug='codex' and active and kind='automation' for share"
        : "select id, slug from actor where slug=$1 and active and kind='automation' for share",
      [session ? session.executor_actor_id : "codex"],
    )).rows[0];
    if (!executor) error(ToolError, { error: "engineering_codex_actor_not_provisioned" });
    if (session && (!session.lease_expires_at || !hasDispatchRunway(session.lease_expires_at)))
      error(ToolError, { error: "engineering_session_not_lease_bound" });
    if (session && !isExactEngineeringAdmissionSession(session, sliceRef, executor.id))
      error(ToolError, { error: "engineering_session_conflict" });
  }

  await c.query("select pg_advisory_xact_lock(hashtextextended($1, 0))", [`engineering-slice:${source.plan.digest}:${sliceRef}`]);
  let refreshed = await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRequest]);
  facts = refreshed.rows[0]?.facts;
  if (!facts?.source || canonicalDigest(facts.source) !== locatorSourceDigest)
    error(ToolError, { error: "engineering_admission_serialization_restart" });
  source = sourceParts(facts.source, ToolError);
  requireExecutableSource(facts.source, ToolError);
  plan = sourcePlan(facts, source, ToolError);
  slice = sliceFor(plan, sliceRef, ToolError);
  requireServerExecutionBinding(plan, slice, ToolError);
  dependenciesSatisfied(facts, source, plan, slice, ToolError);
  priorEnvelopes = (facts.envelopes || [])
    .filter(row => row.slice_ref === sliceRef && row.accepted_plan_id === source.plan.record_id)
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  const serializedPriorEnvelope = priorEnvelopes.at(-1) || null;
  if ((serializedPriorEnvelope?.id || null) !== (priorEnvelope?.id || null))
    error(ToolError, { error: "engineering_admission_serialization_restart" });
  priorEnvelope = serializedPriorEnvelope;

  // DOOR 1 — V5-F02 SLICE ADMISSION (WR-000110). Inside the serialization
  // boundary, after the prior-envelope reconciliation and BEFORE the replay
  // branch opens below, because a refusal here must be a refusal before any
  // edit and before any insert.
  //
  // THE REFUSAL RETURNS; IT DOES NOT THROW. mcp.js commits a handler that
  // returns and rolls back one that throws, so a thrown refusal would erase its
  // own evidence. Returning commits the fact-ledger row, the event and the
  // tool_call replay row together, and a key replay returns the identical
  // refusal without re-deciding. That is a deliberate divergence from the
  // door's other refusals, which throw because they are contract violations: an
  // admission refusal is a DECIDED OUTCOME, and it is the one thing this Work
  // Request requires to be persisted.
  //
  // Every fact the evaluator reads comes from ops.* relations through the
  // census reader, never from args: exactAuthorityFree closed the argument
  // schema to three keys before any of this ran.
  // The program the width is earned for is the Work Request's own typed
  // reference from the passport facts -- `wr:<uuid>`, server-derived -- never
  // args.work_request, which is a name the caller supplied.
  const admission = await programControllerAdmissionDecision(c, source.work.id, sliceRef);
  if (admission && admission.decision !== "allow") {
    await recordProgramControllerRefusal(c, args.idempotency_key, sliceRef, admission);
    await writeEvent(c, actor, "admit-engineering-slice", "ops_work_request", source.work.id.replace(/^wr:/, ""), {
      new: { admission_refused: admission.reason_id, blocking_check: admission.blocking_check,
        slice_ref: sliceRef }, idempotency_key: args.idempotency_key,
    });
    return { ok: false, admitted: false, refusal: "engineering_slice_admission_refused",
      reason_id: admission.reason_id, blocking_check: admission.blocking_check,
      slice_ref: sliceRef, decision: admission.answer ?? null };
  }

  if (priorEnvelope) {
    const priorSlicePlanId = uuid(priorBinding.slice_plan_id, "prior_envelope.slice_plan_id", ToolError);
    await c.query("select pg_advisory_xact_lock(hashtextextended($1, 0))", [`engineering-envelope:${priorSlicePlanId}:${sliceRef}`]);
    // A terminal session is a predecessor, never a replay candidate. Active
    // sessions retain the existing currentness and dispatch-runway boundary.
    if (priorSessionIsActive) {
      // DOOR 2 — V5-F02 CHECKPOINT RESUME (WR-000110). This branch IS the resume
      // door: it hands back a prior envelope and the slice carries on. Asking
      // whether the checkpoint it resumes from is current, lease-bound and
      // record-derived belongs here, before the currentness query.
      //
      // A slice with NO ops.slice_checkpoint row returns null and the branch is
      // exactly what it was: there is nothing to resume FROM RECORDS, the
      // existing currentness logic still governs, and the absence is reported
      // rather than fabricated into a checkpoint.
      const resume = await programControllerResumeDecision(c, sliceRef);
      if (resume && resume.decision !== "allow") {
        await recordProgramControllerRefusal(c, args.idempotency_key, sliceRef, resume);
        return { ok: false, admitted: false, refusal: "engineering_slice_resume_refused",
          reason_id: resume.reason_id, blocking_check: resume.blocking_check,
          slice_ref: sliceRef, checkpoint_absent: false };
      }
      const checkpointAbsent = resume === null;
      const currentness = await c.query("select ops.engineering_envelope_currentness($1::uuid,$2::uuid) as currentness", [priorEnvelope.id, priorEnvelope.job_id]);
      const priorJobReplayable = ["queued", "retry_wait", "running"].includes(priorBinding.job_state);
      if (priorJobReplayable && currentness.rows[0]?.currentness?.eligible === true &&
          currentness.rows[0]?.currentness?.dispatch_runway_sufficient === true)
        return { ok: true, replayed: true, envelope: priorEnvelope.envelope, envelope_id: priorEnvelope.id, job_id: priorEnvelope.job_id, checkpoint_absent: checkpointAbsent };
      if (priorJobReplayable && currentness.rows[0]?.currentness?.eligible === true)
        error(ToolError, { error: "engineering_envelope_insufficient_runway", envelope_id: priorEnvelope.id });
    }
  }

  // engineering_slice_plan is append-only: its trigger forbids UPDATE/DELETE,
  // and carr_writer intentionally has SELECT but no UPDATE authority. A row
  // lock therefore adds no currentness guarantee here and makes this governed
  // admission impossible (PostgreSQL row locks require UPDATE privilege).
  // The transaction-scoped advisory lock plus the repeated passport digest
  // check above are the serialization boundary; keep this lookup read-only.
  const registeredPlan = await c.query(
    `select id from ops.engineering_slice_plan
      where accepted_plan_id=$1::uuid and plan_digest=$2::text`,
    [source.plan.record_id, plan.plan_digest],
  );
  if (!registeredPlan.rows.length) error(ToolError, { error: "engineering_slice_plan_not_registered" });
  const lockedWork = await c.query("select id from ops.work_request where id=$1::uuid for share", [source.work.id.replace(/^wr:/, "")]);
  if (!lockedWork.rows.length) error(ToolError, { error: "engineering_work_request_not_found_or_not_ready" });
  refreshed = await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRequest]);
  facts = refreshed.rows[0]?.facts;
  if (!facts?.source || canonicalDigest(facts.source) !== locatorSourceDigest)
    error(ToolError, { error: "engineering_admission_serialization_restart" });
  source = sourceParts(facts.source, ToolError);
  requireExecutableSource(facts.source, ToolError);
  plan = sourcePlan(facts, source, ToolError);
  slice = sliceFor(plan, sliceRef, ToolError);
  requireServerExecutionBinding(plan, slice, ToolError);
  dependenciesSatisfied(facts, source, plan, slice, ToolError);

  if (priorEnvelope) {
    if (priorSessionIsActive) {
      await c.query(
        `update ops.capability_agent_session set state='cancelled', cancelled_at=now(), version=version+1
          where id=$1::uuid and work_request_id=$2::uuid and state not in ('completed','cancelled')`,
        [priorSessionId, source.work.id.replace(/^wr:/, "")]);
    }
    session = null;
  }
  const sessionExpiry = new Date(Date.now() + 30 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  if (!session) {
    const created = await c.query(
      `insert into ops.capability_agent_session
         (work_request_id, executor_actor_id, created_by_actor_id, source_commit_sha, worktree_ref, scope_ref, lease_expires_at)
       values ($1,$2,$3,$4,$5,$6,$7::timestamptz) returning id, executor_actor_id, state, lease_expires_at`,
      [source.work.id.replace(/^wr:/, ""), executor.id, actor.id, ENGINEERING_SESSION_SOURCE, ENGINEERING_SESSION_WORKTREE, `slice:${sliceRef}`, sessionExpiry]);
    session = created.rows[0];
  }
  const job = (await c.query(
    "select * from ops.engineering_enqueue_slice_job($1::text,$2::text,$3::text,$4::text,$5::integer)",
    [source.work.ref, sliceRef, plan.plan_digest, args.idempotency_key, priorEnvelopes.length + 1])).rows[0];
  if (!job) error(ToolError, { error: "engineering_job_admission_failed" });
  const expiry = session.lease_expires_at ? new Date(session.lease_expires_at).toISOString().replace(/\.\d{3}Z$/, "Z") : sessionExpiry;
  const envelopeId = globalThis.crypto.randomUUID();
  const envelope = buildCodexEnvelope({ source, plan, slice, jobId: job.id, sessionId: session.id, actor, envelopeId, expiresAt: expiry, portfolioBinding,
    replacesEnvelope: priorEnvelope?.envelope || null });
  const envelopeDigest = canonicalDigest(envelope);
  const inserted = await c.query(
    `insert into ops.engineering_execution_envelope
      (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
       state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at,
       supersedes_envelope_id,supersession_reason)
     values ($1,$2,$3,$4,(select id from ops.engineering_slice_plan where accepted_plan_id=$4),$5,$6,
       $7,$8,$9,$10::jsonb,$11::timestamptz,$12::timestamptz,$13,$14)
     returning *`,
    [envelopeId, job.id, source.work.id.replace(/^wr:/, ""), source.plan.record_id, sliceRef, session.id,
      Number(source.work.version), source.work.canonical_record_digest, envelopeDigest,
      JSON.stringify(envelope), envelope.issued_at, envelope.expires_at, priorEnvelope?.id || null,
      priorEnvelope ? "server-derived replacement of expired or non-executable envelope" : null]);
  if (!inserted.rows.length) error(ToolError, { error: "engineering_envelope_admission_failed" });
  const row = inserted.rows[0];
  await writeEvent(c, actor, "admit-engineering-slice", "ops_work_request", source.work.id.replace(/^wr:/, ""), {
    new: { engineering_job_id: job.id, envelope_id: row.id, supersedes_envelope_id: priorEnvelope?.id || null,
      slice_ref: sliceRef, plan_digest: plan.plan_digest }, idempotency_key: args.idempotency_key,
  });
  return { ok: true, replayed: false, job_id: job.id, envelope_id: row.id, envelope_digest: envelopeDigest,
    supersedes_envelope_id: priorEnvelope?.id || null, agent_session_id: session.id, slice_ref: sliceRef };
}

export async function claimEngineeringSlice(c, worker, limit = 1) {
  const claimed = await c.query("select * from ops.engineering_claim_slice($1::text,$2::integer,960)", [worker, limit]);
  const rows = [];
  for (const job of claimed.rows) {
    // Sponsored-authority recovery may create an immutable successor envelope
    // for the same idempotent job.  Never let the controller's choice depend
    // on an unspecified row order (which could resend an expired/read-only
    // predecessor); the most recently issued envelope is the only candidate
    // that may receive this fresh lease.
    // carr_jobs intentionally cannot read capability sessions directly.  The
    // SECURITY DEFINER controller binding below supplies the revalidated
    // session lease after enforcing the canonical lock/currentness boundary.
    const bound = await c.query(
      "select e.* from ops.engineering_execution_envelope e where e.job_id=$1 order by e.issued_at desc, e.id desc limit 1",
      [job.job_id],
    );
    if (!bound.rows.length || !bound.rows[0].envelope) {
      rows.push({ ...job, controller_error: "engineering_envelope_not_found" });
      continue;
    }
    const envelopeRow = bound.rows[0];
    const bindingResult = await c.query(
      "select ops.engineering_controller_binding($1::uuid,$2::uuid,$3::uuid) as binding",
      [envelopeRow.id, job.job_id, job.lease_token],
    );
    const binding = bindingResult.rows[0]?.binding;
    if (!binding || typeof binding !== "object" ||
        !isDispatchableCurrentEnvelope(envelopeRow, binding.agent_session_lease_expires_at)) {
      rows.push({ ...job, envelope: envelopeRow.envelope, envelope_id: envelopeRow.id,
        envelope_digest: envelopeRow.envelope_digest,
        controller_error: binding && typeof binding === "object"
          ? "engineering_envelope_currentness_invalid" : "engineering_controller_binding_missing" });
      continue;
    }
    rows.push({ ...job, envelope: envelopeRow.envelope, envelope_id: envelopeRow.id,
      envelope_digest: envelopeRow.envelope_digest, controller_binding: binding });
  }
  return rows;
}

export async function submitEngineeringReceipt(c, claimed, receipt, actor, ToolError) {
  if (!claimed?.job_id || !claimed.lease_token || !claimed.envelope_id) error(ToolError, { error: "engineering_claim_required" });
  const envelopeRow = (await c.query("select * from ops.engineering_execution_envelope where id=$1", [claimed.envelope_id])).rows[0];
  if (!envelopeRow) error(ToolError, { error: "engineering_envelope_not_found" });
  const workRef = claimed?.payload?.work_request;
  if (typeof workRef !== "string" || !workRef.trim())
    error(ToolError, { error: "engineering_work_request_binding_missing" });
  const facts = (await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRef])).rows[0]?.facts;
  const source = facts ? sourceParts(facts.source, ToolError) : null;
  if (!source || source.work.id !== `wr:${envelopeRow.work_request_id}`)
    error(ToolError, { error: "engineering_work_request_binding_mismatch" });
  requireExecutableSource(facts.source, ToolError);
  const plan = source ? sourcePlan(facts, source, ToolError) : null;
  const slice = plan ? sliceFor(plan, envelopeRow.slice_ref, ToolError) : { slice_ref: envelopeRow.slice_ref, plan_digest: null };
  if (plan && receipt.plan_digest !== plan.plan_digest) error(ToolError, { error: "engineering_receipt_plan_mismatch" });
  validateReceiptBinding(receipt, { ...envelopeRow.envelope, envelope_digest: envelopeRow.envelope_digest }, { ...slice, plan_digest: receipt.plan_digest }, actor, ToolError);
  const receiptDigest = canonicalDigest(receipt);
  const inserted = await c.query(
    "select * from ops.engineering_finalize_slice_receipt($1::uuid,$2::uuid,$3::jsonb,$4::text,$5::uuid)",
    [envelopeRow.id, claimed.lease_token, JSON.stringify(receipt), receiptDigest, actor.id]);
  if (!inserted.rows.length) error(ToolError, { error: "engineering_attempt_or_lease_mismatch" });
  return { ok: receipt.outcome === "claimed_complete", receipt_id: inserted.rows[0].id,
    receipt_digest: receiptDigest, outcome: receipt.outcome };
}

function controllerActor(claim, ToolError) {
  const binding = claim?.controller_binding;
  const actor = binding?.executor_actor;
  if (!actor || typeof actor !== "object") error(ToolError, { error: "engineering_controller_binding_invalid" });
  const actorId = uuid(actor.id, "controller_binding.executor_actor.id", ToolError);
  const actorSlug = id(actor.slug, "controller_binding.executor_actor.slug", ToolError);
  // The first live adapter is deliberately fixed.  A controller cannot turn a
  // job into another principal by supplying a different actor at invocation.
  if (actorSlug !== "codex") error(ToolError, { error: "engineering_executor_not_supported", executor: actorSlug });
  return { id: actorId, slug: actorSlug };
}

function controllerPlan(claim, ToolError) {
  const binding = claim?.controller_binding;
  const plan = requirePlan(binding?.slice_plan, ToolError);
  const slice = sliceFor(plan, claim?.payload?.slice_ref, ToolError);
  if (binding.envelope_id !== claim.envelope_id || binding.envelope_digest !== claim.envelope_digest
      || binding.slice_ref !== slice.slice_ref || binding.plan_digest !== plan.plan_digest)
    error(ToolError, { error: "engineering_controller_binding_mismatch" });
  if (canonicalInstant(binding.job_lease_expires_at) !== binding.job_lease_expires_at ||
      !hasDispatchRunway(binding.job_lease_expires_at, 930))
    error(ToolError, { error: "engineering_controller_job_lease_runway_invalid" });
  return { plan, slice, jobLeaseExpiresAt: binding.job_lease_expires_at };
}

async function failEngineeringClaim(c, claim, failureClass, detail) {
  const failure = await c.query(
    "select ops.engineering_fail_claim($1::uuid,$2::uuid,$3::text,$4::text) as state",
    [claim.job_id, claim.lease_token, failureClass, String(detail || "engineering controller failure").slice(0, 1000)],
  );
  return { job_id: claim.job_id, state: failure.rows[0]?.state || "failure_unreadable",
    failure_class: failureClass };
}

async function controllerReadback(c, claim, ToolError) {
  const work = claim?.payload?.work_request;
  if (typeof work !== "string" || !work.trim()) return { state: "unavailable", reason: "work_request_missing" };
  const row = await c.query("select ops.engineering_passport_facts($1::text) as facts", [work]);
  if (!row.rows[0]?.facts?.source) return { state: "unavailable", reason: "passport_facts_missing" };
  const passport = closureProjection(row.rows[0].facts, ToolError);
  const slice = passport.slices.find(item => item.slice_ref === claim.payload.slice_ref);
  return { state: "read", work_request: passport.work_request.id, slice_ref: claim.payload.slice_ref,
    slice_state: slice?.state || "unknown", projection_digest: passport.projection_digest };
}

// Dedicated controller entrypoint: claim from the existing job ledger, invoke
// the fresh-native Codex adapter, then persist its typed claim through the
// lease-bound receipt function.  The controller supplies the already audited
// room-bridge dispatcher; no Claude fallback or inherited transcript path is
// permitted here.
export async function runEngineeringWorker({ c, worker, desk, dispatchEnvelope,
  withCanonicalOwnership = null, limit = 1, ToolError }) {
  if (typeof dispatchEnvelope !== "function") throw new Error("engineering worker requires the Codex room-bridge dispatcher");
  if (withCanonicalOwnership !== null && typeof withCanonicalOwnership !== "function")
    throw new Error("engineering worker canonical ownership wrapper must be a function");
  // Maintenance runs as separate autocommit statements.  It must not acquire
  // queue locks inside the scoped claim transaction, whose lock order begins
  // with the exact capability session and actor authority.
  await c.query("select ops.reap_expired_jobs()");
  await c.query("select ops.engineering_retire_permanently_ineligible_jobs()");
  const claims = await claimEngineeringSlice(c, worker, limit);
  const results = [];
  for (const claim of claims) {
    if (claim.definition_key !== "engineering-slice") {
      results.push(await failEngineeringClaim(c, claim, "engineering_definition_mismatch", "claimed row was not engineering-slice"));
      continue;
    }
    let persisted;
    try {
      if (claim.controller_error) error(ToolError, { error: claim.controller_error });
      const actor = controllerActor(claim, ToolError);
      const { plan, slice, jobLeaseExpiresAt } = controllerPlan(claim, ToolError);
      // The job payload carries the canonical human Work Request ref that the
      // read-only engineering-passport-source verb resolves.  Preserve it as a
      // distinct field: task.work_request stays the immutable UUID binding.
      const workRequestRef = text(claim.payload?.work_request, "payload.work_request", ToolError);
      const task = { ...(claim.payload || {}), work_request: plan.work_request.id,
        work_request_ref: workRequestRef,
        job_ref: `job:${claim.job_id}`,
        attempt_id: `attempt:${claim.attempt}`, claim_lease_expires_at: jobLeaseExpiresAt,
        engineering_plan: plan, engineering_slice: slice };
      const execute = () => runCodexSlice({ dispatchEnvelope, desk, envelope: claim.envelope, task });
      // The local controller may hold a Worker-only bearer, but the model child
      // never does.  This wrapper acquires/checks/releases canonical ownership
      // around the existing fully validated dispatch path and must finish its
      // release before submitEngineeringReceipt performs the terminal update.
      const receipt = withCanonicalOwnership
        ? await withCanonicalOwnership(claim, execute)
        : await execute();
      if (!receipt || typeof receipt !== "object") throw new Error("Codex worker returned no typed receipt");
      persisted = await submitEngineeringReceipt(c, claim, receipt, actor, ToolError);
    } catch (cause) {
      try {
        results.push(await failEngineeringClaim(c, claim, "engineering_dispatch_failed",
          cause?.message || cause?.error || "engineering dispatch failed"));
      } catch {
        results.push({ job_id: claim.job_id, state: "cleanup_deferred", failure_class: "engineering_dispatch_failed" });
      }
      continue;
    }
    let operatorReadback;
    try {
      operatorReadback = await controllerReadback(c, claim, ToolError);
    } catch {
      operatorReadback = { state: "unavailable", reason: "readback_failed" };
    }
    results.push({ ...persisted, operator_readback: operatorReadback });
  }
  return { claimed: claims.length, completed: results.filter(result => result.ok === true).length, results };
}

export async function recordEngineeringReview(c, actor, args, ToolError, writeEvent) {
  if (typeof writeEvent !== "function") throw new TypeError("engineering review requires an event writer");
  exactAuthorityFree(args, ToolError);
  uuid(args.idempotency_key, "idempotency_key", ToolError);
  const receiptId = uuid(args.receipt_id, "receipt_id", ToolError);
  const fact = args.fact;
  const fields = ["attempt_id", "evidence_refs", "is_independent", "resolved_deviation_refs", "reviewed_deviation_refs", "reviewer_ref", "session_ref", "slice_ref", "state"];
  if (!fact || typeof fact !== "object" || Array.isArray(fact) ||
      Object.keys(fact).sort().join(",") !== fields.join(",") ||
      !["passed", "failed", "blocked"].includes(fact.state))
    error(ToolError, { error: "engineering_reviewer_fact_invalid" });
  if (!Array.isArray(fact.evidence_refs) || !Array.isArray(fact.reviewed_deviation_refs) || !Array.isArray(fact.resolved_deviation_refs))
    error(ToolError, { error: "engineering_reviewer_fact_typed_fields_invalid" });
  id(fact.attempt_id, "fact.attempt_id", ToolError);
  id(fact.slice_ref, "fact.slice_ref", ToolError);
  id(fact.reviewer_ref, "fact.reviewer_ref", ToolError);
  id(fact.session_ref, "fact.session_ref", ToolError);
  if (!isCanonicalReviewerSessionRef(fact.session_ref))
    error(ToolError, { error: "engineering_reviewer_session_invalid" });
  for (const [index, item] of fact.evidence_refs.entries())
    evidence(item, `fact.evidence_refs[${index}]`, ToolError);
  for (const field of ["reviewed_deviation_refs", "resolved_deviation_refs"])
    for (const [index, item] of fact[field].entries()) id(item, `fact.${field}[${index}]`, ToolError);
  const receipt = (await c.query("select * from ops.engineering_slice_receipt where id=$1", [receiptId])).rows[0];
  if (!receipt) error(ToolError, { error: "engineering_receipt_not_found" });
  if (receipt.executor_actor_id === actor.id || fact.attempt_id !== receipt.attempt_id ||
      fact.slice_ref !== receipt.slice_ref || fact.is_independent !== true ||
      ![actor.slug, `actor:${actor.slug}`, `reviewer:${actor.slug}`].includes(fact.reviewer_ref) ||
      fact.session_ref === receipt.receipt?.attribution?.session_ref)
    error(ToolError, { error: "engineering_independent_review_required" });
  if (fact.state === "passed" && receipt.outcome !== "claimed_complete")
    error(ToolError, { error: "engineering_review_receipt_not_complete" });
  if (fact.state === "passed" && fact.evidence_refs.length === 0)
    error(ToolError, { error: "engineering_review_evidence_required" });
  const deviations = Array.isArray(receipt.receipt?.deviations) ? receipt.receipt.deviations : [];
  const deviationRefs = deviations.map(item => item?.deviation_ref).filter(Boolean).sort();
  const reviewed = [...new Set(fact.reviewed_deviation_refs)].sort();
  const resolved = [...new Set(fact.resolved_deviation_refs)].sort();
  if (reviewed.join(",") !== deviationRefs.join(",") || resolved.some(ref => !deviationRefs.includes(ref)))
    error(ToolError, { error: "engineering_review_deviation_coverage_required" });
  if (fact.state === "passed" && (resolved.join(",") !== deviationRefs.join(",") ||
      deviations.some(item => item.review_state !== "resolved" || item.plan_revision_required === true)))
    error(ToolError, { error: "engineering_review_unresolved_deviation" });
  const row = (await c.query(
    `insert into ops.engineering_reviewer_fact
      (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
     values ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::uuid) returning *`,
    [receipt.id, receipt.work_request_id, receipt.slice_ref, actor.id, text(fact.session_ref, "fact.session_ref", ToolError), fact.state, JSON.stringify(fact), args.idempotency_key])).rows[0];
  await writeEvent(c, actor, "review-engineering-slice", "ops_work_request", receipt.work_request_id, { new: { reviewer_fact_id: row.id, receipt_id: receipt.id, state: row.state }, idempotency_key: args.idempotency_key });
  return { ok: true, reviewer_fact_id: row.id, state: row.state };
}

export function engineeringRuntimeTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "engineering-passport-source": {
      description: "Read the exact current accepted Work Request and plan binding required to construct an initial Engineering Slice Plan. This is the bootstrap read: it exposes no execution envelope, does not register, assign, dispatch, or grant authority, and remains available before any slice plan exists.",
      inputSchema: { type: "object", additionalProperties: false, properties: { work_request: { type: "string" } }, required: ["work_request"] },
      handler: async (c, _actor, args) => {
        const work = text(args.work_request, "work_request", ToolError);
        const r = await c.query("select ops.engineering_admission_source($1::text) as source", [work]);
        const source = r.rows[0]?.source;
        if (!source?.work_request || !source?.accepted_plan)
          error(ToolError, { error: "engineering_work_request_not_found_or_not_ready" });
        const parts = sourceParts(source, ToolError);
        return {
          schema_version: "engineering-passport-source.v1",
          work_request: parts.work,
          accepted_plan_revision: parts.plan,
        };
      },
    },
    "register-engineering-slice-plan": {
      write: true,
      description: "Register one typed Engineering Slice Plan as an immutable projection of the exact accepted sourced plan. It does not accept, assign, dispatch, or grant authority.",
      inputSchema: { type: "object", additionalProperties: false, properties: { idempotency_key: { type: "string" }, work_request: { type: "string" }, plan: { type: "object" }, plan_digest: { type: "string" } }, required: ["idempotency_key", "work_request", "plan", "plan_digest"] },
      handler: async (c, actor, args) => {
        exactAuthorityFree(args, ToolError);
        // Pure checks over args run BEFORE withEnvelope.  The envelope's first
        // statements are the advisory lock and the tool_call replay read, and a
        // stored response is returned without reaching the callback: inside it,
        // a v1 key first registered before migration 0616 would replay as
        // {replayed:true, ok:true} while the database door refuses the same
        // request, and a stored malformed plan would replay unvalidated.  Here a
        // refused registration runs no statement at all, and a valid v2 request
        // reaches the envelope exactly as before, so its replay is unchanged.
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const plan = requireRegistrablePlanVersion(requirePlan(args.plan, ToolError), ToolError);
        return withEnvelope(c, actor, "register-engineering-slice-plan", args, async () => {
        const work = text(args.work_request, "work_request", ToolError); const planDigest = digest(args.plan_digest, "plan_digest", ToolError);
        if (plan.plan_digest !== planDigest) error(ToolError, { error: "engineering_slice_plan_digest_mismatch" });
        const r = await c.query("select * from ops.engineering_register_slice_plan($1::text,$2::jsonb,$3::text,$4::uuid)", [work, JSON.stringify(plan), planDigest, args.idempotency_key]);
        if (!r.rows.length) error(ToolError, { error: "engineering_slice_plan_not_registered" });
        const row = r.rows[0]; await writeEvent(c, actor, "register-engineering-slice-plan", "ops_work_request", row.work_request_id, { new: { engineering_slice_plan_id: row.id, plan_digest: row.plan_digest }, idempotency_key: args.idempotency_key });
        return { ok: true, engineering_slice_plan_id: row.id, work_request_id: row.work_request_id, accepted_plan_id: row.accepted_plan_id, plan_digest: row.plan_digest };
      }); },
    },
    "admit-engineering-slice": {
      write: true,
      description: "Admit one eligible DAG slice from the exact accepted plan. The server creates the canonical ops.job, capability session, and immutable execution envelope; caller identity, authority, adapter, and native session continuity are never accepted as input.",
      inputSchema: { type: "object", additionalProperties: false, properties: { idempotency_key: { type: "string" }, work_request: { type: "string" }, slice_ref: { type: "string" } }, required: ["idempotency_key", "work_request", "slice_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "admit-engineering-slice", args, () => admitEngineeringSlice(c, actor, args, ToolError, writeEvent)),
    },
    "review-engineering-slice": {
      write: true,
      description: "Record one independent typed reviewer fact against a persisted Engineering Slice Receipt. The reviewer must be a different actor from the executor and must provide evidence for a pass.",
      inputSchema: { type: "object", additionalProperties: false, properties: { idempotency_key: { type: "string" }, receipt_id: { type: "string" }, fact: { type: "object" } }, required: ["idempotency_key", "receipt_id", "fact"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "review-engineering-slice", args, () => recordEngineeringReview(c, actor, args, ToolError, writeEvent)),
    },
    "engineering-passport": {
      description: "Read the canonical typed Engineering Passport projection. Closure is derived from persisted envelopes, receipts, and independent reviewer facts; it is not a task store or authority source.",
      inputSchema: { type: "object", additionalProperties: false, properties: { work_request: { type: "string" } }, required: ["work_request"] },
      handler: async (c, _actor, args) => { const work = text(args.work_request, "work_request", ToolError); const r = await c.query("select ops.engineering_passport_facts($1::text) as facts", [work]); if (!r.rows.length || !r.rows[0].facts?.source) error(ToolError, { error: "engineering_work_request_not_found" }); return closureProjection(r.rows[0].facts, ToolError); },
    },
    "source-merge-authority": {
      description: "Resolve one merge-only authority packet from the settled decision, Joe-accepted plan-hash file scope, and current unsuperseded Engineering receipt/reviewer generations through a reader-safe security-definer projection. Lease and assurance claims remain non-authorizing evidence and cannot add scope. The caller supplies only immutable locators plus exact PR identity.",
      inputSchema: { type: "object", additionalProperties: false, properties: { decision_id: { type: "string" }, work_request: { type: "string" }, pr_number: { type: "integer", minimum: 1 }, head_sha: { type: "string", pattern: "^[0-9a-f]{40}$" } }, required: ["decision_id", "pr_number", "head_sha"] },
      handler: async (c, _actor, args) => resolveSourceMergeAuthority(c, args, ToolError),
    },
    "engineering-writer-runtime-preflight": {
      fullOnly: true,
      writerConnection: true,
      description: "Read the effective database identity and the exact Engineering admission privileges through the Worker's DATABASE_URL_WRITER binding. It never returns or fingerprints the credential and executes in a read-only transaction.",
      inputSchema: { type: "object", additionalProperties: false, properties: {}, required: [] },
      handler: async (c) => {
        const row = (await c.query(`select
          session_user::text as session_user,
          current_user::text as current_user,
          current_database()::text as database,
          current_setting('transaction_read_only')::text as transaction_read_only,
          pg_has_role(current_user,'carr_writer','member') as member_carr_writer,
          has_table_privilege(current_user,'ops.engineering_slice_plan','select') as select_engineering_slice_plan,
          has_table_privilege(current_user,'ops.engineering_execution_envelope','insert') as insert_engineering_execution_envelope,
          has_function_privilege(current_user,'ops.engineering_passport_facts(text)','execute') as execute_engineering_passport_facts,
          has_function_privilege(current_user,'ops.engineering_enqueue_slice_job(text,text,text,text,integer)','execute') as execute_engineering_enqueue_slice_job`)).rows[0];
        if (!row) error(ToolError, { error: "engineering_writer_runtime_preflight_unavailable" });
        const checks = {
          identity_is_app_writer: row.session_user === "app_writer" && row.current_user === "app_writer",
          database_is_neondb: row.database === "neondb",
          transaction_is_read_only: row.transaction_read_only === "on",
          member_carr_writer: row.member_carr_writer === true,
          select_engineering_slice_plan: row.select_engineering_slice_plan === true,
          insert_engineering_execution_envelope: row.insert_engineering_execution_envelope === true,
          execute_engineering_passport_facts: row.execute_engineering_passport_facts === true,
          execute_engineering_enqueue_slice_job: row.execute_engineering_enqueue_slice_job === true,
        };
        return {
          ok: Object.values(checks).every(Boolean),
          identity: { session_user: row.session_user, current_user: row.current_user, database: row.database },
          checks,
        };
      },
    },
  };
}
