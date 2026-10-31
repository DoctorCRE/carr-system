import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { parse } from "@babel/parser";
import { ToolError } from "../src/tool-error.ts";
import { publicError } from "../src/public-errors.ts";
import { assertClosedTopLevel } from "../src/mutation-registry.js";

// These existing refusals remain private until each has a reviewed public shape.
const INTERNAL_ONLY = new Set([
  "accept_required",
  "account_changed",
  "account_required",
  "agenda_limit_exceeded",
  "already_merged",
  "already_on_another_campaign",
  "already_scored",
  "ambiguous_file",
  "ambiguous_measurement",
  "ambiguous_placement",
  "ambiguous_ref",
  "applied_change_not_found",
  "archive_requires_partner",
  "area_out_of_band",
  "assignee_not_provisioned",
  "automatic_apply_not_allowed",
  "bad_date",
  "bad_field_map",
  "bad_metric_kind",
  "bad_metric_value",
  "bad_occurred_on",
  "bad_option",
  "below_confidence_bar",
  "calendar_history_too_large",
  "call_already_undone",
  "campaign_exists",
  "campaign_not_found",
  "campaign_required",
  "capability_no_decider",
  "channels_required",
  "choose_deal_or_all",
  "claude_owner_requires_full_profile",
  "client_merged_away",
  "client_not_found",
  "closing_outcome_needs_a_lead",
  "closing_outcome_takes_no_next",
  "code_subject_not_minted",
  "code_subject_unparseable",
  "conflict_already_resolved",
  "conflicting_party_inputs",
  "criterion_restates_goal",
  "deal_assignment_invalid",
  "deal_checklist_notification_unavailable",
  "deal_checklist_unavailable",
  "deal_evidence_deal_not_found",
  "deal_history_verification_refused",
  "deal_name_exists",
  "deal_not_found",
  "deal_outside_review_workspace",
  "defect_class_required",
  "derived_claim_type",
  "different_parties",
  "dispatcher_only",
  "do_not_contact_requires_suppression",
  "document_not_found",
  "duplicate_claim",
  "engagement_reason_required",
  "event_not_revertible",
  "evidence_required",
  "field_required",
  "filed_needs_refs",
  "find_result_invalid",
  "goal_required",
  "half_a_price",
  "human_owner_unavailable",
  "industry_event_id_invalid",
  "industry_event_limit_invalid",
  "industry_event_text_invalid",
  "industry_event_time_order_invalid",
  "industry_event_timestamp_invalid",
  "industry_event_update_empty",
  "industry_event_url_invalid",
  "industry_event_value_invalid",
  "industry_event_version_conflict",
  "industry_event_virtual_invalid",
  "inner_write_failed",
  "inner_write_missing_ref",
  "internal_decision_parked",
  "invalid_base_event",
  "invalid_engagement_status",
  "invalid_expected_action",
  "invalid_history_cursor",
  "invalid_invoiced_on",
  "invalid_lease_date",
  "invalid_lease_dates",
  "invalid_lease_evidence_kind",
  "invalid_limit",
  "invalid_mail_recipients",
  "invalid_max_depth",
  "invalid_object",
  "invalid_occurred_on",
  "invalid_owner",
  "invalid_path_limit",
  "invalid_query",
  "invalid_rule_search_limit",
  "invalid_rule_status",
  "invalid_score",
  "invalid_score_reason",
  "invalid_sf_close_date_placeholder",
  "invalid_term_months",
  "invalid_timeline_limit",
  "invalid_winner",
  "items_required",
  "last_surfaced_is_idea_only",
  "lead_client_pair",
  "lead_not_claimable",
  "lead_not_linkable",
  "lease_evidence_ref_required",
  "lease_source_required",
  "lease_write_no_readback",
  "loan_programs_invalid",
  "loop_not_found",
  "loop_open",
  "marketing_subject_not_found",
  "marketing_subject_retired",
  "match_basis_required",
  "merge_graph_attachments_require_resolution",
  "merge_survivorship_unavailable",
  "merge_vendor_assignments_require_resolution",
  "migration_not_applied",
  "missing_basis",
  "missing_loop_ref",
  "missing_merge_note",
  "missing_reason",
  "missing_source",
  "missing_target",
  "morning_brief_context_not_selectable",
  "morning_brief_requires_partner_scope",
  "name_required",
  "needs_joe_not_applicable",
  "negative_metric",
  "next_action_subject_unsupported",
  "next_step_and_date_required",
  "next_step_required",
  "no_block",
  "no_contradiction_stated",
  "no_current_lease",
  "no_deal_for_client",
  "no_export_view_readable",
  "no_measured_evidence",
  "no_op",
  "no_open_action",
  "no_open_deal",
  "no_party_under_ref",
  "no_such_section",
  "no_updatable_fields",
  "not_a_client",
  "not_a_deal_or_client",
  "not_a_lead",
  "not_a_national_account",
  "not_a_national_account_deal",
  "not_a_party",
  "not_a_prospect",
  "not_a_vendor",
  "not_applied_automatically",
  "not_attached",
  "not_deal_owner",
  "not_declinable",
  "not_promotable",
  "nothing_to_record",
  "nothing_to_update",
  "number_taken",
  "occurred_at_in_future",
  "one_party_two_vendor_rows",
  "outbound_format",
  "outcome_too_short",
  "owner_not_permitted",
  "ownership_needs_party",
  "partner_required",
  "party_already_merged",
  "party_not_found",
  "phase_history_undo_unavailable",
  "phase_start_undo_unavailable",
  "placeholder_email",
  "placeholder_phone",
  "placement_not_found",
  "placement_required",
  "post_call_followups_limit_exceeded",
  "prospect_lead_protected",
  "prospect_party_protected",
  "query_required",
  "reason_required",
  "reattach_is_one_at_a_time",
  "ref_not_found",
  "ref_required",
  "referral_broker_mismatch",
  "referral_deal_invalid",
  "renumber_reason_required",
  "research_evidence_incomplete",
  "research_evidence_required",
  "research_source_invalid",
  "reserved_source",
  "review_session_not_open",
  "rule_enforcement_fallback_actor_unregistered",
  "rule_enforcement_fallback_already_recorded",
  "rule_enforcement_fallback_fields_required",
  "rule_enforcement_fallback_idempotency_conflict",
  "rule_enforcement_fallback_kind_unknown",
  "rule_enforcement_fallback_not_recorded",
  "rule_enforcement_fallback_receipts_append_only",
  "rule_enforcement_fallback_requires_joe_authority",
  "rule_enforcement_fallback_rule_not_found",
  "rule_scope_required",
  "rule_search_text_required",
  "salesforce_id_in_use",
  "same_party",
  "same_vendor_row",
  "self_link",
  "source_not_a_locator",
  "source_required",
  "source_unavailable",
  "stage_evidence_mismatch",
  "stage_evidence_not_contact",
  "subject_not_found",
  "success_criterion_required",
  "successor_loop_not_open",
  "suppression_clear_required",
  "suppression_clear_requires_human",
  "team_review_has_no_account",
  "template_exists",
  "template_not_found",
  "template_unreviewed",
  "text_required",
  "too_many_items",
  "too_many_links",
  "triage_cursor_invalid",
  "triage_page_invalid",
  "undo_changed",
  "undo_human_quote_required",
  "unexpected_arguments",
  "unknown_candidate_kind",
  "unknown_category_slug",
  "unknown_channel",
  "unknown_claim_type",
  "unknown_deal_type",
  "unknown_lane",
  "unknown_phase",
  "unknown_stage",
  "unknown_submarket_condition",
  "unknown_vendor_stage",
  "unresolved_items",
  "unsupported_call_change",
  "use_reassign_deal",
  "value_required",
  "vendor_not_live",
  "verticals_not_array",
  "via_is_an_end",
  "window_inverted",
  "wrong_merge_survivor",
]);

