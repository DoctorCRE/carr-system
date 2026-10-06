// DoctorCRE v5 slice V5-A00: durable benchmark-manifest persistence and the
// exact-hash human acceptance rail.
//
// benchmark-minimum.v5.js is pure. It can compute the ONE payload digest a
// verified partner would have to accept, and it can authenticate an acceptance
// envelope that already exists, but it cannot store a manifest and it cannot
// accept one — it says so itself, twice, under WHAT REMAINS INTEGRATION WORK.
// This file is the durable half: the benchmark subject decomposed into typed
// rows, an independent review bound to exact bytes and to the exact measurement
// evidence that was read, and a verified-partner acceptance whose acceptor is
// derived from the authenticated session.
//
// IT IS NOT A SECOND PROJECTION LAYER, AND NOT A SECOND CONTRACT. Every rule
// about what a benchmark payload IS comes from benchmark-minimum.v5.js:
// validateBenchmarkPayload, benchmarkPayloadDigest and
// evaluateBenchmarkWorkloadCoverage are imported and called, never restated.
// The field list, the domain tag, the gate id, the producer role and the
// combiner are imported constants. Nothing here re-decides a benchmark rule; if
// this file and the kernel could disagree, one of them would be wrong.
//
// THE ROWS ARE THE RECORD. `benchmarkDraftRows` decomposes the twenty-six r7
// payload fields into ordered typed rows, and `benchmarkPayloadFromRows` puts
// them back. The round trip is exact — the rebuilt payload hashes to the same
// digest as the original — which is what makes ops/benchmark-acceptance.candidate.sql's
// recomputation of the digest FROM THE STORED ROWS a statement about the
// persisted manifest rather than about a blob a caller once supplied.
//
// THREE FIXED CONSTANT GROUPS ARE NOT STORED. slo_thresholds,
// cost_variance_thresholds and deadline_contract are identity, not
// configuration: r7 declares every field of all three `const`. They are
// re-emitted from the kernel's frozen tables when a payload is rebuilt, so no
// caller-chosen threshold can ever reach a digest, and a draft that disagrees
// with them is refused by validateBenchmarkPayload before it reaches storage.
//
// NO ACTOR ARRIVES IN A PAYLOAD, AND NO BOOLEAN GRANTS ANYTHING.
// benchmark-manifest.v1's four acceptance-envelope fields
// (benchmark_manifest_digest, accepted_by_identity, accepted_at, status) are
// excluded from the payload by the canonicalization contract and are recorded
// by the gateway from a verified human act. A draft carrying any of them is
// refused by name, and so is any argument whose key reads like a self-asserted
// verification. The acceptor's authority class is DERIVED at the moment of the
// act by identity.js's authorizationClassForActor over the LIVE actor, exactly
// as global-boundaries.v5.js does, and is never read back out of a stored row —
// a projection that copied a stored class string would turn the partner test
// into the caller boolean this rail exists to prevent.
//
// THE ACCEPTANCE VERB'S THREE BINDINGS ARE NOW ALL BOUND, AND IT STILL REFUSES
// UNLESS EACH OF THEM ANSWERS. r7's receipt_producer_step_registry makes the
// benchmark acceptance step depend on exactly two steps; acceptance additionally
// rests on the passing review's measurement evidence. Both of the two gaps this
// header used to name are closed — the coverage attestation on its own evidence,
// and Gate Zero in migration 0502 — and closing them changed WHY an acceptance
// refuses, not WHETHER it can. Every acceptance still fails closed unless a
// current passing Gate Zero outcome, an attested passing review on the same
// bytes and an accepted intact portfolio all answer, strictly before it, for a
// live verified partner.
//
//   * step:portfolio-constitution-human-exact-hash-acceptance-receipt — BOUND.
//     ops.portfolio_accepted_revision() from migration 0496 answers it,
//     recomputing every digest from the persisted rows and refusing an
//     integrity failure. Acceptance binds that revision. No accepted portfolio
//     is invented here, and acceptance refuses when none exists.
//     WHAT THAT BINDING PROVES, EXACTLY: portfolio_ref is a reference the
//     ACCEPTOR supplies, so the receipt records that the NAMED portfolio
//     constitution is accepted and intact — not that this benchmark descends
//     from it. No benchmark-to-portfolio lineage is recorded anywhere in the
//     record layer, so none is claimed, and none is invented here. r7 requires
//     the prerequisite step, not a lineage relation.
//   * step:gate-zero-read-only-outcome — BOUND as of migration 0502, and the
//     OLD READING IS RETIRED BY THE PACKET RATHER THAN BY PREFERENCE. This
//     bullet used to say r7 registers no v5 producer for the step and that it
//     runs outside this system. The card-10 amendment (decision
//     311a9af5-3685-4c47-a158-f8dd70870ca1) added the producer row with the role
//     independent_control_plane_oracle, and tools/doctorcre-v5-review.cjs's own
//     comment now says the external boundary moved down to the four
//     predecessors. The seat that holds the oracle — the independent Codex
//     reviewer lane — records the outcome itself under Joe's 2026-09-13 ruling
//     d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70, with no partner countersign.
//     `readGateZeroOutcome` below is STILL PRIVATE and still fail-closed: not
//     exported, and refusing whenever no current passing, unexpired outcome
//     exists. It takes a CONNECTION now rather than nothing, because it reads a
//     record; that is the whole of the change to its shape. It is still read
//     FIRST, so nothing on the acceptance path writes anything before it
//     answers.
//   * the review's MEASUREMENT COVERAGE — BOUND, AND STILL SEPARATE FROM GATE
//     ZERO ON PURPOSE. This verb's own review path proves coverage with the
//     kernel against the payload rebuilt from the stored rows and computes the
//     measurement digest itself. It now RECORDS what proved it, in the same
//     definer call that writes the review: the evaluator by name from a closed
//     source constant, the payload digest the evaluator returned, the
//     measurement digest it proved over, and the digest of the evaluation. A
//     passing review cannot be written without one, by any writer, so the
//     "asserted or proved, and nothing tells them apart" ambiguity is retired —
//     not by promoting the digest, but by making the pass unwritable without an
//     attributed statement of what proved it.
//     `readMeasurementCoverageBinding` is a real read of that record. It refuses
//     a missing attestation, an evaluator outside the closed set, an attested
//     payload digest the draft no longer produces, and an attested measurement
//     digest that is not the review row's. It is still private, and it is still
//     read AFTER Gate Zero, so landing Gate Zero cannot skip it.
//
// THE REMAINING TRUST BOUNDARY, STATED EXACTLY, AND UNCHANGED BY THE
// ATTESTATION LANDING. The samples never enter the record layer. Now that the
// review path records an attestation naming the kernel evaluator, the payload
// digest it proved against and the measurement digest it proved over, the record
// layer is STILL believing a TRUSTED WRITER about an evaluation it did not
// perform and cannot repeat. The attestation makes that belief explicit,
// attributed and auditable, and it makes an unattested pass unwritable; it does
// not make the database a verifier of coverage, and no comment here should be
// read that way. Two of the three attested values — the payload digest and the
// measurement digest — the database does check for itself. The third, that the
// named evaluator is what ran, it takes on the writer's word.
//
// Both integration requirements are stated in full in the two frozen constants
// below and repeated in the refusal details, so each is named — the one still
// unbound and the one resolved — at the point where someone hits it. Every acceptance is additionally required to be
// strictly after both prerequisites — an acceptance AT a prerequisite instant
// did not follow it, which is the same exclusive reading
// benchmark-minimum.v5.js applies to member observation.
//
// WHAT REMAINS INTEGRATION WORK, named rather than implied:
//   * NO LONGER OUTSTANDING: the authenticated Gate Zero outcome record and its
//     two readers. Migration 0502 lands the record;
//     ops.benchmark_gate_zero_outcome() and readGateZeroOutcome below were
//     implemented in the SAME change, which is what the requirement demanded —
//     deleting the throw in one of them alone would have opened the gate without
//     a record. mcp-server/test/benchmark-acceptance-store.v5.test.mjs keeps that
//     pairing enforced now that neither throws: it reads both sources and fails
//     if either one reverts to a raise while the other still reads.
//   * NO LONGER OUTSTANDING: the measurement coverage attestation. It is
//     recorded by the review write path and read by both readers, landed
//     together in one change as the requirement demanded. What it does NOT
//     resolve is the authenticated benchmark_coverage fact the join projection
//     needs — that one requires a LIVE declared evaluator seat and a storage
//     verifier that re-derives the evaluation, and it is a different obligation
//     for a different artifact. See benchmark-minimum.v5.js's own statement of
//     it; nothing here should be read as having supplied it.
//   * Applying ops/benchmark-acceptance.candidate.sql as a numbered migration.
//     It is candidate source and is not in public.schema_migrations.
//   * Registering these verbs. `benchmarkAcceptanceStoreTools` is exported and
//     is deliberately NOT added to any tool index by this slice: registering a
//     humanOnly/authorityOnly verb is a separate, reviewed act.
//   * Projecting an r7 acceptance envelope. Three of its four fields are
//     available from an acceptance receipt, but accepted_by_identity is an
//     authenticated-receipt-identity.v1 and the actor object this rail sees
//     carries no `session:` reference. Minting one would be inventing the
//     binding the envelope exists to record, so no envelope is projected here.

