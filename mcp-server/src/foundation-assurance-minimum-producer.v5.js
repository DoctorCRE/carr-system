import { digest } from "./artifact-trust.js";
import {
  CONSUMER_GATE_RECEIPT_FIELDS,
  FOUNDATION_ASSURANCE_MINIMUM_PROJECTION,
  MINIMUM_REQUIRED_MEMBERS,
  createFoundationAssuranceMinimumGate,
} from "./benchmark-minimum.v5.js";
import { produceStoredBenchmarkCoverage } from "./benchmark-coverage-store.v5.js";
import {
  FOUNDATION_ASSURANCE_COMPARATORS,
  sealFoundationAssuranceEvidence,
} from "./foundation-assurance-evidence.v5.js";
import {
  FOUNDATION_ASSURANCE_ORACLE_SECRET,
  FOUNDATION_ASSURANCE_ORACLE_VERBS,
  assertFoundationAssuranceOracleSeat,
  foundationAssuranceProducerForVerb,
} from "./foundation-assurance-minimum-registration.v5.js";

export const FOUNDATION_ASSURANCE_MEMBER_RECEIPT_SCHEMA = "consumer-gate-receipt.v1";
export const FOUNDATION_ASSURANCE_RUNTIME_BINDING_SCHEMA =
  "doctorcre-v5-foundation-assurance-runtime-binding.v1";

export class FoundationAssuranceProducerError extends Error {
  constructor(code, detail) {
    super(code); this.name = "FoundationAssuranceProducerError";
    this.code = code; if (detail !== undefined) this.detail = detail;
  }
}
const fail = (code, detail) => { throw new FoundationAssuranceProducerError(code, detail); };
const copy = value => JSON.parse(JSON.stringify(value));
function freeze(value) {
  if (Array.isArray(value)) value.forEach(freeze);
  else if (value && typeof value === "object") Object.values(value).forEach(freeze);
  return Object.freeze(value);
}
function iso(value) {
  const n = typeof value === "number" ? value : Date.parse(value);
  if (!Number.isFinite(n)) fail("invalid_instant");
  return new Date(n).toISOString();
}
function memberForVerb(verb) {
  const registration = foundationAssuranceProducerForVerb(verb);
  return registration?.step_ref
    ? MINIMUM_REQUIRED_MEMBERS.find(row => row.step_ref === registration.step_ref) : null;
}

export function foundationAssuranceMemberReceipt({
  verb, accepted_manifest, subject_maker_identity, producer_identity,
  evidence, config, comparator, observed_at, ttl_ms,
}) {
  const member = memberForVerb(verb);
  if (!member) fail("foundation_assurance_member_verb_unknown", verb);
  const seal = sealFoundationAssuranceEvidence(evidence, config);
  const expectedComparator = foundationAssuranceProducerForVerb(verb).comparator_id;
  if (!comparator || comparator.status !== "pass" ||
      !FOUNDATION_ASSURANCE_COMPARATORS.includes(comparator.id) ||
      comparator.id !== expectedComparator)
    fail("foundation_assurance_comparator_not_passing", expectedComparator);
  if (!subject_maker_identity || subject_maker_identity.actor_id === producer_identity?.actor_id)
    fail("foundation_assurance_member_self_attestation");
  const observed = Date.parse(observed_at);
  if (!Number.isFinite(observed) || !Number.isSafeInteger(ttl_ms) || ttl_ms < 1)
    fail("foundation_assurance_member_window_invalid");
  const payload = evidence.benchmark_payload;
  const receipt = {
    gate_id: member.gate_id,
    receipt_producer_step_ref: member.step_ref,
    subject_digest: payload.subject_digest,
    candidate_digest: payload.candidate_digest,
    policy_digest: payload.policy_digest,
    environment_manifest_digest: seal.evidence_digest,
    subject_environment: member.subject_environment,
    evidence_scope: member.evidence_scope,
    subject_maker_identity: copy(subject_maker_identity),
    producer_identity: copy(producer_identity),
    evaluator_identity: copy(producer_identity),
    producer_role: member.producer_role,
    independent_oracle_ref: member.oracle_ref,
    oracle_version: member.oracle_version,
    evidence_ref: seal.evidence_ref,
    fixture_set_digest: comparator.detail_digest,
    observed_at: iso(observed),
    ttl_expires_at: iso(observed + ttl_ms),
    status: "pass",
    comparator: `${comparator.id}@wr95-v1`,
    negative_admission_result: "all_required_denials_observed",
  };
  if (Object.keys(receipt).length !== CONSUMER_GATE_RECEIPT_FIELDS.length)
    fail("foundation_assurance_member_receipt_shape");
  return freeze(receipt);
}