function walk(node, visit, ancestors = []) {
  if (!node || typeof node !== "object") return;
  if (node.type) visit(node, ancestors);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(child => walk(child, visit, [...ancestors, node]));
    else if (value && typeof value === "object") walk(value, visit, [...ancestors, node]);
  }
}

// This inventories tools.js only. It passes ToolError to other modules whose codes
// are not inventoried, and an aliased constructor (const E = ToolError) is missed.
function codesIn(source) {
  const tree = parse(source, { sourceType: "module" });
  const codes = new Set();
  const unresolved = [];
  const dynamicFunctions = [];
  let enclosing;
  function value(node) {
    if (node?.type === "StringLiteral") codes.add(node.value);
    else if (node?.type === "ConditionalExpression") { value(node.consequent); value(node.alternate); }
    else if (node?.type === "LogicalExpression") { value(node.left); value(node.right); }
    else { unresolved.push(source.slice(node.start, node.end)); dynamicFunctions.push(enclosing); }
  }
  walk(tree, (node, ancestors) => {
    if (node.type !== "NewExpression" || node.callee.type !== "Identifier" || node.callee.name !== "ToolError") return;
    enclosing = [...ancestors].reverse().find(parent => /^(FunctionDeclaration|FunctionExpression|ArrowFunctionExpression|ObjectMethod)$/.test(parent.type));
    const arg = node.arguments[0];
    const property = arg?.type === "ObjectExpression" && arg.properties.find(p => p.key?.name === "error" || p.key?.value === "error");
    if (property) value(property.value);
    else { unresolved.push(source.slice(arg.start, arg.end)); dynamicFunctions.push(enclosing); }
  });
  const fingerprints = dynamicFunctions.filter(Boolean).map(node => createHash("sha256").update(source.slice(node.start, node.end)).digest("hex")).sort();
  return { codes, unresolved, fingerprints };
}

