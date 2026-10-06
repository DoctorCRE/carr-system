// Human-only, append-only promotion receipt for client/public Tour maps.
// Coordinate verification and projection sealing are necessary but insufficient:
// this receipt binds the full mobile/native/offline/component/route doctrine gate.

import { authorizationClassForActor, organizationTenantForActor } from "./identity.js";
import { requiredTimestamp } from "./tour-operations-contract.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const AUTHORITY_FIELDS = new Set([
  "tenant", "tenant_id", "organization_tenant_id", "actor", "actor_id", "reviewer",
  "identity", "authorization", "authorization_class", "sponsor", "human_slug",
]);
const REQUIRED_CHECKS = Object.freeze([
  "canonical_address_and_coordinate_review",
  "claims_and_layers_have_source_as_of_rights_and_review_state",
  "deterministic_rebuild_from_canonical_record",
  "exact_native_navigation_handoff",
  "locked_appointments_dwell_and_buffers_preserved",
  "map_list_route_offline_order_parity",
  "no_unresolved_route_critical_unknown_or_conflict",
  "optional_context_layers_progressively_disclosed",
  "ordered_offline_itinerary_verified",
  "phone_and_ipad_interaction_test",
  "provider_terms_attribution_expiry_and_cost_gate_passed",
]);
const FIELDS = new Set([
  "idempotency_key", "projection_id", "decision", "reviewed_at", "brief_version",
  "decision_reason",
  "canonical_dataset_version", "selected_prototype_id", "component_registry_version",
  "route_version", "provider_rights_receipt_ids", "mobile_test_evidence",
  "native_navigation_test_evidence", "offline_test_evidence", "required_checks",
  "receipt_digest",
]);

function fail(ToolError, payload) { throw new ToolError(payload); }
function exact(args, ToolError) {
  if (!args || typeof args !== "object" || Array.isArray(args)) fail(ToolError, { error: "tour_input_invalid", field: "payload" });
  const keys = Object.keys(args);
  const authority = keys.filter(key => AUTHORITY_FIELDS.has(key));
  if (authority.length) fail(ToolError, { error: "caller_authority_field_forbidden", fields: authority });
  const unknown = keys.filter(key => !FIELDS.has(key));
  const missing = [...FIELDS].filter(key => !Object.hasOwn(args, key));
  if (unknown.length) fail(ToolError, { error: "tour_input_unknown_field", fields: unknown });
  if (missing.length) fail(ToolError, { error: "tour_input_missing_field", fields: missing });
}
function text(value, field, ToolError, maximum = 200) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maximum || /[\u0000-\u001f\u007f]/.test(value))
    fail(ToolError, { error: "tour_input_invalid", field });
  return value.trim();
}
function uuid(value, field, ToolError) {
  const candidate = text(value, field, ToolError, 64);
  if (!UUID.test(candidate)) fail(ToolError, { error: "tour_input_invalid", field });
  return candidate;
}
function digest(value, field, ToolError) {
  const candidate = text(value, field, ToolError, 80);
  if (!DIGEST.test(candidate)) fail(ToolError, { error: "tour_input_invalid", field });
  return candidate;
}
function evidence(value, field, ToolError) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !Object.keys(value).length || JSON.stringify(value).length > 8192)
    fail(ToolError, { error: "tour_input_invalid", field });
  const stack = [value];
  while (stack.length) {
    const current = stack.pop();
    for (const [key, nested] of Object.entries(current)) {
      if (AUTHORITY_FIELDS.has(key)) fail(ToolError, { error: "caller_authority_field_forbidden", fields: [key] });
      if (nested && typeof nested === "object") stack.push(nested);
    }
  }
  return structuredClone(value);
}
function tenant(actor, ToolError) {
  if (actor?.human !== true || authorizationClassForActor(actor) !== "verified_partner" || typeof actor.id !== "string" || !actor.id.trim())
    fail(ToolError, { error: "tour_verified_human_required" });
  const value = organizationTenantForActor(actor);
  if (typeof value !== "string" || !value) fail(ToolError, { error: "tour_tenant_context_required" });
  return value;
}

