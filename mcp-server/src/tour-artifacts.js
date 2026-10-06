// Immutable Tour PDF render-request and human-review record seams. Rendering,
// queue ownership, R2 access, QC execution, and publication remain separate.

import { authorizationClassForActor, organizationTenantForActor } from "./identity.js";
import { requiredTimestamp } from "./tour-operations-contract.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$/;
const ARTIFACT_REF = /^artifact:tour-pdf:[A-Za-z0-9_-]{16,128}$/;
// PostgreSQL's regex engine caps a repetition count at 255 (RE_DUP_MAX); this
// bound must never exceed the database's matching constraint (migration
// 0583) or a value this check accepts can still raise SQLSTATE 2201B
// (invalid_regular_expression) in ops.record_tour_pdf_render_result.
const STORAGE_REF = /^tour-pdf\/[A-Za-z0-9._/-]{16,255}\.pdf$/;
const STATUSES = new Set(["queued", "rendering", "qc_blocked", "review_ready", "rejected", "available", "failed"]);
const DECISIONS = new Set(["accept", "reject"]);
const AUTHORITY = new Set(["tenant", "tenant_id", "organization_tenant_id", "actor", "actor_id", "reviewer", "identity", "authorization", "authorization_class", "sponsor", "human_slug"]);
const REQUEST_FIELDS = new Set([
  "idempotency_key", "projection_id", "projection_digest", "packet_digest",
  "template_version", "template_digest", "renderer_version", "renderer_digest",
  "qc_ruleset_version", "qc_ruleset_digest", "expected_property_count",
  "expected_markers_digest", "expected_asset_digests", "expected_font_digests",
]);
const READ_FIELDS = new Set(["render_job_id"]);
const RESULT_FIELDS = new Set([
  "idempotency_key", "render_job_id", "status", "artifact_ref", "artifact_digest",
  "storage_ref", "content_length", "page_count", "blocking_finding_count", "qc_run_digest",
]);
const REVIEW_FIELDS = new Set([
  "idempotency_key", "render_job_id", "qc_run_digest", "decision",
  "reviewed_at", "review_receipt_digest", "reason",
]);

function fail(ToolError, payload) { throw new ToolError(payload); }
function exact(args, fields, ToolError) {
  if (!args || typeof args !== "object" || Array.isArray(args)) fail(ToolError, { error: "tour_input_invalid", field: "payload" });
  const keys = Object.keys(args);
  const authority = keys.filter(key => AUTHORITY.has(key));
  if (authority.length) fail(ToolError, { error: "caller_authority_field_forbidden", fields: authority });
  const unknown = keys.filter(key => !fields.has(key));
  const missing = [...fields].filter(key => !Object.hasOwn(args, key));
  if (unknown.length) fail(ToolError, { error: "tour_input_unknown_field", fields: unknown });
  if (missing.length) fail(ToolError, { error: "tour_input_missing_field", fields: missing });
}
function text(value, field, ToolError, maximum = 240) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maximum || /[\u0000-\u001F\u007F]/.test(value)) fail(ToolError, { error: "tour_input_invalid", field });
  return value.trim();
}
function uuid(value, field, ToolError) { const item = text(value, field, ToolError, 64); if (!UUID.test(item)) fail(ToolError, { error: "tour_input_invalid", field }); return item; }
function digest(value, field, ToolError) { const item = text(value, field, ToolError, 80); if (!DIGEST.test(item)) fail(ToolError, { error: "tour_input_invalid", field }); return item; }
function version(value, field, ToolError) { const item = text(value, field, ToolError, 80); if (!VERSION.test(item)) fail(ToolError, { error: "tour_input_invalid", field }); return item; }
function timestamp(value, field, ToolError) { const item = text(value, field, ToolError, 64); if (!requiredTimestamp(item)) fail(ToolError, { error: "tour_input_invalid", field }); return item; }
function digestList(value, field, ToolError, maximum, minimum = 0) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum || new Set(value).size !== value.length || value.some(item => !DIGEST.test(item))) fail(ToolError, { error: "tour_input_invalid", field });
  return [...value];
}
function tenant(actor, ToolError) {
  if (!actor || typeof actor.id !== "string" || !actor.id.trim()) fail(ToolError, { error: "tour_actor_context_required" });
  const value = organizationTenantForActor(actor);
  if (typeof value !== "string" || !value) fail(ToolError, { error: "tour_tenant_context_required" });
  return value;
}
function verifiedHuman(actor, ToolError) {
  if (actor?.human !== true ||
      (actor.authorization_class || authorizationClassForActor(actor)) !== "verified_partner")
    fail(ToolError, { error: "tour_verified_human_required" });
}

