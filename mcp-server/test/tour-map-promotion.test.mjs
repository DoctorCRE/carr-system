import assert from "node:assert/strict";
import test from "node:test";
import { tourMapPromotionTools } from "../src/tour-map-promotion.js";

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const actor = {
  id: "10000000-0000-4000-8000-000000000001", slug: "joe", human: true,
  authorization_class: "verified_partner", organization_tenant_id: "carr-internal",
};
const checks = Object.fromEntries([
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
].map(key => [key, true]));
const args = {
  idempotency_key: "20000000-0000-4000-8000-000000000001",
  projection_id: "30000000-0000-4000-8000-000000000001",
  decision: "approved", reviewed_at: "2026-08-28T12:00:00.000Z",
  decision_reason: "All required map promotion checks passed.",
  brief_version: "tour-map-brief.v1", canonical_dataset_version: "dataset-v7",
  selected_prototype_id: "carr-map-tour-v1", component_registry_version: "maplibre-6.4.1",
  route_version: 7,
  provider_rights_receipt_ids: ["40000000-0000-4000-8000-000000000001"],
  mobile_test_evidence: { status: "passed", phone: "passed", ipad: "passed", digest: `sha256:${"1".repeat(64)}` },
  native_navigation_test_evidence: { status: "passed", apple_maps: "passed", digest: `sha256:${"2".repeat(64)}` },
  offline_test_evidence: { status: "passed", ordered_itinerary: "passed", digest: `sha256:${"3".repeat(64)}` },
  required_checks: checks, receipt_digest: `sha256:${"4".repeat(64)}`,
};

function harness() {
  const calls = [], events = [];
  const client = { query: async (sql, params) => { calls.push({ sql, params }); return { rows: [{ promotion_receipt_id: "50000000-0000-4000-8000-000000000001" }] }; } };
  const withEnvelope = async (_client, _actor, _verb, _args, fn) => fn();
  return { client, calls, events, tools: tourMapPromotionTools({ withEnvelope, writeEvent: async (...event) => events.push(event), ToolError }) };
}

test("map promotion records the complete verified-human doctrine receipt", async () => {
  const h = harness(); const tool = h.tools["record-tour-map-promotion-receipt"];
  assert.equal(tool.write, true); assert.equal(tool.authorityOnly, true); assert.equal(tool.humanOnly, true);
  const result = await tool.handler(h.client, actor, structuredClone(args));
  assert.deepEqual(result, { ok: true, promotion_receipt_id: "50000000-0000-4000-8000-000000000001", decision: "approved" });
  assert.match(h.calls[0].sql, /record_tour_map_promotion_receipt/);
  assert.equal(h.calls[0].params[0], "carr-internal");
  assert.equal(h.calls[0].params[1], args.projection_id);
  assert.deepEqual(JSON.parse(h.calls[0].params[2]).required_checks, checks);
  assert.equal(h.calls[0].params[3], actor.id);
  assert.equal(h.events.length, 1);
});

test("map promotion refuses machine actors, incomplete approvals, and nested authority selectors", async () => {
  const tool = harness().tools["record-tour-map-promotion-receipt"];
  await assert.rejects(tool.handler(harness().client, { ...actor, human: false, authorization_class: "sponsored_agent" }, structuredClone(args)),
    error => error instanceof ToolError && error.payload.error === "tour_verified_human_required");
  await assert.rejects(tool.handler(harness().client, actor, { ...structuredClone(args), required_checks: { ...checks, exact_native_navigation_handoff: false } }),
    error => error instanceof ToolError && error.payload.error === "tour_map_promotion_checks_incomplete");
  await assert.rejects(tool.handler(harness().client, actor, { ...structuredClone(args), mobile_test_evidence: { actor_id: "caller-chosen" } }),
    error => error instanceof ToolError && error.payload.error === "caller_authority_field_forbidden");
});

test("map rejection records a bounded reason and at least one truthful failed check", async () => {
  const h = harness(); const tool = h.tools["record-tour-map-promotion-receipt"];
  const rejected = structuredClone(args);
  rejected.decision = "rejected";
  rejected.decision_reason = "Native navigation handoff failed on the reviewed device.";
  rejected.required_checks.exact_native_navigation_handoff = false;
  rejected.native_navigation_test_evidence.status = "failed";
  const result = await tool.handler(h.client, actor, rejected);
  assert.equal(result.decision, "rejected");
  assert.equal(JSON.parse(h.calls[0].params[2]).decision_reason, rejected.decision_reason);

  await assert.rejects(tool.handler(harness().client, actor, { ...structuredClone(args), decision: "rejected" }),
    error => error instanceof ToolError && error.payload.error === "tour_map_promotion_checks_incomplete");
  await assert.rejects(tool.handler(harness().client, actor, { ...structuredClone(rejected), decision_reason: "" }),
    error => error instanceof ToolError && error.payload.error === "tour_input_invalid" && error.payload.field === "decision_reason");
});