export function tourMapPromotionTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "record-tour-map-promotion-receipt": {
      write: true,
      authorityOnly: true,
      humanOnly: true,
      description: "Record an immutable verified-human Tour map promotion decision bound to exact projection, dataset, route, provider-rights, component, mobile, native-navigation, offline, and doctrine-check evidence. Approvals require every check to pass; rejections require a failed check and a reason. This receipt alone does not issue a share.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" }, projection_id: { type: "string" },
          decision: { type: "string", enum: ["approved", "rejected"] }, reviewed_at: { type: "string" },
          decision_reason: { type: "string", minLength: 1, maxLength: 500 },
          brief_version: { type: "string" }, canonical_dataset_version: { type: "string" },
          selected_prototype_id: { type: "string" }, component_registry_version: { type: "string" },
          route_version: { type: "integer", minimum: 1 },
          provider_rights_receipt_ids: { type: "array", minItems: 1, maxItems: 100, uniqueItems: true, items: { type: "string" } },
          mobile_test_evidence: { type: "object" }, native_navigation_test_evidence: { type: "object" },
          offline_test_evidence: { type: "object" },
          required_checks: { type: "object", additionalProperties: false,
            properties: Object.fromEntries(REQUIRED_CHECKS.map(key => [key, { type: "boolean" }])), required: [...REQUIRED_CHECKS] },
          receipt_digest: { type: "string" },
        },
        required: [...FIELDS],
      },
      handler: async (client, actor, args) => {
        exact(args, ToolError);
        uuid(args.idempotency_key, "idempotency_key", ToolError);
        const checks = evidence(args.required_checks, "required_checks", ToolError);
        if (Object.keys(checks).sort().join("\n") !== [...REQUIRED_CHECKS].sort().join("\n") ||
            REQUIRED_CHECKS.some(key => typeof checks[key] !== "boolean"))
          fail(ToolError, { error: "tour_map_promotion_checks_incomplete" });
        if (!Array.isArray(args.provider_rights_receipt_ids) || !args.provider_rights_receipt_ids.length ||
            args.provider_rights_receipt_ids.length > 100 || new Set(args.provider_rights_receipt_ids).size !== args.provider_rights_receipt_ids.length)
          fail(ToolError, { error: "tour_input_invalid", field: "provider_rights_receipt_ids" });
        const decision = ["approved", "rejected"].includes(args.decision) ? args.decision : fail(ToolError, { error: "tour_input_invalid", field: "decision" });
        const mobileEvidence = evidence(args.mobile_test_evidence, "mobile_test_evidence", ToolError);
        const nativeEvidence = evidence(args.native_navigation_test_evidence, "native_navigation_test_evidence", ToolError);
        const offlineEvidence = evidence(args.offline_test_evidence, "offline_test_evidence", ToolError);
        const failedCheck = REQUIRED_CHECKS.some(key => checks[key] === false) ||
          [mobileEvidence, nativeEvidence, offlineEvidence].some(item => item.status === "failed");
        const allPassed = REQUIRED_CHECKS.every(key => checks[key] === true) &&
          [mobileEvidence, nativeEvidence, offlineEvidence].every(item => item.status === "passed");
        if ((decision === "approved" && !allPassed) || (decision === "rejected" && !failedCheck))
          fail(ToolError, { error: "tour_map_promotion_checks_incomplete" });
        const payload = {
          decision,
          decision_reason: text(args.decision_reason, "decision_reason", ToolError, 500),
          reviewed_at: requiredTimestamp(args.reviewed_at) ? args.reviewed_at : fail(ToolError, { error: "tour_input_invalid", field: "reviewed_at" }),
          brief_version: text(args.brief_version, "brief_version", ToolError),
          canonical_dataset_version: text(args.canonical_dataset_version, "canonical_dataset_version", ToolError),
          selected_prototype_id: text(args.selected_prototype_id, "selected_prototype_id", ToolError),
          component_registry_version: text(args.component_registry_version, "component_registry_version", ToolError),
          route_version: Number.isInteger(args.route_version) && args.route_version > 0 ? args.route_version : fail(ToolError, { error: "tour_input_invalid", field: "route_version" }),
          provider_rights_receipt_ids: args.provider_rights_receipt_ids.map(value => uuid(value, "provider_rights_receipt_ids", ToolError)),
          mobile_test_evidence: mobileEvidence,
          native_navigation_test_evidence: nativeEvidence,
          offline_test_evidence: offlineEvidence,
          required_checks: checks,
          receipt_digest: digest(args.receipt_digest, "receipt_digest", ToolError),
        };
        const projectionId = uuid(args.projection_id, "projection_id", ToolError);
        const organizationTenant = tenant(actor, ToolError);
        return withEnvelope(client, actor, "record-tour-map-promotion-receipt", args, async () => {
          const result = await client.query(
            "select ops.record_tour_map_promotion_receipt($1::text,$2::uuid,$3::jsonb,$4::text) as promotion_receipt_id /* tour-map-promotion:record */",
            [organizationTenant, projectionId, JSON.stringify(payload), actor.id],
          );
          const id = result.rows[0]?.promotion_receipt_id;
          if (!UUID.test(id || "")) fail(ToolError, { error: "tour_write_refused", entity: "map_promotion_receipt" });
          await writeEvent(client, actor, "record-tour-map-promotion-receipt", "tour_map_promotion_receipt", id, {
            field: "decision", new: { decision: payload.decision, projection_id: projectionId, route_version: payload.route_version },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, promotion_receipt_id: id, decision: payload.decision };
        });
      },
    },
  };
}