test("every tools.js ToolError code is classified", t => {
  t.mock.method(console, "error", () => {});
  const { codes, unresolved, fingerprints } = codesIn(readFileSync(new URL("../src/tools.js", import.meta.url), "utf8"));
  // Dynamic producers need review when their enclosing functions change.
  assert.deepEqual(fingerprints, [
    "090b814f8e6237373e71306c7e3f5493252cf7c730241328ad3bc254b3170a8e",
    "195bc5561ebbcfe865e46d0b5b6dcbd7d493d8db604ba7988a11762123fe4211",
    "59ca89a7c09faa9d51104039816f63ec7c2f7a251f953cc016b32a2140338f17",
    "a2b254f60a6c0186b71b352bd2f4d483268dadfc918b415d0c9a5a6154a6653e",
    "a51a7e48501621ccd17585ed3b6fc0364e758a8ae05371b31fdc8acbcc0947e7",
    "a851aca64ebd6fbb6b15f543fafbbf7d34e127184b57960d7bb143b580bf450d",
    "aee5b46a1bbeed53b669b4e5b6f6f17d5b38fafd2821b81789b0041e0fb7b81e",
    "bb651d2072cf404ce48e7f36a4ba8af950302447f88eb556941c62e1e626563e",
    "bcb27572be7ffd1bca3645633c3f2921c9245477006ffa970c165fb9786a30d0",
    "c313a94c9354e3f838359c0e457cadbbebd3d4246bcf5a1b765dae46a142c1d5",
    "c313a94c9354e3f838359c0e457cadbbebd3d4246bcf5a1b765dae46a142c1d5",
    "c313a94c9354e3f838359c0e457cadbbebd3d4246bcf5a1b765dae46a142c1d5",
    "efba707fdfa59669e1a3f44fe8bfb4309e4cd13437c54b20392363eba89b4f8e",
    "f63a0f2a5645ee4947efb2591ff71cead89f796204ec510bc9e682a4e66c6c1f",
  ], "review changed dynamic code producers");
  // Existing adapters forward codes from other modules; their source is outside this inventory.
  // Counts make new forwarding sites fail until reviewed.
  assert.deepEqual(unresolved.sort(), [
    "payload", "e.message", "`unknown_${which}`", "`unknown_${field}`", "`unknown_${field}`",
    "error.code", "error.code", "error.code", "error.code", "personalScope.error", "error.error",
    "error.payload", "scope.error", 'result || { error: "inner_write_failed" }',
  ].sort(), "review dynamic ToolError sources");
  for (const code of ["missing_required", "inner_write_failed", "unknown_deal_type", "unknown_lane", "unknown_phase", "unknown_stage"]) codes.add(code);
  const source = readFileSync(new URL("../src/tools.js", import.meta.url), "utf8");
  const refusalSet = parse(source, { sourceType: "module" }).program.body.find(node =>
    node.type === "VariableDeclaration" && node.declarations.some(d => d.id.name === "RULE_ENFORCEMENT_FALLBACK_REFUSALS"));
  walk(refusalSet, node => { if (node.type === "StringLiteral") codes.add(node.value); });
  const publicInternal = [...INTERNAL_ONLY].filter(code =>
    publicError(new ToolError({ error: code, field: "field" }), { inputSchema: { properties: { field: {} } } }).error === code).sort();
  assert.deepEqual(publicInternal, [], "internal-only codes must not be public");
  const stale = [...INTERNAL_ONLY].filter(code => !codes.has(code)).sort();
  assert.deepEqual(stale, [], "internal-only codes must still be thrown in tools.js");
  const unclassified = [...codes].filter(code => !INTERNAL_ONLY.has(code) &&
    publicError(new ToolError({ error: code, field: "field" }), { inputSchema: { properties: { field: {} } } }).error !== code).sort();
  assert.deepEqual(unclassified, [], "classify each code as public or internal-only");
});