// The renderer result verb remains in the common registry so it receives the
// normal authority connection, transaction, envelope, and audit treatment.
// Only the in-process renderer can attach this non-serializable capability;
// JSON arriving through MCP or the browser can never reproduce it.
const TRUSTED_RENDERER_RESULT = Symbol("trusted-tour-renderer-result");
export function trustedTourRendererResult(args) {
  const value = { ...args };
  Object.defineProperty(value, TRUSTED_RENDERER_RESULT, { value: true });
  return value;
}
function requireTrustedRendererResult(args, ToolError) {
  if (args?.[TRUSTED_RENDERER_RESULT] !== true)
    fail(ToolError, { error: "tour_trusted_renderer_required" });
}
function projectRender(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !UUID.test(value.render_job_id || "") || !STATUSES.has(value.status)) return null;
  const output = { render_job_id: value.render_job_id, status: value.status };
  if (ARTIFACT_REF.test(value.artifact_ref || "")) output.artifact_ref = value.artifact_ref;
  for (const field of ["artifact_digest", "projection_digest", "template_digest", "renderer_digest", "qc_ruleset_digest", "qc_run_digest"]) if (DIGEST.test(value[field] || "")) output[field] = value[field];
  for (const field of ["created_at", "updated_at", "completed_at", "reviewed_at"]) if (typeof value[field] === "string") output[field] = value[field];
  for (const field of ["expected_property_count", "page_count", "blocking_finding_count", "attempt_count"]) if (Number.isInteger(value[field]) && value[field] >= 0) output[field] = value[field];
  if (["pending", "accepted", "rejected"].includes(value.human_review_state)) output.human_review_state = value.human_review_state;
  return output;
}
const schema = (properties, required) => ({ type: "object", additionalProperties: false, properties, required });
const idempotency = { idempotency_key: { type: "string" } };