import { digest } from "./artifact-trust.js";
import { authorizationClassForActor, isKnownPartner } from "./identity.js";
import { partnerAuthoritySlugForActor } from "./partner-authority.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { sealFoundationAssuranceEvidence } from "./foundation-assurance-evidence.v5.js";
import {
  BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS, BENCHMARK_COMBINER, BENCHMARK_COST_VARIANCE_THRESHOLDS,
  BENCHMARK_DEADLINE_CONTRACT, BENCHMARK_GATE_ID, BENCHMARK_MANIFEST_SCHEMA,
  BENCHMARK_PAYLOAD_DOMAIN_TAG, BENCHMARK_PAYLOAD_FIELDS, BENCHMARK_PRODUCER_ROLE,
  BENCHMARK_SLO_THRESHOLDS, BENCHMARK_STEP_REF, CONSUMER_GATE_RECEIPT_SCHEMA,
  GATE_ZERO_STEP_REF,
  benchmarkPayloadDigest, evaluateBenchmarkAdmissibility, evaluateBenchmarkWorkloadCoverage,
  validateBenchmarkPayload,
} from "./benchmark-minimum.v5.js";

// Module-local adapter schemas. NOT r7 schemas, and namespaced so they can never
// be mistaken for one: r7 declares no draft shape, no row shape and no
// prerequisite shape at all.
export const BENCHMARK_DRAFT_SCHEMA = "doctorcre-v5-benchmark-manifest-draft.v1";
export const BENCHMARK_DRAFT_ROWS_SCHEMA = "doctorcre-v5-benchmark-manifest-draft-rows.v1";
export const BENCHMARK_ACCEPTANCE_PREREQUISITES_SCHEMA =
  "doctorcre-v5-benchmark-acceptance-prerequisites.v1";

/**
 * The ten r7 payload fields that are ordered sets of unique non-empty strings,
 * C-sorted so two readers enumerate them identically. They share one relation in
 * the candidate SQL because they share one shape.
 */
export const BENCHMARK_DIMENSIONS = Object.freeze([
  "acknowledgement_endpoints", "arrival_patterns", "cache_states", "capacity_profiles",
  "comparator_versions", "device_profiles", "hardware_profiles", "network_profiles",
  "routes", "runtime_versions",
]);

/** The payload fields stored as scalar columns on the draft row itself. */
export const BENCHMARK_DRAFT_SCALAR_FIELDS = Object.freeze([
  "candidate_digest", "cost_expectation_matrix_digest", "outlier_rule",
  "p95_aggregation_method", "policy_digest", "samples_per_cell", "subject_digest",
  "warmup_runs",
]);

/**
 * The three constant groups that are re-emitted rather than stored. Keeping the
 * list here, derived from the kernel's own frozen tables, is what makes
 * "emitted, never stored" checkable: the row projection asserts that these are
 * exactly the payload fields it does not persist.
 */
export const BENCHMARK_EMITTED_CONSTANT_FIELDS = Object.freeze([
  "cost_variance_thresholds", "deadline_contract", "slo_thresholds",
]);

/**
 * THE CLOSED SET OF COVERAGE EVALUATORS THIS RAIL WILL ATTEST TO.
 *
 * One member, and it is a NAME rather than a second implementation. Recording
 * "who proved this coverage" is citing the kernel, not evaluating anything:
 * evaluateBenchmarkWorkloadCoverage remains the only place in this system where
 * a benchmark coverage judgement is made, and nothing here re-decides it.
 *
 * It is a CLOSED SOURCE CONSTANT rather than a value on the write path because
 * the alternative is free text a writer chooses, which would make "which
 * evaluator proved this" an assertion instead of a constrained fact. The same
 * list is carried in SQL by ops.benchmark_coverage_evaluators(), and both sides
 * check membership: a name outside the set is refused on the write path, on the
 * read path and by the attestation column's own check constraint.
 */
export const BENCHMARK_COVERAGE_EVALUATORS = Object.freeze([
  "benchmark-minimum.v5.js#evaluateBenchmarkWorkloadCoverage",
]);
/** The one evaluator this module's own review path cites, from the closed set. */
export const BENCHMARK_COVERAGE_EVALUATOR = BENCHMARK_COVERAGE_EVALUATORS[0];

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const BENCHMARK_REF = /^[A-Za-z0-9][A-Za-z0-9:._-]{2,199}$/;

/**
 * Argument keys that would be a caller asserting the very thing this rail
 * exists to derive. Matched on the normalized key at every depth, so a wrapper
 * object cannot smuggle one in. This is belt-and-braces over the closed input
 * schemas: an open schema is an unenforced one, and a closed schema that gains
 * a field in a later edit should still refuse these.
 */
const SELF_ASSERTED_AUTHORITY_FRAGMENTS = Object.freeze([
  "accepted_by", "accepted_at", "acceptor", "acceptance",
  "verified", "verified_human", "partner_confirmed", "human_approved",
  "authority_granted", "gate_zero", "clock_started", "issued",
]);

export class BenchmarkAcceptanceStoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "BenchmarkAcceptanceStoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function refuse(code, message, detail) {
  throw new BenchmarkAcceptanceStoreError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

const copy = value => JSON.parse(JSON.stringify(value));

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    refuse("invalid_digest", `${path} must be a sha256: reference`, { path });
  }
  return value;
}

function assertUuid(value, path) {
  if (typeof value !== "string" || !UUID.test(value)) {
    refuse("invalid_uuid", `${path} must be a uuid`, { path });
  }
  return value;
}

function assertBenchmarkRef(value, path) {
  if (typeof value !== "string" || !BENCHMARK_REF.test(value)) {
    refuse("invalid_benchmark_ref", `${path} must be a benchmark reference`, { path });
  }
  return value;
}

/**
 * The record layer's own bound on a review summary, asserted HERE so a caller
 * meets a named refusal rather than a column check violation.
 *
 * r7 states no bound on this field — it is not an r7 field — so this one belongs
 * to the rail, and it is stated on both sides in the same unit: `.length` counts
 * UTF-16 code units, and ops.benchmark_utf16_length() is what the review guard
 * counts with, because SQL's char_length counts codepoints and the two differ on
 * text above U+FFFF.
 */
const REVIEW_SUMMARY_MAX_UTF16_UNITS = 1000;
function assertReviewSummary(value, path) {
  if (typeof value !== "string" || value.trim() === "") {
    refuse("invalid_review_summary", `${path} must be a non-empty summary`, { path });
  }
  if (value.length > REVIEW_SUMMARY_MAX_UTF16_UNITS) {
    refuse("invalid_review_summary",
      `${path} is ${value.length} UTF-16 code units; this rail bounds it to ${REVIEW_SUMMARY_MAX_UTF16_UNITS}`,
      { path, length: value.length, maximum: REVIEW_SUMMARY_MAX_UTF16_UNITS });
  }
  return value;
}

/** Refuse a self-asserted authority claim anywhere in a caller structure. */
export function assertNoSelfAssertedAuthority(value, path) {
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertNoSelfAssertedAuthority(item, `${path}[${index}]`));
    return;
  }
  if (!isPlainObject(value)) return;
  for (const key of Object.keys(value)) {
    const normalized = key.toLowerCase();
    for (const fragment of SELF_ASSERTED_AUTHORITY_FRAGMENTS) {
      if (normalized === fragment || normalized.endsWith(`_${fragment}`) ||
          normalized.startsWith(`${fragment}_`)) {
        refuse("self_asserted_authority_refused",
          `"${key}" at ${path} asserts an authority this rail derives; it is never accepted from a caller`,
          { path: `${path}.${key}`, key });
      }
    }
    assertNoSelfAssertedAuthority(value[key], `${path}.${key}`);
  }
}

// ---------------------------------------------------------------------------
// The draft: one benchmark payload, validated by the kernel and decomposed into
// the typed rows the record layer stores.
// ---------------------------------------------------------------------------

/**
 * Validate one proposed benchmark payload and return its frozen view.
 *
 * The four acceptance-envelope fields are refused BY NAME before the kernel's
 * closed-shape check runs. The kernel would refuse them too — its `closed()`
 * admits exactly the twenty-six payload fields — but it would say only
 * "closed_shape", and "you supplied accepted_at" is the answer a caller needs:
 * the envelope is not a field they may fill in, it is the record of an act
 * somebody else performs.
 */