export function evaluateFoundationAssuranceMinimum({
  accepted_manifest, subject_maker_identity, producer_identity, evidence, config,
  coverage_fact, member_receipts, gate_zero, as_of,
}) {
  const seal = sealFoundationAssuranceEvidence(evidence, config);
  const payload = evidence.benchmark_payload;
  const snapshot = {
    schema_version: FOUNDATION_ASSURANCE_MINIMUM_PROJECTION,
    tenant: "carr-internal",
    as_of,
    binding: {
      subject_digest: payload.subject_digest,
      candidate_digest: payload.candidate_digest,
      policy_digest: payload.policy_digest,
      minimum_environment_manifest_digest: seal.evidence_digest,
      benchmark_manifest_digest: accepted_manifest.benchmark_manifest_digest,
      maximum_member_receipt_ttl_ms: config.maximum_member_receipt_ttl_ms,
      minimum_receipt_ttl_ms: config.minimum_receipt_ttl_ms,
    },
    gate_zero,
    benchmark_coverage: coverage_fact,
    members: [
      { step_ref: "step:benchmark-contract-human-exact-hash-acceptance-receipt",
        receipt: accepted_manifest },
      ...member_receipts.map(receipt => ({
        step_ref: receipt.receipt_producer_step_ref, receipt,
      })),
    ],
    minimum_receipt_context: {
      subject_maker_identity: copy(subject_maker_identity),
      producer_identity: copy(producer_identity),
      evaluator_identity: copy(producer_identity),
      evidence_ref: seal.evidence_ref,
      fixture_set_digest: seal.evidence_digest,
      comparator: "all-current-independent-pass@wr95-v1",
    },
  };
  const envelope = { schema_version: "doctorcre-v5-foundation-assurance-envelope.v1",
    evidence_digest: seal.evidence_digest };
  const gate = createFoundationAssuranceMinimumGate({
    authenticateEvidence(input) {
      if (input.evidence_digest !== seal.evidence_digest) fail("minimum_evidence_binding_mismatch");
      return { envelope_digest: digest(input), snapshot };
    },
  });
  return gate.evaluate(envelope);
}

export function foundationAssuranceMinimumTools({
  withEnvelope, ToolError, authenticatedIdentity,
}) {
  const translate = error => {
    if (error instanceof FoundationAssuranceProducerError || error?.code)
      throw new ToolError({ error: error.code || error.message, detail: error.detail });
    throw error;
  };
  return Object.fromEntries(FOUNDATION_ASSURANCE_ORACLE_VERBS.map(verb => [verb, {
    write: true,
    humanOnly: false,
    oracleSeatOnly: true,
    oracleFamily: "foundation-assurance",
    description: "WR-000095 oracle-seat-only producer. The caller supplies only a fresh idempotency key; authenticated evidence, current runtime binding, comparator input, identities, time and the durable result are derived inside the Worker/record-layer seam.",
    inputSchema: {
      type: "object", additionalProperties: false,
      properties: { idempotency_key: { type: "string" } },
      required: ["idempotency_key"],
    },
    handler: async (c, actor, args) => withEnvelope(c, actor, verb, args, async () => {
      const identity = authenticatedIdentity.receiptIdentity();
      try { assertFoundationAssuranceOracleSeat(actor, verb, identity); }
      catch (error) { return translate(error); }
      if (typeof c.seatConnection !== "function")
        throw new ToolError({ error: "foundation_assurance_seat_connection_unavailable",
          required_secret: FOUNDATION_ASSURANCE_ORACLE_SECRET });
      if (!c.foundationAssuranceRuntime)
        throw new ToolError({ error: "foundation_assurance_runtime_binding_unavailable" });
      try {
        return await c.seatConnection(async seat => {
          const replay = (await seat.query(
            "select ops.foundation_assurance_record_production($1::text,$2::uuid,$3::jsonb,$4::jsonb) as result",
            [verb, args.idempotency_key, JSON.stringify(identity), null]))
            .rows[0]?.result;
          if (replay) return replay;
          const material = (await seat.query(
            "select ops.foundation_assurance_producer_material($1::text,$2::jsonb,$3::jsonb) as material",
            [verb, JSON.stringify(identity), JSON.stringify(c.foundationAssuranceRuntime)]))
            .rows[0]?.material;
          if (!material) fail("foundation_assurance_material_unavailable", verb);
          let produced;
          if (verb === "produce-foundation-assurance-benchmark-coverage") {
            produced = produceStoredBenchmarkCoverage({ ...material,
              evaluator_identity: identity, observed_at: material.observed_at,
              ttl_ms: material.config.maximum_member_receipt_ttl_ms });
          } else if (verb === "record-foundation-assurance-minimum-outcome") {
            produced = evaluateFoundationAssuranceMinimum({ ...material,
              producer_identity: identity, as_of: material.observed_at });
          } else {
            produced = foundationAssuranceMemberReceipt({ ...material, verb,
              producer_identity: identity, observed_at: material.observed_at,
              ttl_ms: material.config.maximum_member_receipt_ttl_ms });
          }
          return (await seat.query(
            "select ops.foundation_assurance_record_production($1::text,$2::uuid,$3::jsonb,$4::jsonb) as result",
            [verb, args.idempotency_key, JSON.stringify(identity), JSON.stringify(produced)]))
            .rows[0].result;
        });
      } catch (error) { return translate(error); }
    }),
  }]));
}
