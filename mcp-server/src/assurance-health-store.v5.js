// DoctorCRE v5 V5-A01: the live record-layer door for the already-reviewed
// assurance-health contract in lib/assurance_health.py. The database owns the
// evidence and computes the projection; this module refuses any projection
// that could make a label outrun its exact, current evidence.
//
// WORKFLOW TRUTH. The contract makes V5-F09 workflow truth a requirement of
// every capability stage. The F09 census store (PR #1244) is not on main, so
// ops.read_assurance_health reports workflow_truth.available=false and every
// scope reads unknown / unavailable / not green -- an unregistered workflow can
// never read healthy. The guard below refuses any other answer in that state.

export const ASSURANCE_HEALTH_SCHEMA_VERSION = "assurance-health.v1";

export const ASSURANCE_HEALTH_LAYERS = Object.freeze([
  "artifact_assessment",
  "execution_assessment",
  "controller_assessment",
  "candidate_outcome_oracle",
  "activation_readback",
  "actual_business_outcome",
]);

const STATES = new Set(["healthy", "degraded", "failed", "unknown", "disabled", "not-yet-operational"]);
const STAGES = new Set(["act", "draft", "read", "unavailable"]);
const EVIDENCE_STATES = new Set([
  "unreadable", "missing", "mismatched", "unbindable", "conflicting",
  "refused_substitute", "failed", "error", "skipped", "untested",
  "self_attested", "indistinct", "stale", "passing",
]);
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const WORK_REQUEST = /^WR-[0-9]{1,12}$/;
function sameScope(a, b) {
  return a?.workflow_key === b?.workflow_key
    && a?.workflow_version === b?.workflow_version
    && (a?.work_request_id ?? null) === (b?.work_request_id ?? null);
}

function validScope(scope) {
  if (!scope || typeof scope !== "object" || Array.isArray(scope)) return false;
  const keys = Object.keys(scope).sort();
  const expected = scope.work_request_id == null
    ? ["workflow_key", "workflow_version"]
    : ["work_request_id", "workflow_key", "workflow_version"];
  return JSON.stringify(keys) === JSON.stringify(expected)
    && typeof scope.workflow_key === "string" && scope.workflow_key.length > 0
    && Number.isSafeInteger(scope.workflow_version) && scope.workflow_version > 0
    && (scope.work_request_id == null || WORK_REQUEST.test(scope.work_request_id));
}

function refuse(ErrorType, reason) {
  throw new ErrorType({ error: "assurance_health_projection_invalid", reason });
}

/**
 * Validate the record layer's answer before it can reach a product surface.
 * This is a guard, not a second classifier: it never upgrades a state and never
 * manufactures evidence. The database remains the producer.
 */