export function validateBenchmarkDraftPayload(payload) {
  if (!isPlainObject(payload)) refuse("invalid_shape", "payload must be an object", { path: "payload" });
  for (const field of BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS) {
    if (Object.hasOwn(payload, field)) {
      refuse("benchmark_acceptance_envelope_refused",
        `payload.${field} is an acceptance-envelope field; the gateway records it from a verified human act and a proposer may not supply it`,
        { path: `payload.${field}`, envelope_fields: [...BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS] });
    }
  }
  assertNoSelfAssertedAuthority(payload, "payload");
  // The kernel owns every remaining rule. It throws BenchmarkMinimumError with
  // its own stable codes, which are deliberately not re-wrapped: a caller that
  // learns "benchmark_weight_total_mismatch" should see that code, not a second
  // vocabulary for the same fact.
  validateBenchmarkPayload(payload);
  return deepFreeze({
    schema_version: BENCHMARK_DRAFT_SCHEMA,
    manifest_schema_ref: BENCHMARK_MANIFEST_SCHEMA,
    payload_domain_tag: BENCHMARK_PAYLOAD_DOMAIN_TAG,
    gate_id: BENCHMARK_GATE_ID,
    producer_step_ref: BENCHMARK_STEP_REF,
    producer_role: BENCHMARK_PRODUCER_ROLE,
    combiner: BENCHMARK_COMBINER,
    payload_digest: benchmarkPayloadDigest(payload),
    accepted: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Decompose one validated payload into the typed rows the record layer stores.
 *
 * ORDER IS PART OF THE HASH. Every list keeps its supplied order and carries an
 * explicit zero-based ordinal, because r7 array order participates in the
 * canonical serialization: a reorder is a different manifest, not a cosmetic
 * difference, and a record layer that stored these as unordered sets could not
 * reproduce the digest a partner accepted.
 *
 * The three fixed constant groups are absent from the result on purpose, and
 * the assertion below is what keeps that true: if a future edit added a
 * twenty-seventh payload field, this function would refuse rather than silently
 * drop it from storage while it stayed in the digest.
 */
export function benchmarkDraftRows(payload) {
  const view = validateBenchmarkDraftPayload(payload);

  const persisted = new Set([
    ...BENCHMARK_DRAFT_SCALAR_FIELDS, ...BENCHMARK_DIMENSIONS,
    "workload_mix", "request_size_distribution", "concurrency_levels",
    "browsers", "evaluator_identities",
  ]);
  const unaccounted = BENCHMARK_PAYLOAD_FIELDS.filter(field =>
    !persisted.has(field) && !BENCHMARK_EMITTED_CONSTANT_FIELDS.includes(field));
  if (unaccounted.length > 0) {
    refuse("benchmark_payload_field_unstored",
      `${unaccounted.length} payload field(s) would be hashed but not stored; the row projection is incomplete`,
      { fields: unaccounted });
  }

  const dimensions = [];
  for (const dimension of BENCHMARK_DIMENSIONS) {
    payload[dimension].forEach((value, ordinal) => dimensions.push({ dimension, ordinal, value }));
  }

  const scalars = {};
  for (const field of BENCHMARK_DRAFT_SCALAR_FIELDS) scalars[field] = payload[field];

  return deepFreeze({
    schema_version: BENCHMARK_DRAFT_ROWS_SCHEMA,
    payload_digest: view.payload_digest,
    scalars,
    dimensions,
    workloads: payload.workload_mix.map((workload, ordinal) => ({
      ordinal,
      workload_id: workload.workload_id,
      weight_basis_points: workload.weight_basis_points,
      operation_mix_digest: workload.operation_mix_digest,
    })),
    request_sizes: payload.request_size_distribution.map((point, ordinal) => ({
      ordinal, percentile: point.percentile, bytes: point.bytes,
    })),
    concurrency: payload.concurrency_levels.map((concurrency_level, ordinal) => ({
      ordinal, concurrency_level,
    })),
    browsers: payload.browsers.map((browser, ordinal) => ({
      ordinal, name: browser.name, version: browser.version, build: browser.build,
    })),
    evaluators: payload.evaluator_identities.map((identity, ordinal) => ({
      ordinal,
      actor_id: identity.actor_id,
      session_ref: identity.session_ref,
      // Payload content, not an authorization. Nothing in this file reads this
      // value back to decide anything; see the header.
      authority_class: identity.authority_class,
    })),
  });
}

/**
 * Rebuild one benchmark payload from its stored rows, re-emitting the three
 * fixed constant groups from the kernel's frozen tables.
 *
 * This is the JavaScript twin of ops.benchmark_payload_preimage(). Its purpose
 * is provable losslessness: `benchmarkPayloadDigest(benchmarkPayloadFromRows(
 * benchmarkDraftRows(p)))` must equal `benchmarkPayloadDigest(p)` for every
 * valid payload, and the unit tests assert exactly that. A decomposition that
 * loses a field, an order or a value is a record layer that cannot reproduce
 * the hash a partner accepted.
 */
export function benchmarkPayloadFromRows(rows) {
  if (!isPlainObject(rows)) refuse("invalid_shape", "rows must be an object", { path: "rows" });
  if (rows.schema_version !== BENCHMARK_DRAFT_ROWS_SCHEMA) {
    refuse("wrong_rows_schema", `rows.schema_version must be "${BENCHMARK_DRAFT_ROWS_SCHEMA}"`,
      { actual: rows.schema_version });
  }
  const ordered = (list, path) => {
    if (!Array.isArray(list)) refuse("invalid_shape", `${path} must be an array`, { path });
    const sorted = [...list].sort((a, b) => a.ordinal - b.ordinal);
    sorted.forEach((row, index) => {
      // A gap in the ordinals is a row that was expected and is missing, which
      // is exactly the shape a partial insert leaves behind. Rebuilding from
      // gapped rows would produce a shorter list that hashes to something no
      // proposer ever computed.
      if (row?.ordinal !== index) {
        refuse("benchmark_row_ordinal_gap", `${path} is not contiguously ordinaled from zero`,
          { path, expected: index, actual: row?.ordinal });
      }
    });
    return sorted;
  };

  const payload = {
    subject_digest: rows.scalars?.subject_digest,
    candidate_digest: rows.scalars?.candidate_digest,
    policy_digest: rows.scalars?.policy_digest,
    workload_mix: ordered(rows.workloads, "rows.workloads").map(row => ({
      workload_id: row.workload_id,
      weight_basis_points: row.weight_basis_points,
      operation_mix_digest: row.operation_mix_digest,
    })),
    request_size_distribution: ordered(rows.request_sizes, "rows.request_sizes").map(row => ({
      percentile: row.percentile, bytes: row.bytes,
    })),
    concurrency_levels: ordered(rows.concurrency, "rows.concurrency").map(row => row.concurrency_level),
    browsers: ordered(rows.browsers, "rows.browsers").map(row => ({
      name: row.name, version: row.version, build: row.build,
    })),
    samples_per_cell: rows.scalars?.samples_per_cell,
    warmup_runs: rows.scalars?.warmup_runs,
    p95_aggregation_method: rows.scalars?.p95_aggregation_method,
    outlier_rule: rows.scalars?.outlier_rule,
    evaluator_identities: ordered(rows.evaluators, "rows.evaluators").map(row => ({
      actor_id: row.actor_id, session_ref: row.session_ref, authority_class: row.authority_class,
    })),
    cost_expectation_matrix_digest: rows.scalars?.cost_expectation_matrix_digest,
    // EMITTED, NEVER READ BACK. These three are identity; taking them from a
    // stored row would make a fixed r7 constant something a row could move.
    slo_thresholds: copy(BENCHMARK_SLO_THRESHOLDS),
    cost_variance_thresholds: copy(BENCHMARK_COST_VARIANCE_THRESHOLDS),
    deadline_contract: copy(BENCHMARK_DEADLINE_CONTRACT),
  };

  if (!Array.isArray(rows.dimensions)) {
    refuse("invalid_shape", "rows.dimensions must be an array", { path: "rows.dimensions" });
  }
  for (const dimension of BENCHMARK_DIMENSIONS) {
    payload[dimension] = ordered(
      rows.dimensions.filter(row => row?.dimension === dimension),
      `rows.dimensions[${dimension}]`).map(row => row.value);
  }

  // Rebuilt in the r7 field order, so the object a reader inspects reads like
  // the schema. Canonicalization sorts keys anyway, so this is legibility
  // rather than correctness — but a payload that reads like the contract is one
  // a reviewer can check against the contract.
  const rebuilt = {};
  for (const field of BENCHMARK_PAYLOAD_FIELDS) rebuilt[field] = payload[field];
  validateBenchmarkPayload(rebuilt);
  return rebuilt;
}

// ---------------------------------------------------------------------------
// The two acceptance prerequisites.
// ---------------------------------------------------------------------------

/**
 * The Gate Zero integration requirement — RESOLVED as of migration 0502, and
 * resolved to exactly what it said it would take and no more.
 *
 * This is still not a Gate Zero policy, it still grants nothing, and nothing
 * reads it to decide anything. What changed is that the three clauses under
 * `required_to_resolve` were satisfied together, which is what that list asked
 * for.
 *
 * ONE OF THE OLD `why_unresolved` CLAUSES WAS ALSO WRONG BY THE TIME IT WAS
 * REPLACED, and it is kept below under `corrected_stale_clause` rather than
 * silently dropped. It said `step:gate-zero-read-only-outcome` is an external
 * pre-v5 step for which r7 registers no v5 producer, and that this is
 * intentional. The card-10 amendment (decision
 * 311a9af5-3685-4c47-a158-f8dd70870ca1, applied under Joe's ruling on open loop
 * #589) added the producer row, and tools/doctorcre-v5-review.cjs's own comment
 * now says the external boundary moved down to the four predecessors. That
 * sentence was the single most likely thing to send the next reader looking for
 * a human, so it is corrected by name.
 */
export const BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT = deepFreeze({
  step_ref: GATE_ZERO_STEP_REF,
  resolved: true,
  // STILL SCOPED TO THIS RECORD LAYER. Every clause is a statement about what is
  // bindable HERE. Resolving it does not turn this constant into a claim about
  // what Gate Zero decided; the rail binds a recorded outcome and judges none.
  scope: "the binding available in this record layer, not a judgement about what a Gate Zero run decided",
  // FALSE SINCE 2026-09-12, and it is a correction rather than a ruling: r7
  // carries the producer row, so a Gate Zero outcome is a v5 producer's output
  // and not an external arrival this record layer is merely receiving. The
  // producer itself landed in Step A (gate-zero-producer.v5.js).
  external_producer_is_intentional: false,
  what_was_unbound: [
    "This record layer held no authenticated Gate Zero outcome: no outcome digest and no observed instant, so an acceptance had nothing here to bind to.",
    "Both readers were fail-closed stubs, and either implemented alone would have opened the gate without a record on the other side.",
    "Whether the registered producer had emitted an outcome was unknowable to this module, because nothing here could see one. Step A built the producer and Step B records what it emits, so the question is now answered by a row rather than left open.",
  ],
  // THE CORRECTION, kept as a field because a deleted wrong sentence teaches
  // nobody. See the doc comment above.
  corrected_stale_clause: {
    said: "step:gate-zero-read-only-outcome is an external pre-v5 step admitted as such by tools/doctorcre-v5-review.cjs, and r7 registers no v5 producer for it. That is intentional and no producer registry entry is requested.",
    corrected_to: "r7 registers a v5 producer for step:gate-zero-read-only-outcome with the role independent_control_plane_oracle and the oracle oracle:gate-producer:gate-zero-read-only. The external pre-v5 boundary moved down to its four predecessors, and tools/doctorcre-v5-review.cjs says so in its own comment.",
    corrected_by: "311a9af5-3685-4c47-a158-f8dd70870ca1",
  },
  resolved_by: [
    "migrations/0502_gate_zero_read_only_outcome.sql, which records the authenticated outcome: one consumer-gate-receipt.v1 per candidate, the digest RECOMPUTED from the stored receipt, the instant it was observed, and the independent oracle seat that produced it.",
    "readGateZeroOutcome in this module and ops.benchmark_gate_zero_outcome(), implemented together against that record, as the requirement demanded.",
    "the write verb record-gate-zero-read-only-outcome, which refuses every actor except the staffed oracle seat under Joe's 2026-09-13 ruling d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70 — no partner countersign, because the human act in this chain is the benchmark acceptance downstream.",
  ],
  // The strictly-after ordering is unchanged and is still enforced by the
  // acceptance receipt's own check constraint, not by this constant.
  ordering_unchanged: "an acceptance recorded AT the Gate Zero instant did not follow it, and is refused exactly as one recorded before it is.",
  // WHAT RESOLVING THIS DID NOT DO, kept as a field because it is the clause
  // most likely to be forgotten by a reader who sees `resolved: true`.
  still_refused_after_resolution: [
    "a Gate Zero outcome digest supplied by a caller",
    "a caller-selected work-request reference standing in for the outcome",
    "a digest derived from a synthetic test fixture",
    "an arbitrary verified boolean",
    "a Gate Zero policy invented in this slice",
    "a recorded non-passing or expired outcome standing in for a current one",
    "silently upgrading the measurement coverage attestation, which is independent and stays an attributed assertion",
  ],
  // Retained under its old name so a consumer reading the refusal detail does
  // not lose the list when the requirement flipped.
  explicitly_refused: [
    "a Gate Zero outcome digest supplied by a caller",
    "a caller-selected work-request reference standing in for the outcome",
    "a digest derived from a synthetic test fixture",
    "an arbitrary verified boolean",
    "a Gate Zero policy invented in this slice",
    "a recorded non-passing or expired outcome standing in for a current one",
  ],
});

/**
 * The measurement coverage proof binding — RESOLVED, and resolved to exactly
 * what it claimed it would be worth and no more.
 *
 * Like the constant above it is not a coverage rule, it evaluates nothing, and
 * it does not compete with evaluateBenchmarkWorkloadCoverage — the kernel
 * remains the only place coverage is judged. The question it named was narrower:
 * given a review row, is there anything recorded that binds its
 * measurement_set_digest to that judgement? There is now.
 *
 * `remaining_trust_boundary` below is UNCHANGED, word for word, from when this
 * requirement was unresolved. That is deliberate and it is the point: the
 * attestation was specified in advance to be worth an explicit attributed
 * assertion rather than a verification, so resolving it must not quietly
 * re-describe it as something larger.
 */
export const BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT = deepFreeze({
  binding_ref: "binding:benchmark-measurement-coverage-proof",
  resolved: true,
  independent_of: GATE_ZERO_STEP_REF,
  what_was_unbound: [
    "A review's measurement_set_digest named the bytes its writer read. Naming is not proving, and no record beside it said how the digest came to be there.",
    "Written through review-benchmark-manifest-draft, the digest was computed here after evaluateBenchmarkWorkloadCoverage proved coverage against the payload rebuilt from the stored rows. Written by a direct call to ops.benchmark_review_manifest_draft, it was a trusted writer's assertion. Acceptance read a review by id and could not distinguish the two.",
    "Both writers are trusted — direct INSERT is granted to no role — and that trusted-writer authority is preserved rather than replaced. What was absent is the record that tells them apart.",
  ],
  resolved_by: [
    "ops.benchmark_measurement_coverage_attestation, written in the same definer call as the review it attests, so a passing review and its attestation cannot come apart and an unattested pass cannot be written at all.",
    "readMeasurementCoverageBinding in this module and ops.benchmark_measurement_coverage_binding(uuid), implemented together against that attestation.",
  ],
  // What is recorded, field by field, so nobody has to read the schema to know
  // what the assertion actually says.
  what_is_recorded: [
    "the kernel evaluator that proved coverage, named from a closed source constant rather than chosen by a writer",
    "the payload digest that evaluator RETURNED, which the database recomputes from the draft's own rows and refuses when it no longer matches",
    "the measurement digest it proved over, which must be the digest the review row itself names",
    "the digest of the evaluation result, so a holder of the samples can replay the judgement",
  ],
  // What resolving this did NOT do, kept as a field because it is the clause
  // most likely to be forgotten by a reader who sees `resolved: true`.
  still_unresolved_elsewhere: [
    "NO LONGER: the Gate Zero binding, which was unbound when this list was written and landed in migration 0502. It is still independent, still read FIRST, and still refuses on its own grounds — and it did not upgrade this attestation on its way past, which is the fourth thing explicitly_refused below names.",
    "The authenticated benchmark_coverage fact the join projection needs, which requires a LIVE declared evaluator seat and a storage verifier that re-derives the evaluation. That is a different obligation for a different artifact and this attestation is not it.",
  ],
  // KEPT VERBATIM from the unresolved constant. Read the doc comment above.
  remaining_trust_boundary:
    "The samples stay outside the record layer. The attestation makes a trusted writer's assertion explicit, attributed and auditable; it does not make the record layer an independent verifier of coverage, because the evaluation cannot be repeated there.",
  // Still refused, all four. Resolving the binding retires none of them: the
  // digest alone is still not evidence, the verdict is still the kernel's, no
  // second evaluator was written, and the Gate Zero binding is untouched.
  explicitly_refused: [
    "treating measurement_set_digest as evidence that coverage was proved",
    "a coverage verdict supplied by a caller",
    "a second coverage evaluator written outside benchmark-minimum.v5.js",
    "silently upgrading the assertion when the Gate Zero binding lands",
  ],
});

/**
 * THE PRIVATE GATE ZERO READER — now a real read, and the second half of a pair.
 *
 * IT WAS A PARAMETERLESS STUB THAT ALWAYS THREW, from V5-A00 until migration
 * 0502_gate_zero_read_only_outcome.sql. Its own doc comment named the condition
 * for changing that: land the record and implement BOTH readers in the same
 * change, because either alone opens the gate without a record on the other
 * side. That is what happened; ops.benchmark_gate_zero_outcome() is implemented
 * in the same commit, against the same table, with the same currentness rule.
 *
 * STILL PRIVATE, for the reason it always was: an exported reader is a callable
 * claim about Gate Zero, and a caller who could ask this module "what is the
 * current Gate Zero outcome" outside an acceptance would be reading an oracle's
 * receipt as an answer to a question it does not answer. It takes a CONNECTION
 * rather than nothing, because it now reads a record; that is the whole of the
 * change to its shape, and it is exactly the change readMeasurementCoverageBinding
 * made when its own record landed.
 *
 * IT IS THE MODULE-SIDE TWIN OF ops.benchmark_gate_zero_outcome(), NOT A
 * REPLACEMENT FOR IT. The database's reader is the authoritative one: it runs
 * inside the definer acceptance path where a handler bug cannot step around it,
 * and it is granted to no role, so this module cannot call it. What this one
 * does is refuse EARLY and by name, on the same grounds, from the granted
 * primitives — the same pattern deriveBenchmarkAcceptor and
 * readMeasurementCoverageBinding already follow. If the two ever disagreed, the
 * database would win and the acceptance would refuse.
 *
 * WHICH ROW IS CURRENT, as the same ordered procedure the SQL applies: status
 * 'pass'; not past its expiry; latest observed_at, tie-broken on outcome_digest
 * descending so the order is total; none left is a refusal. The two empty cases
 * are told apart — nothing ever recorded, versus everything recorded being
 * expired or non-passing — because they are different problems for whoever
 * hits them, and because a rail that reported them identically would make a
 * quarantined Gate Zero look like a Gate Zero that never ran.
 */
async function readGateZeroOutcome(c) {
  const row = (await c.query(
    `select step_ref, receipt, outcome_digest,
            to_char(observed_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"') as observed_at
       from ops.gate_zero_read_only_outcome
      where status = 'pass' and ttl_expires_at > now()
      order by observed_at desc, outcome_digest collate "C" desc
      limit 1`)).rows[0];

  if (!row) {
    const any = (await c.query(
      `select exists (select 1 from ops.gate_zero_read_only_outcome) as recorded`)).rows[0];
    if (any?.recorded) {
      refuse("gate_zero_outcome_not_current",
        "benchmark acceptance requires a CURRENT PASSING Gate Zero read-only outcome, and every outcome this record layer holds is non-passing or past its expiry. A recorded run is not a binding: r7's Q036.D1 requires truthful failure propagation, so a fail, unknown, stale or quarantined outcome refuses here exactly as an absent one does.",
        { ...BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT, recorded_outcomes_present: true });
    }
    refuse("gate_zero_outcome_unresolved",
      "benchmark acceptance requires an authenticated Gate Zero read-only outcome binding, and none has been recorded in this record layer yet. The record and its writer exist (ops.gate_zero_read_only_outcome, written by the independent oracle seat through record-gate-zero-read-only-outcome); until that seat records one, acceptance fails closed. No caller-supplied, configured or synthetic Gate Zero outcome is accepted.",
      { ...BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT, recorded_outcomes_present: false });
  }

  // THE RECEIPT IS READ TO VERIFY, NOT TO RETURN. r7 binds outcome_digest to
  // the canonical tagged preimage ["consumer-gate-receipt.v1", receipt]. A
  // stored digest is evidence only after this consumer independently derives
  // the same value from the stored receipt; otherwise the reader refuses before
  // the acceptance path reaches its next prerequisite or any write.
  const recomputed = digest([CONSUMER_GATE_RECEIPT_SCHEMA, row.receipt]);
  if (row.outcome_digest !== recomputed) {
    refuse("gate_zero_outcome_digest_divergence",
      "the current Gate Zero outcome digest does not match the tagged digest recomputed from its stored receipt",
      {
        recorded: row.outcome_digest,
        recomputed_here: recomputed,
        digest_recipe: `digest(["${CONSUMER_GATE_RECEIPT_SCHEMA}", receipt])`,
      });
  }

  // Exactly the closed three-field object benchmark-minimum.v5.js reads
  // (:435, :1449-1453), and nothing more. The receipt, the seat and the status
  // are recorded and are deliberately NOT returned: acceptance binds none of
  // them, and a field on this result that nothing consumes invites a future
  // reader to consume it as something it is not.
  return deepFreeze({
    step_ref: row.step_ref,
    outcome_digest: row.outcome_digest,
    observed_at: row.observed_at,
  });
}

/**
 * THE PRIVATE MEASUREMENT COVERAGE PROOF READER — now a real read.
 *
 * STILL PRIVATE. It is not exported, for the same reason it never was: an
 * exported reader is a callable surface, and a caller who could ask this module
 * "was coverage proved for review X" outside an acceptance would be reading a
 * trusted writer's attestation as an answer to a question it does not answer.
 * It takes a CONNECTION and a REVIEW ID rather than nothing, because it now
 * reads a record; that is the whole of the change to its shape.
 *
 * IT DOES NOT EVALUATE COVERAGE. evaluateBenchmarkWorkloadCoverage is the only
 * coverage authority in this system and this function is not a second one. It
 * reads what was recorded and refuses when the record does not hold up.
 *
 * IT IS THE MODULE-SIDE TWIN OF ops.benchmark_measurement_coverage_binding(uuid),
 * NOT A REPLACEMENT FOR IT. The database's reader is the authoritative one: it
 * runs inside the definer acceptance path where a handler bug cannot step around
 * it, and it is granted to no role, so this module cannot call it. What this one
 * does is refuse EARLY and by name, on the same grounds, from the granted
 * primitives — exactly the pattern deriveBenchmarkAcceptor already follows in
 * re-deriving an authority class the SQL guard also re-derives. If the two ever
 * disagreed, the database would win and the acceptance would refuse.
 *
 * THE THREE REFUSALS, and which of them the database can check for itself:
 *   * no attestation for this review — the pass was written by a path that
 *     recorded no proof, which the write path no longer permits;
 *   * an attested payload digest the draft no longer produces — RECOMPUTED, so
 *     appending content to a draft after its review invalidates the attestation
 *     instead of letting it silently outlive the bytes it was about;
 *   * an attested measurement digest that is not the review row's, or an
 *     evaluator outside the closed set.
 */
async function readMeasurementCoverageBinding(c, reviewId) {
  assertUuid(reviewId, "review_id");
  // The review is in the FROM clause and the attestation is LEFT JOINed, so an
  // unknown review is zero rows and an unattested one is a row with nulls —
  // both ordinary answers, each refused by name below rather than arriving as a
  // raw database error. Same safe-query shape as readLiveDraft.
  const row = (await c.query(
    `select r.id as review_id,
            r.measurement_set_digest as review_measurement_set_digest,
            ops.benchmark_payload_digest(r.draft_id) as live_payload_digest,
            a.coverage_proved_by,
            a.benchmark_payload_digest as attested_payload_digest,
            a.measurement_set_digest as attested_measurement_set_digest
       from ops.benchmark_manifest_review r
       left join ops.benchmark_measurement_coverage_attestation a on a.review_id = r.id
      where r.id = $1::uuid`,
    [reviewId])).rows[0];

  if (!row) {
    refuse("benchmark_review_unknown",
      "the measurement coverage proof binding names a review this record layer does not hold",
      { review_id: reviewId });
  }
  if (row.coverage_proved_by === null || row.coverage_proved_by === undefined) {
    refuse("measurement_coverage_proof_unbound",
      "benchmark acceptance requires a recorded proof binding for the passing review's measurement set, and none is recorded for this review: measurement_set_digest names the bytes a trusted writer read, and without an attestation beside it nothing binds those bytes to a coverage evaluation by benchmark-minimum.v5.js. Accepting on the digest alone would claim an independent verification that does not exist.",
      { ...BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT, review_id: reviewId });
  }
  if (!BENCHMARK_COVERAGE_EVALUATORS.includes(row.coverage_proved_by)) {
    refuse("measurement_coverage_evaluator_unknown",
      "the recorded coverage attestation names an evaluator this rail does not admit; the evaluator set is a closed source constant",
      { review_id: reviewId, coverage_proved_by: row.coverage_proved_by,
        admitted: [...BENCHMARK_COVERAGE_EVALUATORS] });
  }
  if (row.attested_payload_digest !== row.live_payload_digest) {
    refuse("measurement_coverage_payload_stale",
      "the recorded coverage attestation proves coverage against a payload digest this draft no longer produces; the proof is about bytes that have changed",
      { review_id: reviewId, attested: row.attested_payload_digest,
        recomputed: row.live_payload_digest });
  }
  if (row.attested_measurement_set_digest !== row.review_measurement_set_digest) {
    refuse("measurement_coverage_measurement_mismatch",
      "the recorded coverage attestation proves coverage over a measurement set the review does not name",
      { review_id: reviewId, attested: row.attested_measurement_set_digest,
        reviewed: row.review_measurement_set_digest });
  }
  // Exactly the documented return, and nothing more. The evaluation digest is
  // recorded and is deliberately not returned: acceptance binds nothing to it,
  // and a field on this result that nothing consumes invites a future reader to
  // consume it as something it is not.
  return deepFreeze({
    review_id: row.review_id,
    measurement_set_digest: row.review_measurement_set_digest,
    coverage_proved_by: row.coverage_proved_by,
  });
}

/**
 * The honest, zero-effect statement of what a benchmark acceptance still needs.
 *
 * A reader is entitled to know that the gate is shut and why. This reports the
 * BINDING STATUS of the r7 prerequisites and of the measurement coverage proof;
 * it makes no claim about Gate Zero itself, judges no coverage, exposes no
 * reader and configures nothing.
 */
export function benchmarkAcceptancePrerequisites() {
  return deepFreeze({
    schema_version: BENCHMARK_ACCEPTANCE_PREREQUISITES_SCHEMA,
    gate_id: BENCHMARK_GATE_ID,
    producer_step_ref: BENCHMARK_STEP_REF,
    producer_role: BENCHMARK_PRODUCER_ROLE,
    combiner: BENCHMARK_COMBINER,
    portfolio_constitution: {
      step_ref: "step:portfolio-constitution-human-exact-hash-acceptance-receipt",
      gate_id: "portfolio-constitution-accepted",
      resolved: true,
      resolved_by: "ops.portfolio_accepted_revision(text)",
      note: "The accepted revision and its recomputed accepted digest are bound onto the acceptance receipt. Acceptance refuses when no portfolio is accepted; none is created or assumed here.",
      // The exact reading, so nobody has to infer it from the field name.
      proves: [
        "the portfolio constitution named by the acceptor is accepted",
        "its accepted digest still recomputes from its persisted rows",
        "it was accepted strictly before this benchmark acceptance",
      ],
      does_not_prove: [
        "that this benchmark manifest descends from that portfolio: portfolio_ref is a reference the acceptor supplies, and no benchmark-to-portfolio lineage is recorded anywhere in the record layer",
      ],
      lineage_note: "No lineage rule is added here either. r7 makes the portfolio acceptance STEP a prerequisite, not a lineage relation, so inventing one would be a policy this slice does not hold. The claim is simply not made.",
    },
    gate_zero: BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT,
    measurement_coverage_proof: BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT,
    // ZERO ENTRIES NOW, AND IT WENT FROM TWO TO ONE TO ZERO THE ONLY HONEST WAY
    // BOTH TIMES: by a record landing, visibly, in a field a reader can check —
    // never by two requirements being quietly merged. The coverage binding
    // cleared on its own evidence; Gate Zero cleared on its own, in migration
    // 0502, and did not upgrade the coverage one on its way past.
    //
    // WHAT `acceptance_available: true` MEANS AND WHAT IT DOES NOT. It means no
    // binding is structurally absent any more: each of the three has a record
    // and a reader. It does NOT mean any particular acceptance will succeed —
    // every one of them still refuses unless a current passing Gate Zero
    // outcome, an attested passing review on the same bytes, and an accepted
    // intact portfolio all answer, strictly before it, for a live verified
    // partner. This field reports the SHAPE of the rail, not a prediction about
    // a call.
    acceptance_available: true,
    acceptance_blocked_by: [],
    ordering_rule: "strictly_after_both_prerequisites_and_the_passing_review",
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Derive the acceptor from the LIVE authenticated actor or its verified sponsor.
 *
 * A direct partner must still be a live known human partner. A native agent may
 * name that same acceptor only through partnerAuthoritySlugForActor, whose
 * sponsor comes from the server-held grant or local-token binding and requires
 * native-agent verification. The runtime remains the envelope/event actor; the
 * returned identity is the partner whose authority the act exercises.
 *
 * The identity is computed here and now from server-built actor state. It is
 * never read from `args`, and never from a stored row.
 */
export function deriveBenchmarkAcceptor(actor) {
  if (!isPlainObject(actor)) {
    refuse("acceptor_identity_unavailable",
      "a benchmark acceptance requires an authenticated actor; none was supplied", { path: "actor" });
  }
  const authority_class = authorizationClassForActor(actor);
  const directPartner = actor.human === true && isKnownPartner(actor.slug) &&
    authority_class === "verified_partner" ? actor.slug : null;
  const sponsoredPartner = directPartner === null ? partnerAuthoritySlugForActor(actor) : null;
  const acceptorSlug = directPartner || sponsoredPartner;
  if (acceptorSlug === null) {
    refuse("verified_partner_required",
      "only a verified partner may accept a benchmark manifest; this actor's derived authority class does not permit it",
      { derived_authority_class: authority_class, required_producer_role: BENCHMARK_PRODUCER_ROLE });
  }
  return deepFreeze({
    actor_id: acceptorSlug,
    // Direct sessions derive the partner from the live actor. Sponsored native
    // agents derive the same partner from the server-held grant/local-token
    // binding; caller input never selects or overrides that sponsor.
    authority_class: "verified_partner",
    authority_class_source: directPartner !== null
      ? "identity.authorizationClassForActor"
      : "partner-authority.partnerAuthoritySlugForActor",
    producer_role: BENCHMARK_PRODUCER_ROLE,
  });
}

// ---------------------------------------------------------------------------
// The verbs.
//
// No verb takes an actor, a partner or a tenant. Proposal and review derive
// their author from the writer context the server established; acceptance runs
// on the per-partner authority connection whose session_user the database
// reads. A caller may name a digest, and the database will only ever compare it
// against one recomputed from the stored rows.
//
// EVERY WRITE RESULT REPORTS EFFECTS THE PORTFOLIO WAY, not with the kernel's
// V5_NO_EFFECTS. That constant asserts `database_writes: 0`, which is true of a
// pure evaluator and false of a record layer. Claiming it here would be a
// convenient lie about the one thing these verbs actually do. The pure
// functions above do carry it, because for them it is true.
// ---------------------------------------------------------------------------

const RECORD_LAYER_EFFECTS = deepFreeze({
  creates_effect: false,
  jobs: 0, capabilities: 0, execution_envelopes: 0,
  admissions: 0, schedules: 0, deployments: 0,
  clock_started: false,
});

/**
 * Acceptance's OWN effects, which are not the proposal's.
 *
 * A proposal and a review add an inert row. An acceptance additionally closes
 * the benchmark gate for that draft: from then on
 * ops.benchmark_accepted_draft() returns it and the draft's content is frozen.
 * That is a real, benchmark-specific consequence and reporting the shared
 * record-layer constant here would have understated it.
 *
 * It is still not an execution effect and still starts no clock — the Journey 1
 * origin is the first current passing foundation-assurance-minimum receipt,
 * which this rail neither issues nor reaches.
 *
 * This object is returned only after every prerequisite answers and the
 * acceptance transaction succeeds, so `acceptance_enabled` must be true. A
 * false value here would contradict both the accepted status and the durable
 * gate-closing effects reported beside it.
 */
const ACCEPTANCE_EFFECTS = deepFreeze({
  ...RECORD_LAYER_EFFECTS,
  acceptance_enabled: true,
  benchmark_gate_closed_for_draft: true,
  draft_content_frozen: true,
  grants_dispatch_activation_or_execution: false,
});

export function benchmarkAcceptanceStoreTools({
  withEnvelope, writeEvent, ToolError, authenticatedIdentity,
}) {
  const digestSchema = { type: "string", pattern: "^sha256:[0-9a-f]{64}$" };
  const toolRefuse = (error, detail) => { throw new ToolError({ error, ...detail }); };

  /** Translate a module refusal into a tool refusal without losing the code. */
  const asToolError = (error) => {
    if (error instanceof BenchmarkAcceptanceStoreError || error?.name === "BenchmarkMinimumError" ||
        error?.name === "FoundationAssuranceEvidenceError") {
      toolRefuse(error.code, {
        message: error.message,
        ...(error.detail !== undefined ? { detail: error.detail } : {}),
      });
    }
    throw error;
  };

  /**
   * Run module-level assertions inside a handler. Without this a shape refusal
   * would leave the handler as a raw BenchmarkAcceptanceStoreError while a
   * contract refusal left as a ToolError, so a caller would meet two different
   * failure shapes for the same class of mistake.
   */
  const check = (fn) => { try { return fn(); } catch (error) { return asToolError(error); } };

  /**
   * The live payload and digest for one draft, read back from the rows.
   *
   * THE DRAFT IS IN THE FROM CLAUSE, NOT IN A SCALAR SELECT, and that is the
   * whole point of the shape. Called as bare scalars, these functions RAISE
   * "benchmark draft ... does not exist" for an unknown id, and a raw database
   * error would reach the caller in place of a named refusal — different in
   * shape from every other refusal this module produces, and carrying an
   * internal message a caller cannot act on. Joining against the draft row
   * instead makes an unknown draft return ZERO ROWS, which is an ordinary
   * answer, and the named refusal below is issued from it. It is the same
   * safe-query shape the readback uses.
   */
  const readLiveDraft = async (c, draftId) => {
    const row = (await c.query(
      `select ops.benchmark_payload_preimage(d.id) as payload,
              ops.benchmark_payload_digest(d.id) as payload_digest,
              ops.benchmark_draft_structure_error(d.id) as structure_error
         from ops.benchmark_manifest_draft d
        where d.id = $1::uuid`,
      [draftId])).rows[0];
    if (!row) toolRefuse("benchmark_draft_unknown", { draft_id: draftId });
    if (row.structure_error) {
      toolRefuse("benchmark_draft_incomplete", { draft_id: draftId, detail: row.structure_error });
    }
    return row;
  };

  return {
    "read-benchmark-manifest": {
      write: false,
      description: "Read one DoctorCRE v5 benchmark manifest: its payload rebuilt from the stored rows, both the digest recorded at proposal and the digest recomputed from those rows right now, its structural validity, the independent reviews recorded against it, and whether a verified partner has accepted it. Also reports the acceptance bindings and whether each is bound: the portfolio prerequisite (bound, and reported with what it does and does not prove), the Gate Zero outcome (bound as of migration 0502 — an independent oracle seat records one consumer-gate-receipt.v1 per candidate and this rail reads the current passing, unexpired one; a recorded fail, unknown, stale or quarantined outcome is a run and not a binding, and refuses exactly as an absent one does) and the measurement coverage proof (bound, and bound to exactly what it claimed it would be worth: a passing review carries a recorded attestation naming the kernel evaluator, the payload digest it proved against and the measurement digest it proved over, and the database recomputes that payload digest from the draft's own rows and compares that measurement digest against the review's own — it stays a trusted writer's attributed assertion and not an independent verification, because the samples stay outside this record layer). Exposes only content inside the payload digest and produces no effect.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { benchmark_ref: { type: "string" } }, required: ["benchmark_ref"],
      },
      handler: async (c, _actor, args) => {
        const row = (await c.query("select ops.benchmark_readback($1::text) as readback",
          [args.benchmark_ref])).rows[0]?.readback;
        if (!row) toolRefuse("benchmark_readback_unavailable", { benchmark_ref: args.benchmark_ref });
        return { ok: true, ...row, acceptance_prerequisites: benchmarkAcceptancePrerequisites() };
      },
    },

    "propose-benchmark-manifest-draft": {
      write: true,
      description: "Propose one inert DoctorCRE v5 benchmark manifest draft: the twenty-six closed r7 payload fields, stored as typed ordered rows rather than as a blob. The three fixed constant groups (SLO thresholds, cost variance thresholds and the deadline contract) are identity and are neither supplied nor stored. The four acceptance-envelope fields are refused by name. The draft creates no job, execution envelope, capability session, schedule, deployment or clock, and starts no Journey 1 clock. Its proposer is the authenticated writer, never a field in this payload, and the supplied digest is compared against one recomputed from the stored rows before the transaction may commit.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          benchmark_ref: { type: "string" },
          draft_version: { type: "integer", minimum: 1 },
          payload_digest: digestSchema,
          payload: { type: "object" },
        },
        required: ["idempotency_key", "benchmark_ref", "draft_version", "payload_digest", "payload"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "propose-benchmark-manifest-draft", args, async () => {
        check(() => {
          assertUuid(args.idempotency_key, "idempotency_key");
          assertBenchmarkRef(args.benchmark_ref, "benchmark_ref");
          assertDigestRef(args.payload_digest, "payload_digest");
        });

        // Validated and decomposed in the module first, so a malformed manifest
        // is refused with a named contract clause rather than a database
        // constraint message.
        const rows = check(() => benchmarkDraftRows(args.payload));

        // THE CALLER'S HASH IS NEVER THE ANSWER. It is compared here against
        // the digest this module computes, and again in the database against
        // the digest the stored rows produce. A proposer who believed they held
        // one manifest is refused rather than silently storing another.
        if (rows.payload_digest !== args.payload_digest) {
          toolRefuse("benchmark_payload_digest_mismatch",
            { expected: rows.payload_digest, supplied: args.payload_digest });
        }

        const draftId = (await c.query(
          `select ops.benchmark_propose_manifest_draft($1::text,$2::integer,$3::uuid,$4::text,
             $5::jsonb,$6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb,$10::jsonb,$11::jsonb) as id`,
          [args.benchmark_ref, args.draft_version, args.idempotency_key, rows.payload_digest,
            JSON.stringify(rows.scalars), JSON.stringify(rows.dimensions),
            JSON.stringify(rows.workloads), JSON.stringify(rows.request_sizes),
            JSON.stringify(rows.concurrency), JSON.stringify(rows.browsers),
            JSON.stringify(rows.evaluators)])).rows[0].id;

        await writeEvent(c, actor, "propose-benchmark-manifest-draft", "benchmark", draftId,
          { field: "draft_proposed",
            new: { benchmark_ref: args.benchmark_ref, draft_version: args.draft_version,
              payload_digest: rows.payload_digest },
            idempotency_key: args.idempotency_key });

        return {
          ok: true, draft_id: draftId, benchmark_ref: args.benchmark_ref,
          draft_version: args.draft_version, payload_digest: rows.payload_digest,
          // The kernel's own deterministic pre-acceptance answer, reused rather
          // than restated: it reports the one digest a partner would have to
          // accept and says, in its own fields, that it accepts nothing.
          admissibility: evaluateBenchmarkAdmissibility({ payload: args.payload }),
          accepted: false,
          acceptance_prerequisites: benchmarkAcceptancePrerequisites(),
          effects: RECORD_LAYER_EFFECTS,
        };
      }),
    },

    "review-benchmark-manifest-draft": {
      write: true,
      description: "Record one independent review of an exact DoctorCRE v5 benchmark payload digest. The reviewer is the authenticated writer and is never a field in this payload. The payload reviewed is read back from the stored rows, never taken from this call, so a reviewer cannot pass one manifest while naming another. A passing verdict additionally requires a measurement set that covers every required matrix cell and meets every fixed SLO: the coverage is proved ON THIS PATH by benchmark-minimum.v5.js against the stored payload, and the measurement digest recorded is computed here rather than accepted from the caller. Recorded beside the review, in the same definer call, is an attestation naming the kernel evaluator that proved coverage, the payload digest it proved against, the measurement digest it proved over and the digest of its result; a passing review cannot be written without one, by this verb or by a direct call to ops.benchmark_review_manifest_draft. Note exactly what that is worth: the samples stay outside the record layer, so the attestation is a trusted writer's explicit, attributed and auditable assertion rather than an independent verification the database performed. The database does check two of its three values for itself — it recomputes the payload digest from the stored rows and compares the measurement digest against the review row. A review naming a digest the draft no longer produces is refused, and a proposer cannot pass their own draft.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" }, draft_id: { type: "string" },
          reviewed_payload_digest: digestSchema,
          verdict: { type: "string", enum: ["pass", "fail"] },
          review_summary: { type: "string" },
        },
        required: ["idempotency_key", "draft_id", "reviewed_payload_digest", "verdict", "review_summary"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "review-benchmark-manifest-draft", args, async () => {
        check(() => {
          assertUuid(args.idempotency_key, "idempotency_key");
          assertUuid(args.draft_id, "draft_id");
          assertDigestRef(args.reviewed_payload_digest, "reviewed_payload_digest");
          assertReviewSummary(args.review_summary, "review_summary");
        });

        const live = await readLiveDraft(c, args.draft_id);
        // CURRENTNESS, at the module boundary as well as in the database: a
        // reviewer holding a stale hash is refused rather than shown a
        // different manifest under the old name.
        if (live.payload_digest !== args.reviewed_payload_digest) {
          toolRefuse("benchmark_review_digest_stale",
            { expected: live.payload_digest, supplied: args.reviewed_payload_digest });
        }

        let measurementSetDigest = null;
        let attestation = null;
        if (args.verdict === "pass") {
          const material = (await c.query(
            "select ops.foundation_assurance_benchmark_review_material($1::uuid) as material",
            [args.draft_id])).rows[0]?.material;
          if (!material?.evidence || !material?.config)
            toolRefuse("foundation_assurance_evidence_unavailable",
              { reason: "a passing review reads the server-sealed release evidence; no caller measurement field exists" });
          let seal;
          let coverage;
          try {
            seal = sealFoundationAssuranceEvidence(material.evidence, material.config);
            // Proved against the payload REBUILT FROM THE STORED ROWS, not
            // against anything in this call. The kernel refuses a missing cell,
            // an unrequired one, a duplicate, a short warmup, an exclusion that
            // takes a cell below its floor, a quoted rule that differs from the
            // accepted one, and any cell whose p95 misses its threshold.
            //
            // THE RETURN VALUE IS KEPT NOW. It used to be discarded, and the
            // attestation below is built out of it rather than out of a second
            // computation: benchmark_payload_digest is the digest the kernel
            // ACTUALLY PROVED AGAINST, and recomputing it here would attest to a
            // digest nobody proved anything about — a difference that is
            // invisible while the two agree and is the whole point when they do
            // not.
            coverage = evaluateBenchmarkWorkloadCoverage({
              payload: live.payload, measurements: material.evidence.measurements,
            });
          } catch (error) { return asToolError(error); }
          if (coverage.benchmark_payload_digest !== seal.benchmark_payload_digest)
            toolRefuse("benchmark_review_evidence_payload_mismatch", {
              reviewed_payload_digest: live.payload_digest,
              evidence_payload_digest: seal.benchmark_payload_digest,
            });
          // The exact bytes that were proved ON THIS PATH. The samples stay
          // outside the record layer; the digest is what names them, and it is
          // computed here rather than accepted from the caller.
          measurementSetDigest = seal.measurement_set_digest;
          // THE ATTESTATION. Four values, none of them invented and none of them
          // supplied by the MCP caller: a closed source constant naming the
          // evaluator, the evaluator's own returned payload digest, the
          // measurement digest computed on this path, and the digest of the
          // evaluation itself so the judgement is replayable by whoever holds
          // the samples. It carries no verdict — the verdict is the kernel's
          // throw-or-return above — and it is not in this verb's inputSchema.
          attestation = {
            coverage_proved_by: BENCHMARK_COVERAGE_EVALUATOR,
            benchmark_payload_digest: coverage.benchmark_payload_digest,
            measurement_set_digest: measurementSetDigest,
            evaluation_digest: digest(coverage),
          };
          const reviewerIdentity = authenticatedIdentity?.receiptIdentity?.();
          if (!reviewerIdentity || reviewerIdentity.actor_id !== actor.slug)
            toolRefuse("benchmark_reviewer_identity_unavailable", {});
        }

        const reviewId = (await c.query(
          `select ops.benchmark_review_manifest_draft($1::uuid,$2::uuid,$3::text,$4::text,$5::text,$6::text,$7::jsonb) as id`,
          [args.draft_id, args.idempotency_key, live.payload_digest, args.verdict,
            measurementSetDigest, args.review_summary,
            attestation === null ? null : JSON.stringify(attestation)])).rows[0].id;

        // READ THE RECORD BACK, THROUGH THE READER ACCEPTANCE ITSELF USES.
        //
        // This is what makes measurement_coverage_proof_recorded below a real
        // answer rather than a restatement of the branch this handler is already
        // inside. What it reports is not "I built an attestation and sent it" —
        // it is "the record layer holds an attestation for this review, its
        // evaluator is one this rail admits, its payload digest is the one this
        // draft still produces, and its measurement digest is the one the review
        // row names". The reader refuses on each of those, and a refusal here
        // rolls the whole review back rather than returning a pass whose proof
        // could not be read.
        let recordedBinding = null;
        if (args.verdict === "pass") {
          recordedBinding = await readMeasurementCoverageBinding(c, reviewId)
            .catch(error => asToolError(error));
        }

        await writeEvent(c, actor, "review-benchmark-manifest-draft", "benchmark", args.draft_id,
          { field: "draft_reviewed",
            new: { verdict: args.verdict, reviewed_payload_digest: live.payload_digest,
              measurement_set_digest: measurementSetDigest },
            idempotency_key: args.idempotency_key });

        return {
          ok: true, review_id: reviewId, draft_id: args.draft_id, verdict: args.verdict,
          reviewed_payload_digest: live.payload_digest,
          measurement_set_digest: measurementSetDigest,
          // True of THIS call: the kernel proved coverage against the payload
          // rebuilt from the stored rows a moment ago.
          coverage_proved_against_stored_payload: args.verdict === "pass",
          measurement_coverage_proof_binding:
            BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT.binding_ref,
          // THE REAL ANSWER, READ BACK FROM THE RECORD, not a constant and not
          // this handler restating its own branch. It is true when the record
          // layer holds an attestation for this review that survived every
          // check the reader applies, and false for a fail verdict, which proves
          // no coverage and records none.
          measurement_coverage_proof_recorded: recordedBinding !== null,
          // The attestation as the record layer holds it, so a caller sees the
          // evaluator by name rather than being told a boolean.
          measurement_coverage_proof: recordedBinding,
          // And what it is worth, quoted from the requirement rather than
          // paraphrased, so a passing review cannot be read as a verification.
          measurement_coverage_proof_limit:
            BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT.remaining_trust_boundary,
          accepted: false,
          acceptance_prerequisites: benchmarkAcceptancePrerequisites(),
          effects: RECORD_LAYER_EFFECTS,
        };
      }),
    },

    "accept-benchmark-manifest-draft": {
      write: true, humanOnly: true, authorityOnly: true,
      description: "HUMAN-ONLY: accept one exact DoctorCRE v5 benchmark payload digest as the verified_partner_benchmark_authority. The acceptor is derived from the authenticated partner authority session and is never a field in this payload; a writer connection cannot reach this verb at all. Acceptance requires a fresh passing independent review on the same bytes, three distinct identities, an accepted and intact portfolio constitution named by the acceptor (a prerequisite binding, not a claim of lineage: no benchmark-to-portfolio descent is recorded anywhere), and the Gate Zero read-only outcome as authenticated in this record layer -- and must fall strictly after all of them. ALL THREE BINDINGS ARE NOW BOUND, AND IT STILL REFUSES UNLESS EACH ANSWERS. The Gate Zero binding landed with migration 0502: an independent oracle seat records one consumer-gate-receipt.v1 per candidate through record-gate-zero-read-only-outcome, and this verb reads the CURRENT PASSING, UNEXPIRED one FIRST. A recorded fail, unknown, stale or quarantined outcome is a run and not a binding, and refuses here exactly as an absent one does -- that is Q036.D1's truthful-failure-propagation clause, not a courtesy. That read happens before this verb writes anything, so no acceptance can be half-attempted or mistaken for one that nearly worked. THE SECOND REFUSAL WAS RETIRED EARLIER, on its own evidence and not by Gate Zero: a passing review carries a recorded coverage attestation naming the kernel evaluator, the payload digest it proved against and the measurement digest it proved over, and the coverage binding is read AFTER Gate Zero so nothing about Gate Zero can skip it. It remains a trusted writer's attributed assertion, not an independent verification: the samples are outside this record layer, and Gate Zero landing did not upgrade it.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" }, draft_id: { type: "string" },
          accepted_payload_digest: digestSchema, review_id: { type: "string" },
          portfolio_ref: { type: "string" },
        },
        required: ["idempotency_key", "draft_id", "accepted_payload_digest", "review_id", "portfolio_ref"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "accept-benchmark-manifest-draft", args, async () => {
        check(() => {
          assertUuid(args.idempotency_key, "idempotency_key");
          assertUuid(args.draft_id, "draft_id");
          assertUuid(args.review_id, "review_id");
          assertDigestRef(args.accepted_payload_digest, "accepted_payload_digest");
          assertBenchmarkRef(args.portfolio_ref, "portfolio_ref");
          assertNoSelfAssertedAuthority(args, "args");
        });

        // ORDER IS DELIBERATE, AND IT IS THE SAME ORDER IT WAS BEFORE GATE ZERO
        // LANDED — which is the point. Nothing about the sequence changed when
        // the second binding became a real read; only the reason each step can
        // refuse did.
        //
        //   1. The acceptor is derived from the LIVE actor. The humanOnly and
        //      authorityOnly flags already gate this verb; deriving the class
        //      again here means the rail does not depend on a flag being read
        //      correctly somewhere else.
        //   2. The Gate Zero binding is read, and it is STILL FIRST. It is no
        //      longer a parameterless stub — since migration 0502 it reads the
        //      current passing, unexpired outcome — and it still refuses when
        //      there is none, so an acceptance attempted before any oracle run
        //      exists fails exactly where it always did.
        //   3. The measurement coverage proof binding is read SECOND, so
        //      whatever happens to (2) can never skip it.
        //
        // THE GUARANTEE, RESTATED AGAIN RATHER THAN QUIETLY DROPPED, because it
        // has now been narrowed twice and each narrowing is worth seeing. It was
        // "nothing on this path reaches the database", true while both readers
        // were parameterless stubs. It became "nothing reaches the database
        // while Gate Zero is unbound", true while one was. It is now: NOTHING ON
        // THIS PATH WRITES ANYTHING UNTIL ALL THREE BINDINGS ANSWER. Both
        // readers issue queries and both refuse before any write, so an
        // acceptance still cannot be half-attempted, logged as pending, or
        // mistaken for one that nearly worked. The old test assertion that this
        // verb issues ZERO statements is retired with the stub it described; the
        // assertion that replaces it is that it issues only READS and writes
        // nothing when either binding refuses.
        //
        // The two bindings remain INDEPENDENT, and Gate Zero landing did not
        // upgrade the coverage one: that is the fourth thing the coverage
        // requirement explicitly refuses, and it is asserted by test rather than
        // promised here.
        const acceptor = check(() => deriveBenchmarkAcceptor(actor));
        const gateZero = await readGateZeroOutcome(c).catch(error => asToolError(error));
        const coverage = await readMeasurementCoverageBinding(c, args.review_id)
          .catch(error => asToolError(error));

        // REACHABLE AS OF MIGRATION 0502, AND THIS BLOCK IS UNCHANGED BY THAT.
        // It was written out in full rather than stubbed precisely so that
        // landing Gate Zero would be a change to the two Gate Zero readers and
        // nothing else — not a rewrite of the acceptance path under time
        // pressure, and not a fresh set of decisions made by whoever happened to
        // land it. It cost nothing here, exactly as the coverage pair did.
        const live = await readLiveDraft(c, args.draft_id);
        if (live.payload_digest !== args.accepted_payload_digest) {
          toolRefuse("benchmark_acceptance_digest_stale",
            { expected: live.payload_digest, supplied: args.accepted_payload_digest });
        }
        // Everything remaining — the passing review on the same bytes, the
        // three distinct identities, the accepted portfolio constitution, the
        // Gate Zero binding, the coverage proof binding and the strictly-after
        // ordering — is enforced inside ops.benchmark_accept_manifest_draft and
        // its guard, where a handler bug cannot step around it. The database
        // re-derives all three bindings rather than trusting anything sent from
        // here, and compares each through ops.benchmark_assert_bound(), which
        // refuses an underived value instead of comparing it to NULL.
        const receiptId = (await c.query(
          `select ops.benchmark_accept_manifest_draft($1::uuid,$2::uuid,$3::text,$4::uuid,$5::text) as id`,
          [args.draft_id, args.idempotency_key, live.payload_digest, args.review_id,
            args.portfolio_ref])).rows[0].id;

        await writeEvent(c, actor, "accept-benchmark-manifest-draft", "benchmark", args.draft_id,
          { field: "draft_accepted",
            new: { accepted_payload_digest: live.payload_digest,
              portfolio_ref: args.portfolio_ref },
            idempotency_key: args.idempotency_key });

        return {
          ok: true, receipt_id: receiptId, draft_id: args.draft_id,
          gate_id: BENCHMARK_GATE_ID, producer_step_ref: BENCHMARK_STEP_REF,
          producer_role: acceptor.producer_role,
          accepted_payload_digest: live.payload_digest,
          accepted: true, status: "accepted",
          portfolio_ref: args.portfolio_ref,
          // The attestation the acceptance bound, named on the receipt result so
          // a consumer can see WHICH evaluator's judgement stands behind the
          // passing review — and, from the field name alone, that what stands
          // behind it is an attributed assertion rather than a verification this
          // database performed.
          // The Gate Zero binding this acceptance fell strictly after, named on
          // the result so a consumer can see WHICH oracle run stands behind it
          // rather than being told a boolean. The database re-derived the same
          // values inside ops.benchmark_accept_manifest_draft and wrote those;
          // these are the ones this rail read on the way in, and the two agree
          // or the definer call would have refused.
          gate_zero_step_ref: gateZero.step_ref,
          gate_zero_outcome_digest: gateZero.outcome_digest,
          gate_zero_observed_at: gateZero.observed_at,
          measurement_coverage_proved_by: coverage.coverage_proved_by,
          measurement_coverage_attested_over: coverage.measurement_set_digest,
          measurement_coverage_binding_proves: BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT.remaining_trust_boundary,
          // What that portfolio binding proves, carried on the result so a
          // consumer does not read descent into it. See the header.
          portfolio_binding_proves: "the named portfolio constitution is accepted and intact; not that this benchmark descends from it",
          // Acceptance's OWN effects, not the shared record-layer ones: it
          // closes the benchmark gate for this draft and freezes the draft's
          // content. It does not start the Journey 1 clock — that origin is the
          // first current passing foundation-assurance-minimum receipt, which
          // this rail neither issues nor reaches.
          effects: ACCEPTANCE_EFFECTS,
        };
      }),
    },
  };
}
