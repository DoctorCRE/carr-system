// DoctorCRE v5 slice V5-A03 — durable independent review and bounded
// adjudication.  The pure policy module remains the closed description of the
// four seams.  This module is the record-layer front door that binds those
// seams to append-only PostgreSQL state; no caller-supplied object can act as a
// registry, ledger, or receipt store.

import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_CONTEXT_BINDINGS,
  V5_REVIEW_DIMENSIONS,
  V5_REVIEW_ROLES,
  V5_REVIEW_STATES,
  V5_SUBMISSION_STATES,
} from "./complete-set-review-a03.vocabulary.v5.js";

export const V5_A03_STORE_SCHEMA_VERSION = "doctorcre-v5-complete-set-review-store.v1";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const REF = /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const SESSION_REF = /^session:[A-Za-z0-9][A-Za-z0-9._:-]{1,119}$/;
const ADJUDICATION_OUTCOMES = Object.freeze(["fail", "pass", "quarantine"]);

function sortedUnique(values) {
  return Array.isArray(values) && values.every(value => typeof value === "string" && value.length > 0)
    && values.every((value, index) => index === 0 || values[index - 1] < value);
}

// Identity separation, complete finding batches, the two-round bound and the
// adjudicator's non-party status are decided ONLY by the SECURITY DEFINER
// functions in migration 0719, against append-only rows. An earlier revision
// carried pure JavaScript "mirrors" of those guards that no handler called;
// they were deleted rather than wired in, because a second copy of a gate
// that reads caller-shaped objects is a second authority, not a check.

function exactArgs(args, allowed, ToolError) {
  if (!args || typeof args !== "object" || Array.isArray(args)) throw new ToolError({ error: "invalid_arguments" });
  const extra = Object.keys(args).filter(key => !allowed.includes(key));
  if (extra.length) throw new ToolError({ error: "unregistered_field", fields: extra.sort() });
}

function requiredString(value, name, pattern, ToolError) {
  if (typeof value !== "string" || !pattern.test(value)) throw new ToolError({ error: "invalid_field", field: name });
  return value;
}

function requiredOrdinal(value, name, ToolError) {
  if (!Number.isInteger(value) || value < 1) throw new ToolError({ error: "invalid_field", field: name });
  return value;
}

function requireStringList(value, name, ToolError, { allowEmpty = true } = {}) {
  if (!sortedUnique(value) || (!allowEmpty && value.length === 0))
    throw new ToolError({ error: "invalid_field", field: name });
  return value;
}

function resultRow(result, ToolError, error) {
  const row = result.rows[0];
  if (!row) throw new ToolError({ error });
  return row;
}

const IDEM = "idempotency_key";