export function validateAssuranceHealthProjection(projection, requestedScope, ErrorType = Error) {
  if (!projection || typeof projection !== "object" || Array.isArray(projection)) refuse(ErrorType, "projection_not_object");
  if (projection.schema_version !== ASSURANCE_HEALTH_SCHEMA_VERSION) refuse(ErrorType, "schema_version");
  if (!validScope(requestedScope) || !validScope(projection.scope) || !sameScope(projection.scope, requestedScope))
    refuse(ErrorType, "scope_mismatch");
  if (!STATES.has(projection.state) || !STAGES.has(projection.capability_stage)
      || typeof projection.green !== "boolean" || projection.green !== (projection.state === "healthy"))
    refuse(ErrorType, "closed_state_or_stage");
  if (!projection.owner || projection.owner.kind !== "record_layer" || typeof projection.owner.ref !== "string")
    refuse(ErrorType, "owner_missing");
  // WORKFLOW TRUTH (V5-F09) IS A PRECONDITION OF EVERY STAGE. Until the census
  // store is readable here the record layer reports it unavailable, and an
  // unavailable truth admits exactly one answer: unknown, unavailable, not green.
  const truth = projection.workflow_truth;
  if (!truth || typeof truth !== "object" || Array.isArray(truth) || typeof truth.available !== "boolean")
    refuse(ErrorType, "workflow_truth_missing");
  if (!truth.available && (projection.state !== "unknown" || projection.capability_stage !== "unavailable" || projection.green))
    refuse(ErrorType, "workflow_truth_unavailable_claimed_state");
  if (truth.available && projection.state === "disabled" && truth.state !== "declared_disabled")
    refuse(ErrorType, "disabled_without_workflow_truth");
  if (truth.available && (projection.green || projection.capability_stage === "act")
      && (!Array.isArray(truth.admissible_modes) || !truth.admissible_modes.includes("live")))
    refuse(ErrorType, "act_without_live_admission");
  if (!projection.evidence || typeof projection.evidence !== "object" || Array.isArray(projection.evidence)
      || JSON.stringify(Object.keys(projection.evidence).sort()) !== JSON.stringify([...ASSURANCE_HEALTH_LAYERS].sort()))
    refuse(ErrorType, "evidence_layers");

  const identities = new Set();
  const nonpassing = [];
  for (const layer of ASSURANCE_HEALTH_LAYERS) {
    const row = projection.evidence[layer];
    if (!row || row.layer !== layer || !EVIDENCE_STATES.has(row.state) || !validScope(row.scope) || !sameScope(row.scope, requestedScope))
      refuse(ErrorType, `evidence_contract:${layer}`);
    if (row.state === "passing") {
      if (row.status !== "pass" || typeof row.evidence_ref !== "string" || row.evidence_ref.length === 0
          || !DIGEST.test(row.evidence_digest ?? "") || !Number.isFinite(Date.parse(row.observed_at ?? ""))
          || !Number.isFinite(Date.parse(row.expires_at ?? ""))) refuse(ErrorType, `passing_evidence:${layer}`);
      const identity = `${row.evidence_ref}\n${row.evidence_digest}`;
      if (identities.has(identity)) refuse(ErrorType, `indistinct_evidence:${layer}`);
      identities.add(identity);
    } else {
      nonpassing.push(layer);
    }
  }

  if ((projection.green || projection.state === "healthy" || projection.capability_stage === "act") && nonpassing.length)
    refuse(ErrorType, "missing_layer_claimed_operational");
  if (projection.state === "healthy" && identities.size !== ASSURANCE_HEALTH_LAYERS.length)
    refuse(ErrorType, "healthy_without_six_distinct_receipts");
  if (!projection.impact || !sameScope(projection.impact.scope_limited_to, requestedScope)
      || !Array.isArray(projection.impact.withdrawn_stages)) refuse(ErrorType, "impact_scope");
  if (!projection.recovery || !Array.isArray(projection.recovery.required_evidence)
      || projection.recovery.required_evidence.some(layer => !ASSURANCE_HEALTH_LAYERS.includes(layer)))
    refuse(ErrorType, "recovery_contract");
  for (const layer of nonpassing) {
    if (!projection.recovery.required_evidence.includes(layer)) refuse(ErrorType, `recovery_omits:${layer}`);
  }
  return projection;
}

export function assuranceHealthStoreTools({ ToolError }) {
  if (typeof ToolError !== "function") throw new TypeError("ToolError is required");
  return {
    "read-assurance-health": {
      // The read derives tenant from authenticated writer transaction context.
      // It writes no row; writerConnection only supplies that identity context.
      writerConnection: true,
      description: "Read DoctorCRE V5-A01 assurance health for one exact workflow/work-request scope. Every label carries the six current record-layer evidence dispositions that produced it; missing, stale, mismatched or failed evidence blocks green and act, and impact is fenced to the requested scope.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: {
          scope: {
            type: "object",
            additionalProperties: false,
            properties: {
              workflow_key: { type: "string", minLength: 1, maxLength: 255 },
              workflow_version: { type: "integer", minimum: 1 },
              work_request_id: { type: "string", pattern: "^WR-[0-9]{1,12}$" },
            },
            required: ["workflow_key", "workflow_version"],
          },
        },
        required: ["scope"],
      },
      handler: async (c, _actor, args) => {
        if (!validScope(args?.scope)) throw new ToolError({ error: "assurance_health_scope_invalid" });
        const { workflow_key, workflow_version, work_request_id = null } = args.scope;
        const projection = (await c.query(
          "select ops.read_assurance_health($1::text,$2::integer,$3::text) as projection /* read-assurance-health */",
          [workflow_key, workflow_version, work_request_id],
        )).rows[0]?.projection;
        return validateAssuranceHealthProjection(projection, args.scope, ToolError);
      },
    },
  };
}
