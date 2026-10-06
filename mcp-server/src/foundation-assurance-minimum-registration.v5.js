// WR-000095: closed registration for the nine Foundation/Assurance oracle calls.
//
// A REVIEW_TOKENS bearer authenticates an actor.  This table only answers which
// already-authenticated actor may run which producer; it never authenticates a
// bearer and no request field can alter it.

import { MINIMUM_REQUIRED_MEMBERS } from "./benchmark-minimum.v5.js";

export const FOUNDATION_ASSURANCE_REGISTRATION_SCHEMA =
  "doctorcre-v5-foundation-assurance-producer-registration.v1";
export const FOUNDATION_ASSURANCE_ORACLE_ROLE =
  "independent_foundation_assurance_minimum_oracle";
export const FOUNDATION_ASSURANCE_ORACLE_SECRET =
  "DATABASE_URL_FOUNDATION_ASSURANCE_WRITER";

const MEMBER_VERBS = Object.freeze({
  "step:assurance-fabric-preactivation-contract-receipt":
    "produce-assurance-fabric-preactivation-receipt",
  "step:foundation-control-plane-preactivation-contract-receipt":
    "produce-foundation-control-plane-preactivation-receipt",
  "step:global-execution-contract-independent-receipt":
    "produce-global-execution-contract-receipt",
  "step:global-no-phi-boundary-independent-receipt":
    "produce-global-no-phi-boundary-receipt",
  "step:global-prompt-injection-boundary-independent-receipt":
    "produce-global-prompt-injection-boundary-receipt",
  "step:global-secrets-boundary-independent-receipt":
    "produce-global-secrets-boundary-receipt",
  "step:global-source-authority-independent-receipt":
    "produce-global-source-authority-receipt",
});
const COMPARATOR_BY_STEP = Object.freeze({
  "step:assurance-fabric-preactivation-contract-receipt": "assurance-fabric-preactivation",
  "step:foundation-control-plane-preactivation-contract-receipt": "foundation-control-plane-preactivation",
  "step:global-execution-contract-independent-receipt": "global-execution-contract",
  "step:global-no-phi-boundary-independent-receipt": "global-no-phi-boundary",
  "step:global-prompt-injection-boundary-independent-receipt": "global-prompt-injection-boundary",
  "step:global-secrets-boundary-independent-receipt": "global-secrets-boundary",
  "step:global-source-authority-independent-receipt": "global-source-authority",
});

const ACTORS = Object.freeze({
  "produce-foundation-assurance-benchmark-coverage": "codex-fa-coverage",
  "produce-assurance-fabric-preactivation-receipt": "codex-fa-assurance",
  "produce-foundation-control-plane-preactivation-receipt": "codex-fa-foundation",
  "produce-global-execution-contract-receipt": "codex-fa-execution",
  "produce-global-no-phi-boundary-receipt": "codex-fa-phi",
  "produce-global-prompt-injection-boundary-receipt": "codex-fa-prompt",
  "produce-global-secrets-boundary-receipt": "codex-fa-secrets",
  "produce-global-source-authority-receipt": "codex-fa-source",
  "record-foundation-assurance-minimum-outcome": "codex-fa-minimum",
});

export const FOUNDATION_ASSURANCE_ORACLE_VERBS = Object.freeze(
  Object.keys(ACTORS).sort());
export const FOUNDATION_ASSURANCE_ORACLE_ACTORS = Object.freeze(
  [...new Set(Object.values(ACTORS))].sort());

function freeze(value) {
  if (Array.isArray(value)) value.forEach(freeze);
  else if (value && typeof value === "object") Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

const memberByStep = new Map(MINIMUM_REQUIRED_MEMBERS
  .filter(member => member.output_schema_ref === "consumer-gate-receipt.v1")
  .map(member => [member.step_ref, member]));

export const FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION = freeze({
  schema_version: FOUNDATION_ASSURANCE_REGISTRATION_SCHEMA,
  work_request: "WR-000095",
  plan_ref: "PLAN-23d37d8d8be0-v7",
  database_role: "carr_foundation_assurance_oracle",
  database_secret: FOUNDATION_ASSURANCE_ORACLE_SECRET,
  producers: FOUNDATION_ASSURANCE_ORACLE_VERBS.map(verb => {
    const step = Object.keys(MEMBER_VERBS).find(key => MEMBER_VERBS[key] === verb);
    const member = step ? memberByStep.get(step) : null;
    return {
      verb,
      actor_slug: ACTORS[verb],
      kind: verb === "produce-foundation-assurance-benchmark-coverage"
        ? "benchmark_coverage"
        : verb === "record-foundation-assurance-minimum-outcome"
          ? "minimum_outcome" : "member_receipt",
      ...(member ? { step_ref: member.step_ref, gate_id: member.gate_id,
        producer_role: member.producer_role, oracle_ref: member.oracle_ref,
        oracle_version: member.oracle_version, evidence_scope: member.evidence_scope,
        subject_environment: member.subject_environment,
        comparator_id: COMPARATOR_BY_STEP[member.step_ref] } : {}),
    };
  }),
});

const byVerb = new Map(FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION.producers
  .map(item => [item.verb, item]));

export function foundationAssuranceProducerForVerb(verb) {
  return byVerb.get(verb) || null;
}

export function foundationAssuranceOracleLane(verb) {
  return foundationAssuranceProducerForVerb(verb)?.actor_slug || null;
}

export function assertFoundationAssuranceOracleSeat(actor, verb, identity = null) {
  const producer = foundationAssuranceProducerForVerb(verb);
  if (!producer || !actor || actor.human === true || actor.slug !== producer.actor_slug ||
      actor.review !== true || actor.via !== "review-token") {
    const error = new Error("foundation_assurance_oracle_seat_required");
    error.code = "foundation_assurance_oracle_seat_required";
    error.detail = { verb, required_actor: producer?.actor_slug || null };
    throw error;
  }
  if (identity !== null &&
      (identity.actor_id !== actor.slug || identity.authority_class !== "review_agent" ||
       typeof identity.session_ref !== "string" || !identity.session_ref.startsWith("session:"))) {
    const error = new Error("foundation_assurance_oracle_identity_unavailable");
    error.code = "foundation_assurance_oracle_identity_unavailable";
    error.detail = { verb };
    throw error;
  }
  return Object.freeze({ ...producer, identity });
}

Object.freeze(foundationAssuranceProducerForVerb);
Object.freeze(foundationAssuranceOracleLane);
Object.freeze(assertFoundationAssuranceOracleSeat);