export function completeSetReviewA03StoreTools({ withEnvelope, writeEvent, ToolError }) {
  const event = typeof writeEvent === "function" ? writeEvent : async () => {};
  return {
    "open-complete-set-review": {
      write: true,
      description: "Open one V5-A03 review case over an immutable delivered-set digest. The maker actor is server-derived; the caller supplies only its canonical session reference. A change_ref that already has a case, or a digest any case has already delivered or produced, is refused: the two-round bound is per change.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, change_ref: { type: "string" },
        delivered_set_digest: { type: "string" }, maker_session_ref: { type: "string" },
      }, required: [IDEM, "change_ref", "delivered_set_digest", "maker_session_ref"] },
      handler: async (c, actor, args) => {
        exactArgs(args, [IDEM, "change_ref", "delivered_set_digest", "maker_session_ref"], ToolError);
        requiredString(args.idempotency_key, IDEM, UUID, ToolError);
        requiredString(args.change_ref, "change_ref", REF, ToolError);
        requiredString(args.delivered_set_digest, "delivered_set_digest", SHA256_REF, ToolError);
        requiredString(args.maker_session_ref, "maker_session_ref", SESSION_REF, ToolError);
        return withEnvelope(c, actor, "open-complete-set-review", args, async () => {
          const row = resultRow(await c.query(
            "select * from ops.v5_a03_open_review_case($1::text,$2::text,$3::text,$4::uuid,$5::uuid)",
            [args.change_ref, args.delivered_set_digest, args.maker_session_ref, args.idempotency_key, actor.id]),
          ToolError, "complete_set_review_not_opened");
          await event(c, actor, "open-complete-set-review", "v5_a03_review_case", row.case_id,
            { new: { status: row.status, change_ref: args.change_ref }, idempotency_key: args.idempotency_key });
          return { ok: true, schema_version: V5_A03_STORE_SCHEMA_VERSION, ...row };
        });
      },
    },

    "record-complete-set-participant": {
      write: true,
      description: "Register the authenticated actor's own V5-A03 duty and session. Reviewers register one accepted dimension with fresh context; no actor identity is accepted from the caller.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, case_id: { type: "string" }, role: { type: "string", enum: [...V5_REVIEW_ROLES] },
        dimension: { type: ["string", "null"], enum: [...V5_REVIEW_DIMENSIONS, null] },
        session_ref: { type: "string" }, context_binding: { type: ["string", "null"], enum: [...V5_CONTEXT_BINDINGS, null] },
      }, required: [IDEM, "case_id", "role", "session_ref"] },
      handler: async (c, actor, args) => {
        exactArgs(args, [IDEM, "case_id", "role", "dimension", "session_ref", "context_binding"], ToolError);
        requiredString(args.idempotency_key, IDEM, UUID, ToolError);
        requiredString(args.case_id, "case_id", UUID, ToolError);
        requiredString(args.session_ref, "session_ref", SESSION_REF, ToolError);
        if (!V5_REVIEW_ROLES.includes(args.role)) throw new ToolError({ error: "invalid_field", field: "role" });
        if (args.role === "reviewer" && (!V5_REVIEW_DIMENSIONS.includes(args.dimension) || args.context_binding !== "fresh"))
          throw new ToolError({ error: "review_context_not_fresh" });
        if (args.role !== "reviewer" && (args.dimension != null || args.context_binding != null))
          throw new ToolError({ error: "participant_dimension_only_for_reviewer" });
        return withEnvelope(c, actor, "record-complete-set-participant", args, async () => {
          const row = resultRow(await c.query(
            "select * from ops.v5_a03_record_participant($1::uuid,$2::text,$3::text,$4::text,$5::text,$6::uuid,$7::uuid)",
            [args.case_id, args.role, args.dimension ?? null, args.session_ref, args.context_binding ?? null,
              args.idempotency_key, actor.id]), ToolError, "complete_set_participant_not_recorded");
          return { ok: true, schema_version: V5_A03_STORE_SCHEMA_VERSION, ...row };
        });
      },
    },

    "record-complete-set-finding-set": {
      write: true,
      description: "Append one review dimension's entire finding set for a numbered round, bound to the authenticated reviewer participant and to the artifact that round reviews: the delivered-set digest in round 1, round 1's post-repair artifact digest in round 2.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, case_id: { type: "string" }, round_ordinal: { type: "integer" },
        dimension: { type: "string", enum: [...V5_REVIEW_DIMENSIONS] }, reviewer_session_ref: { type: "string" },
        reviewed_set_digest: { type: "string" }, state: { type: "string", enum: [...V5_SUBMISSION_STATES] },
        finding_refs: { type: "array", uniqueItems: true, items: { type: "string" } }, enumerated_before_repair: { type: "boolean" },
      }, required: [IDEM, "case_id", "round_ordinal", "dimension", "reviewer_session_ref",
        "reviewed_set_digest", "state", "finding_refs", "enumerated_before_repair"] },
      handler: async (c, actor, args) => {
        exactArgs(args, [IDEM, "case_id", "round_ordinal", "dimension", "reviewer_session_ref",
          "reviewed_set_digest", "state", "finding_refs", "enumerated_before_repair"], ToolError);
        requiredString(args.idempotency_key, IDEM, UUID, ToolError); requiredString(args.case_id, "case_id", UUID, ToolError);
        requiredOrdinal(args.round_ordinal, "round_ordinal", ToolError);
        if (!V5_REVIEW_DIMENSIONS.includes(args.dimension) || args.state !== "submitted" || args.enumerated_before_repair !== true)
          throw new ToolError({ error: "invalid_finding_set" });
        requiredString(args.reviewer_session_ref, "reviewer_session_ref", SESSION_REF, ToolError);
        requiredString(args.reviewed_set_digest, "reviewed_set_digest", SHA256_REF, ToolError);
        requireStringList(args.finding_refs, "finding_refs", ToolError);
        return withEnvelope(c, actor, "record-complete-set-finding-set", args, async () => {
          const row = resultRow(await c.query(
            "select * from ops.v5_a03_record_finding_set($1::uuid,$2::integer,$3::text,$4::text,$5::text,$6::text,$7::text[],$8::boolean,$9::uuid,$10::uuid)",
            [args.case_id, args.round_ordinal, args.dimension, args.reviewer_session_ref,
              args.reviewed_set_digest, args.state, args.finding_refs, args.enumerated_before_repair,
              args.idempotency_key, actor.id]), ToolError, "complete_set_finding_set_not_recorded");
          return { ok: true, schema_version: V5_A03_STORE_SCHEMA_VERSION, ...row };
        });
      },
    },

    "seal-complete-set-review-round": {
      write: true,
      description: "Seal one complete eleven-dimension round after one batch repair and a non-weakened regression. The record layer derives prior checks and refuses gaps, weakened checks, third rounds, or caller-supplied counts; round-2 drift (repeated finding, circular reversion, reviewer instability) is recorded and routes the case to stronger adjudication, and a clean drift-free round records pass.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, case_id: { type: "string" }, round_ordinal: { type: "integer" },
        batch_repair_digest: { type: "string" }, repaired_finding_refs: { type: "array", uniqueItems: true, items: { type: "string" } },
        regression_suite_ref: { type: "string" }, checks_executed: { type: "array", minItems: 1, uniqueItems: true, items: { type: "string" } },
        post_repair_artifact_digest: { type: "string" }, state: { type: "string", enum: [...V5_REVIEW_STATES] },
      }, required: [IDEM, "case_id", "round_ordinal", "batch_repair_digest", "repaired_finding_refs",
        "regression_suite_ref", "checks_executed", "post_repair_artifact_digest", "state"] },
      handler: async (c, actor, args) => {
        exactArgs(args, [IDEM, "case_id", "round_ordinal", "batch_repair_digest", "repaired_finding_refs",
          "regression_suite_ref", "checks_executed", "post_repair_artifact_digest", "state"], ToolError);
        requiredString(args.idempotency_key, IDEM, UUID, ToolError); requiredString(args.case_id, "case_id", UUID, ToolError);
        requiredOrdinal(args.round_ordinal, "round_ordinal", ToolError);
        requiredString(args.batch_repair_digest, "batch_repair_digest", SHA256_REF, ToolError);
        requiredString(args.post_repair_artifact_digest, "post_repair_artifact_digest", SHA256_REF, ToolError);
        requiredString(args.regression_suite_ref, "regression_suite_ref", REF, ToolError);
        requireStringList(args.repaired_finding_refs, "repaired_finding_refs", ToolError);
        requireStringList(args.checks_executed, "checks_executed", ToolError, { allowEmpty: false });
        if (!V5_REVIEW_STATES.includes(args.state)) throw new ToolError({ error: "invalid_field", field: "state" });
        return withEnvelope(c, actor, "seal-complete-set-review-round", args, async () => {
          const row = resultRow(await c.query(
            "select * from ops.v5_a03_seal_review_round($1::uuid,$2::integer,$3::text,$4::text[],$5::text,$6::text[],$7::text,$8::text,$9::uuid,$10::uuid)",
            [args.case_id, args.round_ordinal, args.batch_repair_digest, args.repaired_finding_refs,
              args.regression_suite_ref, args.checks_executed, args.post_repair_artifact_digest,
              args.state, args.idempotency_key, actor.id]), ToolError, "complete_set_review_round_not_sealed");
          return { ok: true, schema_version: V5_A03_STORE_SCHEMA_VERSION, ...row };
        });
      },
    },

    "record-complete-set-adjudication": {
      write: true,
      description: "After exactly two sealed rounds that ended unresolved or with recorded drift, append the authenticated stronger adjudicator's pass, fail, or quarantine disposition as the case outcome. The database refuses any identity holding another duty on the case, including the sealing program controller, and records a content digest.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, case_id: { type: "string" }, adjudicator_session_ref: { type: "string" },
        outcome: { type: "string", enum: [...ADJUDICATION_OUTCOMES] },
        disputed_finding_refs: { type: "array", minItems: 1, uniqueItems: true, items: { type: "string" } },
      }, required: [IDEM, "case_id", "adjudicator_session_ref", "outcome", "disputed_finding_refs"] },
      handler: async (c, actor, args) => {
        exactArgs(args, [IDEM, "case_id", "adjudicator_session_ref", "outcome", "disputed_finding_refs"], ToolError);
        requiredString(args.idempotency_key, IDEM, UUID, ToolError); requiredString(args.case_id, "case_id", UUID, ToolError);
        requiredString(args.adjudicator_session_ref, "adjudicator_session_ref", SESSION_REF, ToolError);
        if (!ADJUDICATION_OUTCOMES.includes(args.outcome)) throw new ToolError({ error: "invalid_field", field: "outcome" });
        requireStringList(args.disputed_finding_refs, "disputed_finding_refs", ToolError, { allowEmpty: false });
        return withEnvelope(c, actor, "record-complete-set-adjudication", args, async () => {
          const row = resultRow(await c.query(
            "select * from ops.v5_a03_record_adjudication($1::uuid,$2::text,$3::text,$4::text[],$5::uuid,$6::uuid)",
            [args.case_id, args.adjudicator_session_ref, args.outcome, args.disputed_finding_refs,
              args.idempotency_key, actor.id]), ToolError, "complete_set_adjudication_not_recorded");
          return { ok: true, schema_version: V5_A03_STORE_SCHEMA_VERSION, ...row };
        });
      },
    },

    "read-complete-set-review": {
      writerConnection: true,
      description: "Read one authoritative V5-A03 review case: server-derived participants, eleven-dimension submissions, sealed rounds, drift findings, batch/regression evidence, and any stronger-adjudicator disposition.",
      inputSchema: { type: "object", additionalProperties: false, properties: { case_id: { type: "string" } }, required: ["case_id"] },
      handler: async (c, _actor, args) => {
        exactArgs(args, ["case_id"], ToolError); requiredString(args.case_id, "case_id", UUID, ToolError);
        const review = (await c.query("select ops.v5_a03_read_review_case($1::uuid) as review", [args.case_id])).rows[0]?.review;
        if (!review || typeof review !== "object") throw new ToolError({ error: "complete_set_review_not_found" });
        return { ok: true, schema_version: V5_A03_STORE_SCHEMA_VERSION, review, effects: V5_NO_EFFECTS };
      },
    },
  };
}