test("shared argument layer errors reach callers", t => {
  t.mock.method(console, "error", () => {});
  const source = readFileSync(new URL("../src/tools.js", import.meta.url), "utf8");
  const tree = parse(source, { sourceType: "module" });
  const names = ["assertRequiredArgs", "coerceArgsToSchema", "versionGuard", "assertDeclaredVocabularies", "pgConstraintError"];
  for (const name of names) {
    let fn;
    walk(tree, node => { if (node.type === "FunctionDeclaration" && node.id?.name === name) fn = node; });
    assert.ok(fn, `missing shared function ${name}`);
    const body = source.slice(fn.start, fn.end);
    const { codes, unresolved } = codesIn(body);
    if (name === "assertRequiredArgs") {
      assert.deepEqual(unresolved, ["payload"]);
      const payload = fn.body.body.find(node => node.type === "VariableDeclaration" && node.declarations.some(d => d.id.name === "payload"));
      assert.ok(payload, "missing required-argument payload");
      const declaration = source.slice(payload.start, payload.end);
      const extracted = codesIn(`new ToolError(${declaration.replace(/^const payload = /, "").replace(/;$/, "")})`);
      for (const code of extracted.codes) codes.add(code);
    } else assert.deepEqual(unresolved, [], `review dynamic codes in ${name}`);
    assert.ok(codes.size, `no ToolError codes found in ${name}`);
    for (const code of codes) {
      const result = publicError(new ToolError({ error: code, field: "base_version", column: "base_version" }),
        { inputSchema: { properties: { base_version: {} } } });
      assert.notEqual(result.error, "unhandled_verb_failure", `${name}: ${code}`);
    }
  }
  let forwarder;
  walk(tree, node => { if (node.type === "FunctionDeclaration" && node.id?.name === "assertRegisteredToolInput") forwarder = node; });
  assert.ok(forwarder, "missing shared function assertRegisteredToolInput");
  assert.deepEqual(codesIn(source.slice(forwarder.start, forwarder.end)).unresolved, ["error.error"]);
  assert.throws(() => assertClosedTopLevel("example", { inputSchema: { properties: {} } }, { unknown: true }), error => {
    assert.equal(error.error, "unregistered_operation_fields");
    assert.notEqual(publicError(new ToolError({ error: error.error, fields: error.fields })).error, "unhandled_verb_failure");
    return true;
  });
});

test("inventory parses multiline constructors and conditional codes", () => {
  const { codes, unresolved } = codesIn(`function helper() {
    throw new ToolError({\n error: condition ? "first_code" : "second_code"\n });
  }`);
  assert.deepEqual([...codes].sort(), ["first_code", "second_code"]);
  assert.deepEqual(unresolved, []);
});

test("changes to a dynamic code producer require review", () => {
  const source = 'function helper() { for (const field of ["stage"]) throw new ToolError({ error: `unknown_${field}` }); }';
  const changed = source.replace('["stage"]', '["stage", "lane"]');
  assert.notDeepEqual(codesIn(source).fingerprints, codesIn(changed).fingerprints);
});


test("find-rule validation and forwarded identity errors retain their private classification", async t => {
  t.mock.method(console, "error", () => {});
  const { TOOLS } = await import("../src/tools.js");
  const partner = { slug: "joe", human: true };
  const noQuery = { query: () => assert.fail("refusals must precede database access") };
  for (const [args, actor, code] of [
    [{ text: " " }, partner, "rule_search_text_required"],
    [{ text: "rule", status: "invalid" }, partner, "invalid_rule_status"],
    [{ text: "rule", limit: 0 }, partner, "invalid_rule_search_limit"],
    [{ text: "rule" }, { slug: "unknown" }, "invalid_runtime_principal"],
    [{ text: "rule" }, { slug: "claude", human: false, via: "oauth-google" }, "missing_or_ambiguous_sponsor"],
  ]) {
    await assert.rejects(TOOLS["find-rule"].handler(noQuery, actor, args), error => {
      assert.equal(error.payload.error, code);
      assert.equal(publicError(error).error, "unhandled_verb_failure");
      return true;
    });
  }
});