export function tourArtifactTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "request-tour-pdf-render": {
      write: true,
      description: "Request an immutable PDF render from a sealed facts-only projection and pinned render inputs. This does not render, approve, share, or publish.",
      inputSchema: schema({ ...idempotency,
        projection_id: { type: "string" }, projection_digest: { type: "string" }, packet_digest: { type: "string" },
        template_version: { type: "string" }, template_digest: { type: "string" }, renderer_version: { type: "string" }, renderer_digest: { type: "string" },
        qc_ruleset_version: { type: "string" }, qc_ruleset_digest: { type: "string" }, expected_property_count: { type: "integer", minimum: 1, maximum: 50 },
        expected_markers_digest: { type: "string" }, expected_asset_digests: { type: "array", maxItems: 100, uniqueItems: true, items: { type: "string" } },
        expected_font_digests: { type: "array", minItems: 1, maxItems: 20, uniqueItems: true, items: { type: "string" } },
      }, [...REQUEST_FIELDS]),
      handler: async (client, actor, args) => {
        exact(args, REQUEST_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        if (!Number.isInteger(args.expected_property_count) || args.expected_property_count < 1 || args.expected_property_count > 50) fail(ToolError, { error: "tour_input_invalid", field: "expected_property_count" });
        const request = {
          projection_id: uuid(args.projection_id, "projection_id", ToolError),
          projection_digest: digest(args.projection_digest, "projection_digest", ToolError),
          packet_digest: digest(args.packet_digest, "packet_digest", ToolError),
          template_version: version(args.template_version, "template_version", ToolError),
          template_digest: digest(args.template_digest, "template_digest", ToolError),
          renderer_version: version(args.renderer_version, "renderer_version", ToolError),
          renderer_digest: digest(args.renderer_digest, "renderer_digest", ToolError),
          qc_ruleset_version: version(args.qc_ruleset_version, "qc_ruleset_version", ToolError),
          qc_ruleset_digest: digest(args.qc_ruleset_digest, "qc_ruleset_digest", ToolError),
          expected_property_count: args.expected_property_count,
          expected_markers_digest: digest(args.expected_markers_digest, "expected_markers_digest", ToolError),
          expected_asset_digests: digestList(args.expected_asset_digests, "expected_asset_digests", ToolError, 100),
          expected_font_digests: digestList(args.expected_font_digests, "expected_font_digests", ToolError, 20, 1),
        };
        return withEnvelope(client, actor, "request-tour-pdf-render", args, async () => {
          const result = await client.query(
            "select ops.request_tour_pdf_render($1::text,$2::text,$3::jsonb) as render_job_id /* tour-artifacts:request */",
            [tenant(actor, ToolError), actor.id, JSON.stringify(request)],
          );
          const id = result.rows[0]?.render_job_id;
          if (!UUID.test(id || "")) fail(ToolError, { error: "tour_write_refused", entity: "pdf_render_job" });
          await writeEvent(client, actor, "request-tour-pdf-render", "tour_pdf_render_job", id, { field: "status", new: { status: "queued", projection_id: request.projection_id, expected_property_count: request.expected_property_count }, idempotency_key: args.idempotency_key });
          return { ok: true, render_job_id: id, status: "queued" };
        });
      },
    },
    "read-tour-pdf-render": {
      writerConnection: true,
      description: "Read sanitized internal PDF render/QC state. R2 keys, leases, provider data, and session material are never returned.",
      inputSchema: schema({ render_job_id: { type: "string" } }, [...READ_FIELDS]),
      handler: async (client, actor, args) => {
        exact(args, READ_FIELDS, ToolError);
        const result = await client.query(
          "select ops.read_tour_pdf_render($1::text,$2::uuid,$3::text) as render /* tour-artifacts:read */",
          [tenant(actor, ToolError), uuid(args.render_job_id, "render_job_id", ToolError), actor.id],
        );
        const render = projectRender(result.rows[0]?.render);
        if (!render) fail(ToolError, { error: "tour_pdf_render_not_found" });
        return { ok: true, render };
      },
    },
    "record-tour-pdf-render-result": {
      write: true,
      authorityOnly: true,
      description: "Authority-bound server renderer receipt after exact R2 readback and deterministic QC. This cannot approve, publish, or grant client download authority.",
      inputSchema: schema({ ...idempotency, render_job_id: { type: "string" }, status: { type: "string", enum: ["review_ready", "qc_blocked", "failed"] }, artifact_ref: { type: ["string", "null"] }, artifact_digest: { type: ["string", "null"] }, storage_ref: { type: ["string", "null"] }, content_length: { type: ["integer", "null"], minimum: 1 }, page_count: { type: ["integer", "null"], minimum: 1, maximum: 50 }, blocking_finding_count: { type: "integer", minimum: 0 }, qc_run_digest: { type: "string" } }, [...RESULT_FIELDS]),
      handler: async (client, actor, args) => {
        requireTrustedRendererResult(args, ToolError);
        exact(args, RESULT_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const renderJobId = uuid(args.render_job_id, "render_job_id", ToolError);
        const failed = args.status === "failed";
        if (!["review_ready", "qc_blocked", "failed"].includes(args.status) ||
            !Number.isInteger(args.blocking_finding_count) || args.blocking_finding_count < 0 ||
            (failed && (args.artifact_ref !== null || args.artifact_digest !== null || args.storage_ref !== null ||
              args.content_length !== null || args.page_count !== null || args.blocking_finding_count !== 0)) ||
            (!failed && (!ARTIFACT_REF.test(args.artifact_ref || "") || !STORAGE_REF.test(args.storage_ref || "") ||
              !Number.isInteger(args.content_length) || args.content_length < 1 || !Number.isInteger(args.page_count) ||
              args.page_count < 1 || args.page_count > 50 ||
              (args.status === "review_ready" && args.blocking_finding_count !== 0))))
          fail(ToolError, { error: "tour_input_invalid", field: "render_result" });
        const artifactDigest = failed ? null : digest(args.artifact_digest, "artifact_digest", ToolError);
        const qcRunDigest = digest(args.qc_run_digest, "qc_run_digest", ToolError);
        return withEnvelope(client, actor, "record-tour-pdf-render-result", args, async () => {
          const result = await client.query(
            "select ops.record_tour_pdf_render_result($1::text,$2::uuid,$3::text,$4::text,$5::text,$6::text,$7::integer,$8::integer,$9::integer,$10::text,$11::text) as render_result_id /* tour-artifacts:render-result */",
            [tenant(actor, ToolError), renderJobId, args.status, args.artifact_ref, artifactDigest, args.storage_ref, args.content_length, args.page_count, args.blocking_finding_count, qcRunDigest, actor.id],
          );
          const id = result.rows[0]?.render_result_id;
          if (!UUID.test(id || "")) fail(ToolError, { error: "tour_write_refused", entity: "pdf_render_result" });
          await writeEvent(client, actor, "record-tour-pdf-render-result", "tour_pdf_render_result", id, { field: "status", new: { render_job_id: renderJobId, status: args.status, ...(artifactDigest ? { artifact_digest: artifactDigest } : {}), ...(args.page_count ? { page_count: args.page_count } : {}), blocking_finding_count: args.blocking_finding_count, qc_run_digest: qcRunDigest }, idempotency_key: args.idempotency_key });
          return { ok: true, render_result_id: id, render_job_id: renderJobId, status: args.status };
        });
      },
    },
    "record-tour-pdf-human-review": {
      write: true,
      humanOnly: true,
      authorityOnly: true,
      description: "Authority-only human review receipt for a completed QC run. Acceptance is not client publication or share promotion.",
      inputSchema: schema({ ...idempotency, render_job_id: { type: "string" }, qc_run_digest: { type: "string" }, decision: { type: "string", enum: [...DECISIONS] }, reviewed_at: { type: "string" }, review_receipt_digest: { type: "string" }, reason: { type: "string" } }, [...REVIEW_FIELDS]),
      handler: async (client, actor, args) => {
        verifiedHuman(actor, ToolError);
        exact(args, REVIEW_FIELDS, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        if (!DECISIONS.has(args.decision)) fail(ToolError, { error: "tour_input_invalid", field: "decision" });
        const payload = [
          tenant(actor, ToolError), uuid(args.render_job_id, "render_job_id", ToolError),
          digest(args.qc_run_digest, "qc_run_digest", ToolError), args.decision,
          timestamp(args.reviewed_at, "reviewed_at", ToolError), digest(args.review_receipt_digest, "review_receipt_digest", ToolError),
          text(args.reason, "reason", ToolError, 500), actor.id,
        ];
        return withEnvelope(client, actor, "record-tour-pdf-human-review", args, async () => {
          const result = await client.query(
            "select ops.record_tour_pdf_human_review($1::text,$2::uuid,$3::text,$4::text,$5::timestamptz,$6::text,$7::text,$8::text) as review_receipt_id /* tour-artifacts:human-review */",
            payload,
          );
          const id = result.rows[0]?.review_receipt_id;
          if (!UUID.test(id || "")) fail(ToolError, { error: "tour_write_refused", entity: "pdf_human_review" });
          await writeEvent(client, actor, "record-tour-pdf-human-review", "tour_pdf_human_review", id, { field: "decision", new: { render_job_id: payload[1], decision: args.decision, reviewed_at: payload[4] }, idempotency_key: args.idempotency_key });
          return { ok: true, review_receipt_id: id, decision: args.decision };
        });
      },
    },
  };
}
