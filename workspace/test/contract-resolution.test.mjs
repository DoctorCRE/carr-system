// Test workspace contract and requirement resolution.
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import { futureGateIds, testRegistry } from "./id-registry.mjs";
import { compileSchema } from "../contracts/schema-validator.mjs";

const workspace = new URL("../", import.meta.url);
const readJson = async relative => JSON.parse(await readFile(new URL(relative, workspace), "utf8"));
const readText = relative => readFile(new URL(relative, workspace), "utf8");
const exactKeys = (value, keys, label) => assert.deepEqual(Object.keys(value).sort(), [...keys].sort(), label);

async function loadFixtures() {
  const directory = new URL("fixtures/", workspace);
  const names = (await readdir(directory)).filter(name => name.endsWith(".v1.json"));
  return new Map(await Promise.all(names.map(async name => {
    const fixture = JSON.parse(await readFile(join(directory.pathname, name), "utf8"));
    return [fixture.surface, fixture];
  })));
}

test("all fixture twins conform to the finalized exact-key contract", async () => {
  const [contract, fixtures] = await Promise.all([readJson("contracts/prototype-fixture-contract.v1.json"), loadFixtures()]);
  const validateFixture = compileSchema(contract);
  assert.deepEqual([...fixtures.keys()].sort(), [...contract.required_surfaces].sort());
  const normalKeys = {
    "call-review": ["freshness", "call"],
    "command-center": ["freshness", "headline", "items", "metrics"],
    "deal-room": ["freshness", "record", "parking_reasons"],
    "doc-request": ["freshness", "context", "answer", "request"],
    "lead-board": ["freshness", "items"],
    "market-map": ["freshness", "market"],
    marketing: ["freshness", "items"],
    more: ["freshness", "destinations", "registry_only"],
    notifications: ["freshness", "items"],
    tour: ["freshness", "tour"]
  };
  for (const [surface, fixture] of fixtures) {
    const validation = validateFixture(fixture);
    assert.equal(validation.valid, true, `${surface}: ${validation.errors.join("; ")}`);
    exactKeys(fixture, ["schema_version", "surface", "requirement_ids", "synthetic", "states"], `${surface} top-level`);
    assert.equal(fixture.schema_version, "workspace-fixture/v1");
    assert.equal(fixture.synthetic, true);
    exactKeys(fixture.states, contract.required_states, `${surface} state set`);
    for (const [state, value] of Object.entries(fixture.states)) {
      exactKeys(value.freshness, ["status", "as_of"], `${surface}:${state} freshness`);
      if (state === "normal") exactKeys(value, normalKeys[surface], `${surface}:normal`);
      else if (surface === "command-center") exactKeys(value, ["freshness", "message", "items"], `${surface}:${state}`);
      else if (surface === "deal-room" && state === "partial") exactKeys(value, ["freshness", "message", "record"], `${surface}:${state}`);
      else if (surface === "deal-room" && state === "conflict") exactKeys(value, ["freshness", "message", "conflict"], `${surface}:${state}`);
      else if (surface === "tour" && state === "offline") exactKeys(value, ["freshness", "message", "tour"], `${surface}:${state}`);
      else exactKeys(value, ["freshness", "message"], `${surface}:${state}`);
    }
  }
});

test("fixture requirement IDs resolve to traceability requirements", async () => {
  const [traceability, fixtures] = await Promise.all([readJson("contracts/phase0-traceability.v1.json"), loadFixtures()]);
  const requirements = new Set(traceability.entries.map(entry => entry.id));
  for (const fixture of fixtures.values()) {
    for (const id of fixture.requirement_ids) assert.ok(requirements.has(id), `${fixture.surface} unresolved requirement ${id}`);
  }
});

test("every test ID resolves to either an executed check or an explicit future gate", async () => {
  const [traceability, threat, events, crossReference, environment] = await Promise.all([
    readJson("contracts/phase0-traceability.v1.json"),
    readJson("contracts/threat-model.v1.json"),
    readJson("contracts/notification-event-taxonomy.v1.json"),
    readJson("contracts/cross-reference-registry.v1.json"),
    readJson("contracts/environment-release-process.v1.json")
  ]);
  const referenced = new Set([
    ...traceability.entries.flatMap(entry => entry.tests),
    ...threat.threats.flatMap(item => item.tests),
    ...events.events.flatMap(event => event.test_ids)
  ]);
  assert.deepEqual([...referenced].sort(), [...crossReference.test_ids].sort(), "cross-reference test namespace drift");
  const classified = new Set([...testRegistry.keys(), ...futureGateIds]);
  assert.deepEqual([...classified].sort(), [...crossReference.test_ids].sort(), "test classification drift");
  const runtimeGates = new Map(environment.runtime_verification_gates.map(item => [item.test_id, item]));
  for (const id of referenced) {
    assert.notEqual(testRegistry.has(id), futureGateIds.has(id), `${id} must have exactly one classification`);
    if (futureGateIds.has(id)) {
      const gate = runtimeGates.get(id);
      assert.equal(gate?.status, "future_gate_not_implemented_in_phase0_static_prototype", `${id} future gate status`);
      assert.ok(gate.required_evidence.length >= 2, `${id} future evidence`);
    }
  }
});

test("every registered contract test executes against the prototype and planning evidence", async () => {
  const [fixtures, html, css, app, client, server, environment, manifest, council, trace, acceptance, governance, threat, events, machines, tenantDenial, sponsorIdentity, api, authority, marketMapContract] = await Promise.all([
    loadFixtures(),
    readText("public/index.html"),
    readText("public/css/app.css"),
    readText("public/js/app.js"),
    readText("public/js/client.js"),
    readText("public/server.mjs"),
    readJson("contracts/environment-release-process.v1.json"),
    readJson("contracts/phase0-manifest.v1.json"),
    readJson("contracts/council-review-register.v1.json"),
    readJson("contracts/phase0-traceability.v1.json"),
    readJson("contracts/phase0-acceptance.v1.json"),
    readJson("contracts/tenant-workflow-governance.v1.json"),
    readJson("contracts/threat-model.v1.json"),
    readJson("contracts/notification-event-taxonomy.v1.json"),
    readJson("contracts/state-machines.v1.json"),
    readJson("test/fixtures/tenant-boundary-denials.v1.json"),
    readJson("test/fixtures/sponsor-runtime-identity.v1.json"),
    readJson("contracts/business-entity-api-contracts.v1.json"),
    readJson("contracts/authority-risk-matrix.v1.json"),
    readJson("contracts/market-map-route-planning.v1.json")
  ]);
  const context = { fixtures, html, css, app, client, server, environment, manifest, council, trace, acceptance, governance, threat, events, machines, tenantDenial, sponsorIdentity, api, authority, marketMapContract };
  for (const [id, check] of testRegistry) assert.doesNotThrow(() => check(context), id);
});

test("sponsor and runtime identity fixture covers every required identity outcome without claiming runtime completion", async () => {
  const [fixture, acceptance, events, api] = await Promise.all([
    readJson("test/fixtures/sponsor-runtime-identity.v1.json"),
    readJson("contracts/phase0-acceptance.v1.json"),
    readJson("contracts/notification-event-taxonomy.v1.json"),
    readJson("contracts/business-entity-api-contracts.v1.json")
  ]);
  assert.deepEqual(fixture.cases.map(item => item.id), ["joe_codex_rules", "joe_claude_rules", "dell_codex_rules", "cross_brain_read", "unattended_agent", "missing_scope", "connector_fallback_parity", "personal_rules_non_escalation"]);
  assert.equal(fixture.reproduced_evidence.original_standing_context_result.shared_rule_count, 143);
  assert.equal(fixture.reproduced_evidence.original_standing_context_result.personal_rule_count, 0);
  assert.equal(fixture.reproduced_evidence.verified_live_result.shared_rule_count, 144);
  assert.equal(fixture.reproduced_evidence.verified_live_result.personal_rule_count, 30);
  assert.equal(fixture.reproduced_evidence.verified_live_result.sponsoring_human_id, "joe");
  assert.equal(fixture.reproduced_evidence.verified_live_result.agent_principal_id, "codex");
  assert.equal(fixture.reproduced_evidence.verified_live_result.human_only, false);
  assert.equal(fixture.reproduced_evidence.joe_personal_artifact.active_rule_count, 30);
  const validatePrincipalExecutionContext = compileSchema(api, api.$defs.PrincipalExecutionContext);
  for (const item of fixture.cases) {
    const validation = validatePrincipalExecutionContext(item.server_context);
    assert.equal(validation.valid, true, `${item.id}: ${validation.errors.join("; ")}`);
  }
  const unattended = fixture.cases.find(item => item.id === "unattended_agent");
  assert.equal(unattended.server_context.personal_brain_scope, "none");
  assert.equal(unattended.server_context.personal_rule_count, 0);
  assert.equal(unattended.server_context.shared_rule_count, 144);
  const dell = fixture.cases.find(item => item.id === "dell_codex_rules");
  assert.equal(dell.server_context.resolution_status, "resolved");
  assert.equal(dell.server_context.personal_rule_count, 0);
  assert.match(acceptance.sponsor_runtime_identity_acceptance.current_gate_status, /^passed_live_and_deterministic_matrix/);
  const required = new Set(events.event_envelope.required);
  for (const field of fixture.audit_expectation.required_safe_fields) assert.ok(required.has(field === "resolution_status" ? "identity_resolution_status" : field), field);
  const forbidden = new Set(events.event_envelope.forbidden);
  for (const field of fixture.audit_expectation.forbidden_fields) assert.ok(forbidden.has(field), field);
});

test("all baseline IDs render with unknown values and an instrumentation or interview action", async () => {
  const [acceptance, fixtures] = await Promise.all([readJson("contracts/phase0-acceptance.v1.json"), loadFixtures()]);
  const cutover = fixtures.get("more").states.normal.destinations.find(item => Array.isArray(item.baselines));
  assert.equal(cutover.text, acceptance.operating_objective_gate.question);
  assert.deepEqual(cutover.baselines.map(item => item.id), acceptance.baseline_measure_plan.map(item => item.id));
  for (const baseline of cutover.baselines) {
    assert.equal(baseline.value, null, baseline.id);
    assert.equal(baseline.status, "Unknown — not yet measured", baseline.id);
    assert.match(baseline.next_action, /interview|diary|journey|time|count/i, baseline.id);
  }
});

test("surface cutover owners resolve without invented evidence", async () => {
  const surfaceMap = await readJson("contracts/surface-registry-migration-map.v1.json");
  const assignments = new Map(surfaceMap.cutover_owner_assignments.map(item => [item.id, item]));
  assert.equal(assignments.size, 4);
  for (const surface of surfaceMap.surfaces.filter(item => item.cutover_owner.startsWith("OPEN-SURFACE-OWNER-"))) {
    const assignment = assignments.get(surface.cutover_owner);
    assert.equal(assignment.surface_id, surface.id, surface.cutover_owner);
    assert.ok(assignment.accountable && assignment.technical_verifier, surface.cutover_owner);
  }
});

test("baseline presentation exposes the bound action and like-for-like comparison", async () => {
  const fixtures = await loadFixtures();
  const baselines = fixtures.get("more").states.normal.destinations.find(item => item.baselines).baselines;
  assert.ok(baselines.every(item => item.bound_product_action && item.post_launch_comparison));
  const app = await readText("public/js/app.js");
  assert.match(app, /Bound product action:/);
  assert.match(app, /Post-launch comparison:/);
});

test("glossary provenance and all six conceptual operating roles are explicit", async () => {
  const glossary = await readJson("contracts/domain-glossary.v1.json");
  assert.equal(glossary.source.source_sha256, "cb7733ae8f85eb1847b2b67ee6801b048494102b7e49857dda5d91b90bec8935");
  assert.equal(glossary.source.doctrine_document_id, "c7f31740-7f4b-47e9-ab93-c7f2854bacc6");
  assert.equal(glossary.source.doctrine_generation, 344);
  assert.equal(glossary.source.doctrine_section_count, 35);
  assert.equal(glossary.source.timing_section_key, "s23");
  assert.equal(glossary.source.timing_section_version, 4);
  const terms = new Set(glossary.terms.map(item => item.term));
  for (const role of ["CARR Workspace", "The Command Center", "CARR Control Room", "Doc", "Claude Code and Codex", "CARR record layer"]) assert.ok(terms.has(role), role);
});

test("integrated planning sources, milestone timing, predecessors, and cost bands stay harmonized", async () => {
  const [manifest, trace, council, acceptance, hermes, hermesFixture, reconciliation, naming, humanSeat, humanSeatFixture, driveFree, crossBoundary, councilLaunch, councilLedger] = await Promise.all([
    readJson("contracts/phase0-manifest.v1.json"),
    readJson("contracts/phase0-traceability.v1.json"),
    readJson("contracts/council-review-register.v1.json"),
    readJson("contracts/phase0-acceptance.v1.json"),
    readJson("../phase0/hermes-runtime-council-candidate.v1.json"),
    readJson("../phase0/hermes-runtime-evaluation-fixtures.v1.json"),
    readJson("../phase0/council-reconciliation-2026-08-12.v1.json"),
    readJson("../phase0/platform-naming-council-candidate.v1.json"),
    readJson("../phase0/human-seat-workspace-isolation-council-candidate.v1.json"),
    readJson("../phase0/human-seat-workspace-isolation-fixtures.v1.json"),
    readJson("../phase0/drive-free-system-council-candidate.v1.json"),
    readJson("../phase0/cross-product-boundary.v1.json"),
    readJson("../phase0/council-launch-packet-2026-08-12.v1.json"),
    readJson("../phase0/council-disposition-ledger-template.v1.json")
  ]);
  const expectedIds = [
    "c7f31740-7f4b-47e9-ab93-c7f2854bacc6",
    "11fdc56f-9af5-47c9-92a7-bb392ca60bd6",
    "15d2250c-4821-4f83-9dc5-063f9470139d",
    "10d25f48-916b-4a7f-a1a6-d231274fed4b"
  ].sort();
  assert.deepEqual(manifest.canonical_planning_sources.map(item => item.document_id).sort(), expectedIds);
  assert.deepEqual(trace.canonical_planning_set.map(item => item.document_id).sort(), expectedIds);
  for (const id of expectedIds) assert.ok(council.review_event.inputs.some(item => item.includes(id)), id);
  assert.equal(manifest.source.doctrine_generation, 344);
  assert.equal(manifest.source.timing_section_version, 4);
  assert.equal(manifest.source.desktop_artifact_matches_current_doctrine_generation, "verified_at_generation_344");
  const program = manifest.integrated_delivery_program;
  assert.equal(program.mature_foundation_v1.target_date, "2026-10-05");
  assert.equal(program.workspace_web_timing.evidence_range, "12–16 weeks");
  assert.match(program.full_multi_platform_timing, /^4–6 months/);
  assert.deepEqual(program.pricing_evidence_bands_usd_per_month, {
    phase0_or_pilot_incremental: {min: 5, max: 20},
    mature_two_partner_web_operations: {min: 62, max: 84},
    before_paid_incident_response: {min: 28, max: 50},
    with_apple_reserve_approximate: {min: 70, max: 93},
    high_intentional_use: {min: 300, max: 500}
  });
  assert.match(acceptance.integrated_program_acceptance.construction_gate, /Joe approves.*council output/);
  assert.equal(hermes.status, "proposed_for_council_not_approved");
  assert.deepEqual(hermes.decision_record_ids, ["94c0206f-910f-4059-b0b3-24d67b05027c", "715d95e0-09e6-4138-a3d2-d1ff4d3b2983", "4cbcc9dd-dcc7-4522-8dd9-6f86022b8056"]);
  assert.equal(hermes.proposed_end_state_role_split.status, "proposed_for_council_not_approved");
  assert.match(hermes.proposed_end_state_role_split.scope_clarification, /CARR business and system operating environment.*not unrestricted macOS.*shell.*filesystem/i);
  assert.match(hermes.proposed_end_state_role_split.hermes, /persistent operations orchestrator and dispatcher.*does not own truth.*repair production code/i);
  assert.match(hermes.proposed_end_state_role_split.claude_code_and_codex, /engineering escalation plane.*new capabilities.*system features.*reviewed repairs/i);
  assert.match(hermes.proposed_end_state_role_split.adoption_test, /without opening or supervising Claude Code or Codex.*typed, evidence-rich engineering work request.*verified result returns/i);
  assert.match(hermes.proposed_end_state_role_split.doc, /Hermes may be the default runtime behind Doc.*visibly discloses.*executor/i);
  assert.deepEqual(hermes.engineering_handoff_graduation.evaluation_r0.expected_refusals, ["carr.engineering_work_request.create_v1", "carr.engineering_dispatch.execute_v1"]);
  assert.deepEqual([hermes.engineering_handoff_graduation.evaluation_r0.durable_request_create, hermes.engineering_handoff_graduation.evaluation_r0.engineering_dispatch], [false, false]);
  assert.equal(hermes.engineering_handoff_graduation.request_create_r1.status, "future_separate_gate");
  assert.equal(hermes.engineering_handoff_graduation.engineering_dispatch_r2.status, "future_separate_gate");
  assert.match(hermes.crux.crux, /persistent presence, not a second memory or authority system/i);
  assert.match(hermes.recommended_evaluation.runtime, /dedicated Nous Portal Cloud.*Small.*Medium/i);
  assert.match(hermes.recommended_evaluation.mcp_surface, /R0 read-only.*mutation.*absent/i);
  assert.match(hermes.joe_risk_weighting.interpretation, /always-on hosted compute.*economical model access.*primary product value/i);
  assert.match(hermes.non_negotiable_architecture.portal, /incremental.*Privacy Mode.*minimum-necessary/i);
  assert.equal(hermes.live_data_gate.status, "requested_for_council_and_joe_approval_not_yet_authorized");
  assert.match(hermes.live_data_gate.phase_a, /Synthetic fixtures only until/i);
  assert.match(hermes.live_data_gate.phase_b_entry, /council recommendation and Joe's approval.*time-bounded live read-only/i);
  assert.ok(hermes.live_data_gate.allowed_minimum_fields.some(item => /human-readable client or deal label/i.test(item)));
  assert.ok(hermes.live_data_gate.excluded_payloads.some(item => /raw email bodies.*call audio.*transcripts/i.test(item)));
  assert.match(hermes.live_data_gate.enforcement, /purpose-built read model.*cannot widen/i);
  assert.match(hermes.cost_hypothesis.pilot_budget_hypothesis, /Start Small.*roughly \$13\.30.*model and tool use/i);
  assert.match(hermes.cost_hypothesis.claim_boundary, /not an observed CARR monthly cost/i);
  assert.match(hermes.cost_hypothesis.credit_mechanics_correction, /not automatically \$29.*\$20.*\$22.*\$8\.70.*\$13\.30/i);
  assert.deepEqual(hermes.cost_hypothesis.official_cloud_sizes.map(item => item.thirty_day_running_usd), [8.70, 16.80, 32.70]);
  assert.deepEqual(hermes.cost_hypothesis.existing_seat_comparison.map(item => item.monthly_price_usd), [20, 100, 200, 20, 100, 200]);
  assert.equal(hermes.roi_hypothesis.status, "illustrative_sensitivity_model_not_observed_carr_baseline");
  assert.ok(Object.values(hermes.roi_hypothesis.current_carr_baselines).every(value => value === null));
  assert.deepEqual(hermes.roi_hypothesis.top_down_arithmetic_check.hours_saved_per_month_from_6_to_10_hours_week_and_30_to_50_percent, {low: 7.79, high: 21.65});
  assert.match(hermes.roi_hypothesis.top_down_arithmetic_check.finding, /17–30.*do not follow.*upside case/i);
  assert.ok(hermes.roi_hypothesis.bound_actions.some(item => /Do not claim payback.*net \$2,900–\$5,200/i.test(item)));
  assert.ok(hermes.acceptance_tests.some(item => /Deleting and rebuilding.*loses no authoritative/i.test(item)));
  assert.ok(hermes.stop_conditions.some(item => /local memory or skill treated as authoritative/i.test(item)));
  assert.ok(hermes.stop_conditions.some(item => /outside the approved versioned minimum-data allow-list/i.test(item)));
  assert.ok(council.open_items.some(item => item.id === "OPEN-HERMES-001" && /Portal Cloud evaluation/i.test(item.recommended_direction)));
  assert.ok(reconciliation.proposed_rulings.some(item => item.id === "PROP-007" && /Portal Cloud-hosted/i.test(item.recommended_ruling)));
  assert.ok(reconciliation.proposed_rulings.some(item => item.id === "PROP-007" && /Hermes orchestrates routine CARR operations.*Claude Code and Codex.*construction.*reviewed repairs/i.test(item.recommended_ruling)));
  assert.equal(naming.status, "proposed_for_council_not_approved");
  assert.equal(naming.decision_record_id, "27a297b3-dafb-428b-ba1e-62a804b518bb");
  assert.match(naming.recommendation, /independent platform name.*CARR as the launch tenant/i);
  assert.equal(naming.options.find(item => item.id === "NAME-OPT-002").recommendation, true);
  assert.deepEqual(naming.proposed_identity_architecture.tenant_members, ["Joe", "Dell"]);
  assert.match(naming.cutover_contract.before_approval, /Do not rename canonical roadmap records.*production domains.*repository/i);
  assert.ok(council.open_items.some(item => item.id === "OPEN-PLATFORM-NAME-001" && /independent platform name/i.test(item.recommended_direction)));
  assert.ok(reconciliation.proposed_rulings.some(item => item.id === "PROP-008" && /CARR as the launch tenant/i.test(item.recommended_ruling)));
  assert.equal(humanSeat.status, "proposed_for_council_not_approved");
  assert.equal(humanSeat.decision_record_id, "8d27333b-6544-4117-b6bd-e9b86d1b1c89");
  assert.match(humanSeat.clarification, /human real estate professional.*not an AI agent/i);
  assert.match(humanSeat.scale_and_rollout.known_company_shape, /more than 150.*solo.*teams/i);
  assert.match(humanSeat.seat_contract.seat_effect, /licensed modules.*no business workspace.*by itself/i);
  assert.match(humanSeat.seat_contract.solo_agent_default, /private business workspace/i);
  assert.match(humanSeat.seat_contract.team_agent_default, /explicitly approved team workspace/i);
  assert.equal(humanSeat.panhandle_isolation.protected_surfaces.length, 10);
  assert.deepEqual(humanSeatFixture.cases.slice(10).map(item => item.id), ["HUMAN-SCOPE-011", "HUMAN-SCOPE-012", "HUMAN-SCOPE-013", "HUMAN-SCOPE-014", "HUMAN-SCOPE-015", "HUMAN-SCOPE-016", "HUMAN-SCOPE-017", "HUMAN-SCOPE-018"]);
  assert.ok(humanSeatFixture.cases.slice(10).every(item => item.expected.effect === "privacy_safe_not_found" && item.expected.reason === "cross_workspace"));
  assert.equal(humanSeatFixture.cases.find(item => item.id === "HUMAN-SCOPE-005").expected.reason, "admin_has_no_business_data_authority");
  assert.equal(humanSeatFixture.cases.find(item => item.id === "HUMAN-SCOPE-008").expected.reason, "runtime_cannot_widen_human_workspace");
  assert.equal(humanSeatFixture.cases.find(item => item.id === "HUMAN-SCOPE-009").expected.reason, "membership_revoked");
  assert.match(crossBoundary.human_seat_workspace_boundary.workspace_rule, /Panhandle Team.*Joe and Dell.*solo agent.*team workspace/i);
  assert.ok(council.open_items.some(item => item.id === "OPEN-HUMAN-WORKSPACE-001" && /Pre-engineer one CARR company tenant.*workspace membership.*operate only Panhandle Team/i.test(item.recommended_direction)));
  assert.ok(reconciliation.proposed_rulings.some(item => item.id === "PROP-009" && /Panhandle Team.*human seat grants authentication.*zero business-data access/i.test(item.recommended_ruling)));
  assert.equal(driveFree.status, "proposed_for_council_not_approved");
  assert.match(driveFree.source_context.joe_human_quotes.join(" "), /Hermes.*real game changer.*need hermes in our system/i);
  assert.match(driveFree.recommendation, /No Google Drive file may be required.*Retire Cowork.*Hermes.*persistent routine operations.*Claude Code or Codex.*engineering/i);
  assert.match(driveFree.joe_proposed_direction.protective_boundary, /Drive independence belongs to the governed platform, not to Hermes.*Hermes or Portal outage/i);
  assert.deepEqual(driveFree.acceptance_tests.map(item => item.id), ["DRIVE-FREE-001", "DRIVE-FREE-002", "DRIVE-FREE-003", "DRIVE-FREE-004", "DRIVE-FREE-005", "DRIVE-FREE-006"]);
  assert.ok(council.open_items.some(item => item.id === "OPEN-DRIVE-FREE-001" && /Drive-free.*Cowork.*Hermes/i.test(`${item.fork} ${item.recommended_direction}`)));
  assert.ok(reconciliation.proposed_rulings.some(item => item.id === "PROP-010" && /No Drive file may be required.*Hermes.*routine-operations.*Claude Code and Codex.*engineering/i.test(item.recommended_ruling)));
  assert.deepEqual(councilLaunch.reviewer_lenses.map(item => item.seat), ["Claude frontier seat", "Codex frontier seat", "Grok frontier seat"]);
  assert.deepEqual(councilLaunch.model_selection_policy.seats.map(item => item.runtime_family), ["Claude", "Codex", "Grok"]);
  assert.match(councilLaunch.model_selection_policy.rule, /Do not hardcode model versions.*latest, most advanced generally available frontier model/i);
  assert.match(councilLaunch.model_selection_policy.reasoning_effort, /exactly.*high.*floor and a ceiling.*below high.*max, ultra.*above high/i);
  assert.match(councilLaunch.model_selection_policy.fail_closed, /exactly high.*stop.*below high.*max\/ultra/i);
  assert.ok(councilLaunch.meeting_protocol[0].procedure.some(item => /exactly.*high.*neither lower nor max\/ultra/i.test(item)));
  assert.equal(councilLaunch.meeting_method_decision_id, "6b4feb08-7b82-4351-9173-98d7b4239b10");
  assert.deepEqual(councilLaunch.meeting_protocol.map(item => item.stage), [0, 1, 2, 3, 4, 5, 6, 7]);
  assert.match(councilLaunch.meeting_protocol[1].name, /sealed independent reviews/i);
  assert.match(councilLaunch.facilitation_rules.join(" "), /not a fourth vote.*majority does not override.*Dissent is preserved/i);
  assert.equal(councilLaunch.explicit_non_goals.some(item => /onboarding CARR's other 150-plus agents/i.test(item)), true);
  assert.equal(councilLaunch.known_open_evidence.some(item => /six uncoached Workspace journeys are not performed/i.test(item)), true);
  assert.deepEqual(councilLaunch.progressive_disclosure_review_order.pass_1_full_dependency_review.map(item => item.source_slug), ["carr-production-maturity-baseline", "carr-workspace-bduf", "carr-control-room-bduf", "carr-mature-software-end-state-bduf"]);
  assert.deepEqual(councilLaunch.progressive_disclosure_review_order.pass_2_cross_cutting_proposals.map(item => item.source), ["phase0/platform-naming-council-candidate.v1.json", "phase0/human-seat-workspace-isolation-council-candidate.v1.json", "phase0/drive-free-system-council-candidate.v1.json", "phase0/hermes-runtime-council-candidate.v1.json", "phase0/workspace-market-map-council-candidate.v1.json"]);
  assert.match(councilLaunch.copy_paste_prompts.independent_reviewer.join(" "), /Follow progressive_disclosure_review_order exactly.*do not score.*Baseline, Workspace, Control Room, and Mature End State/i);
  assert.match(councilLaunch.copy_paste_prompts.single_session_council_chair.join(" "), /latest, most advanced.*exactly.*high.*below high.*above high.*one-session council chair.*freeze your own.*separate fresh CLI contexts.*Do not reveal any review.*all three validate/i);
  assert.ok(councilLedger.council.reviewers.every(item => item.latest_frontier_verified === false && item.high_effort_verified === false && item.above_high_refused === false));
  assert.equal(councilLaunch.single_session_council_orchestration.execution_order.length, 7);
  assert.deepEqual(councilLedger.fork_dispositions.map(item => item.fork_id), councilLaunch.forks_to_disposition.map(item => item.id));
  assert.equal(councilLedger.joe_approval_card.status, "not_presented");
  assert.deepEqual(hermesFixture.capability_manifest.include, hermes.machine_evaluable_contract.exact_proposed_read_tools);
  assert.deepEqual(hermesFixture.capability_manifest.exclude_classes, hermes.machine_evaluable_contract.absent_capability_classes);
  assert.deepEqual([hermesFixture.capability_manifest.scheduler_present, hermesFixture.capability_manifest.channel_delivery_present, hermesFixture.capability_manifest.personal_rule_bodies_returned], [false, false, false]);
  assert.equal(hermesFixture.authoritative_server_context.humanOnly_authority, false);
  assert.deepEqual(Object.fromEntries(hermesFixture.cases.map(item => [item.id, item.expected.effect])), {
    "HERMES-CASE-001": "allow",
    "HERMES-CASE-002": "refuse",
    "HERMES-CASE-003": "refuse",
    "HERMES-CASE-004": "refuse",
    "HERMES-CASE-005": "refuse",
    "HERMES-CASE-006": "refuse",
    "HERMES-CASE-007": "refuse",
    "HERMES-CASE-008": "refuse",
    "HERMES-CASE-009": "refuse",
    "HERMES-CASE-010": "allow_shared_metadata_only",
    "HERMES-CASE-011": "refuse",
    "HERMES-CASE-012": "refuse"
  });
  for (const item of hermesFixture.cases) assert.equal(item.expected.humanOnly, false, item.id);
  assert.equal(hermesFixture.cases.find(item => item.id === "HERMES-CASE-009").request.tool, "cron.create");
  assert.equal(hermesFixture.cases.find(item => item.id === "HERMES-CASE-009").expected.reason, "scheduler_absent");
  assert.equal(hermesFixture.cases.find(item => item.id === "HERMES-CASE-011").expected.reason, "engineering_request_create_absent_in_r0");
  assert.equal(hermesFixture.cases.find(item => item.id === "HERMES-CASE-012").expected.reason, "engineering_dispatch_absent_in_r0");
});

test("tenant, workflow, maintenance, and owner-control amendments are executable Phase 0 contracts without production claims", async () => {
  const [governance, denial, acceptance, machines, api, manifest, authority, routes, retention, trace, council, threat, events] = await Promise.all([
    readJson("contracts/tenant-workflow-governance.v1.json"),
    readJson("test/fixtures/tenant-boundary-denials.v1.json"),
    readJson("contracts/phase0-acceptance.v1.json"),
    readJson("contracts/state-machines.v1.json"),
    readJson("contracts/business-entity-api-contracts.v1.json"),
    readJson("contracts/phase0-manifest.v1.json"),
    readJson("contracts/authority-risk-matrix.v1.json"),
    readJson("contracts/route-ia.v1.json"),
    readJson("contracts/retention-redaction.v1.json"),
    readJson("contracts/phase0-traceability.v1.json"),
    readJson("contracts/council-review-register.v1.json"),
    readJson("contracts/threat-model.v1.json"),
    readJson("contracts/notification-event-taxonomy.v1.json")
  ]);
  const expectedChannels = ["browser", "api", "background_job", "local_edge_sync", "search", "export", "attachment", "doc", "ai"];
  const expectedDenials = ["deny_before_render", "deny_before_object_lookup_response", "deny_before_job_execution", "deny_before_sync_acceptance", "zero_cross_tenant_hits", "deny_before_export_creation", "deny_before_metadata_or_content", "deny_before_context_assembly", "deny_before_prompt_or_tool_context"];
  const expectedTenantClasses = ["records", "events", "search", "files_and_attachments", "calls", "ai_memory_and_retrieval", "queues", "integrations", "audit", "offline_packs"];
  const expectedTrialSafety = ["trial creates no new source of truth", "trial performs no destructive migration", "trial creates no parallel writer", "trial does not introduce or depend on a generic workflow engine"];
  const requiredExclusions = ["generic workflow engine", "plugin marketplace", "customer scripting"];
  assert.equal(governance.canonical_sources.find(item => item.document_id === manifest.source.durable_doctrine_id).generation, 344);
  assert.deepEqual(governance.tenant_context_contract.propagation_channels, expectedChannels);
  assert.equal(governance.tenant_context_contract.immutable_per_request, true);
  assert.match(governance.tenant_context_contract.authoritative_source, /^server derives/);
  assert.deepEqual(governance.tenant_context_contract.scoped_resource_classes, expectedTenantClasses);
  assert.deepEqual(denial.cases.map(item => item.channel), expectedChannels);
  assert.deepEqual(denial.cases.map(item => item.expected), expectedDenials);
  assert.equal(new Set(denial.cases.map(item => item.id)).size, expectedChannels.length);
  assert.notEqual(denial.authoritative_context.tenant_id, denial.other_tenant.tenant_id);
  assert.ok(denial.cases.every(item => item.untrusted_tenant_hint === denial.other_tenant.tenant_id));
  assert.ok(denial.cases.every(item => item.target_tenant_id === denial.other_tenant.tenant_id));
  assert.equal(denial.expected_response.matches_nonexistent_target, true);
  assert.equal(denial.expected_response.returns_business_payload, false);
  assert.equal(denial.expected_response.returns_object_metadata, false);
  assert.equal(denial.expected_response.audit_contains_business_payload, false);
  assert.deepEqual(machines.machines.workflow_definition.states, ["experimental", "pilot", "approved", "standard", "retired"]);
  assert.deepEqual(machines.machines.workflow_definition.terminal, ["retired"]);
  assert.equal(governance.bounded_workflow_trial_acceptance.duration, "at most five business days; shorter trials are allowed");
  assert.ok(governance.bounded_workflow_trial_acceptance.maximum_duration_business_days <= 5);
  assert.deepEqual(governance.bounded_workflow_trial_acceptance.safety_clause, expectedTrialSafety);
  assert.ok(governance.mature_rail_boundary.locked.includes("server-derived tenant isolation"));
  assert.ok(governance.tenant_configuration_contract.forbidden_configuration.includes("arbitrary code"));
  assert.ok(governance.tenant_configuration_contract.forbidden_configuration.includes("SQL"));
  assert.ok(requiredExclusions.every(item => governance.launch_topology.explicit_exclusions.includes(item)));
  assert.ok(requiredExclusions.every(item => governance.tenant_configuration_contract.forbidden_configuration.includes(item)));
  assert.equal(acceptance.maintenance_measure_plan.observed_baseline_hours, null);
  assert.deepEqual(acceptance.maintenance_measure_plan.normal_internal_target_human_hours_per_month, {minimum: 3, maximum: 5});
  assert.equal(governance.maintenance_accounting_contract.escalation_gate, "more than five normal internal maintenance hours in each of two consecutive months creates prioritized toil-reduction work");
  assert.equal(governance.maintenance_accounting_contract.low_toil_mature_claim_gate, "a low-toil or mature claim requires three complete consecutive normal months at three to five human hours, with required control evidence intact and exceptions reported separately");
  assert.equal(governance.maintenance_accounting_contract.required_control_evidence_for_claim.length, 7);
  assert.match(acceptance.maintenance_measure_plan.escalation_gate, /^More than five normal internal maintenance hours in each of two consecutive months creates prioritized toil-reduction work\.$/);
  assert.match(acceptance.maintenance_measure_plan.low_toil_mature_claim_gate, /^A low-toil or mature claim requires three complete consecutive normal months at three to five human hours, with required control evidence intact and exceptions reported separately\.$/);
  assert.equal(acceptance.maintenance_measure_plan.required_control_evidence_for_claim.length, 7);
  assert.equal(api.tenant_context_policy.client_tenant_fields_authoritative, false);
  assert.ok(api.$defs.VersionedRecord.required.includes("tenant_id"));
  assert.equal(api.typed_errors.find(item => item.code === "TENANT_SCOPE_REFUSED").http, 404);
  assert.match(authority.identity_boundary.tenant_rule, /server derives one immutable tenant context/);
  assert.equal(authority.platform_control.owner, "Joe Bookout");
  assert.equal(routes.tenant_configuration_boundary.tenant_selector, "structurally absent");
  assert.ok(routes.tenant_configuration_boundary.forbidden_settings.includes("arbitrary code"));
  assert.ok(retention.hard_rules.some(item => item.includes("tenant-bound")));
  assert.ok(manifest.prohibited_claims.some(item => item.includes("production tenant isolation")));
  const tenantTrace = trace.entries.find(item => item.id === "TENANT-BOUNDARY-001");
  const workflowTrace = trace.entries.find(item => item.id === "WORKFLOW-LIFECYCLE-001");
  const maintenanceTrace = trace.entries.find(item => item.id === "MAINTENANCE-ACCOUNTING-001");
  for (const label of ["records", "events", "search", "files and attachments", "calls", "AI memory and retrieval", "queues", "integrations", "audit", "offline packs"]) assert.match(tenantTrace.requirement, new RegExp(label, "i"));
  for (const label of ["no new source of truth", "destructive migration", "parallel writer", "generic workflow engine"]) assert.match(workflowTrace.requirement, new RegExp(label, "i"));
  assert.match(maintenanceTrace.requirement, /more than five normal hours in each of two consecutive months creates prioritized toil-reduction work/i);
  assert.match(maintenanceTrace.requirement, /three complete consecutive normal months at three to five hours with required control evidence intact and exceptions separate/i);
  const settled = new Map(council.settled_inputs.map(item => [item.id, item.decision]));
  for (const label of ["no new source of truth", "destructive migration", "parallel writer", "generic workflow engine", "plugin marketplaces", "customer scripting"]) assert.match(settled.get("SET-013"), new RegExp(label, "i"));
  assert.match(settled.get("SET-014"), /more than five normal internal maintenance hours in each of two consecutive months creates prioritized toil-reduction work/i);
  assert.match(settled.get("SET-014"), /three complete consecutive normal months at three to five hours with required control evidence intact and exceptions reported separately/i);
  const threats = new Map(threat.threats.map(item => [item.id, item]));
  for (const label of ["no new source of truth", "no destructive migration", "no parallel writer", "plugin marketplace", "customer scripting"]) assert.ok(threats.get("T14").mitigations.some(item => item.includes(label)), label);
  assert.ok(threats.get("T15").mitigations.some(item => item.includes("two consecutive months")));
  assert.ok(threats.get("T15").mitigations.some(item => item.includes("three complete consecutive normal months")));
  assert.ok(threats.get("T15").mitigations.some(item => item.includes("required tenant, authorization, approval, audit, backup/restore, release/rollback, workflow, retention, and exception evidence")));
  const eventMap = new Map(events.events.map(item => [item.name, item.outcomes]));
  assert.deepEqual(eventMap.get("workspace.workflow.promoted"), ["pilot", "approved", "standard", "refused"]);
  assert.deepEqual(eventMap.get("workspace.workflow.retired"), ["retired", "refused"]);
  assert.deepEqual(eventMap.get("workspace.maintenance.toil_triggered"), ["triggered_after_two_consecutive_over_five_normal_months"]);
  assert.deepEqual(eventMap.get("workspace.maintenance.remedy_opened"), ["prioritized_toil_reduction_work_opened"]);
});

test("Local Edge Node hardware receipts and gates remain executable and non-authorizing", async () => {
  const [manifest, trace, acceptance, threat] = await Promise.all([
    readJson("contracts/phase0-manifest.v1.json"),
    readJson("contracts/phase0-traceability.v1.json"),
    readJson("contracts/phase0-acceptance.v1.json"),
    readJson("contracts/threat-model.v1.json")
  ]);
  const gate = manifest.hardware_program_gate;
  assert.deepEqual(gate.workspace_section, {
    section_key: "s33-carr-local-edge-node-and-mac-hardware-requirements",
    section_id: "8f6edd0f-08a3-4012-92fc-e46640323fde"
  });
  assert.deepEqual(gate.control_room_section, {
    section_key: "s38-local-edge-node-mac-hardware-and-operational-controls",
    section_id: "7f263925-5939-44bf-98ca-e4c0054e63c8"
  });
  assert.deepEqual(gate.mature_end_state_section, {
    section_key: "s41-carr-local-edge-node-and-hardware-purchase-decision",
    section_id: "87eca39b-e83c-457e-adaa-75c5f0b086ef"
  });
  assert.equal(gate.purchase_requires_joe, true);
  assert.equal(gate.activation_requires_acceptance_evidence, true);
  assert.deepEqual(
    [gate.timeline_expansion_authorized, gate.adoption_credit_authorized, gate.production_authorization],
    [false, false, false]
  );
  assert.deepEqual(gate.required_audit_events, [
    "edge_node.state_changed",
    "edge_node.permission_denied",
    "edge_node.power_recovery_verified",
    "edge_node.backup_restore_verified"
  ]);
  const programTrace = trace.entries.find(item => item.id === "PROGRAM-SEQUENCE-001");
  for (const source of ["Workspace s33", "Control Room s38", "Mature End State s41"]) assert.ok(programTrace.source_sections.includes(source));
  for (const pattern of [/FileVault/i, /permission/i, /network outage/i, /UPS/i, /backup restore/i, /replacement-Mac clean rebuild/i, /Unknown/i, /human gate/i]) {
    assert.match(gate.required_acceptance_evidence.join(" "), pattern);
  }
  const nodeThreat = threat.threats.find(item => item.id === "T17");
  assert.match(nodeThreat.abuse, /shadow source of truth.*Healthy.*timeline expansion.*adoption credit.*production authorization/i);
  assert.match(nodeThreat.mitigations.join(" "), /FileVault.*UPS.*Unknown.*Joe alone approves purchase.*no timeline expansion, adoption credit, or production authorization/i);
  assert.ok(acceptance.phase0_exit.manual_checks.some(item => /Local Edge Node purchase\/activation remains blocked/i.test(item)));
  assert.match(manifest.integrated_delivery_program.construction_gate, /No production construction.*Joe approves.*council output/i);
});

test("domain entities are complete typed objects rather than generic record aliases", async () => {
  const api = await readJson("contracts/business-entity-api-contracts.v1.json");
  for (const group of api.domain_groups) for (const entity of group.entities) {
    const schema = api.$defs[entity];
    assert.equal(schema.type, "object", entity);
    assert.equal(schema.additionalProperties, false, entity);
    assert.ok(schema.required.length >= 3, entity);
    assert.ok(schema.properties.record || ["CallSummary", "CallCandidate", "EngineeringRequest", "Tour", "Notification", "TenantContext", "PrincipalExecutionContext"].includes(entity), entity);
  }
  assert.ok(api.$defs.Tour.properties.device_sync.items.properties.state.enum.includes("superseded"));
});

test("JSON Schema not is enforced and unsupported validation keywords fail compilation", async () => {
  const api = await readJson("contracts/business-entity-api-contracts.v1.json");
  const validateTopic = compileSchema(api, api.$defs.Notification.properties.topic);
  assert.equal(validateTopic("tour_review_ready").valid, true);
  const forbidden = validateTopic("named_lead_outreach");
  assert.equal(forbidden.valid, false);
  assert.match(forbidden.errors.join(" "), /forbidden not schema/);
  assert.throws(() => compileSchema({ type: "string", minLenght: 1 }), /Unsupported JSON Schema keyword: minLenght/);
});

test("Tour prototype stages are canonical and pack phases cannot masquerade as lifecycle states", async () => {
  const [fixtureSchema, api, machines, fixtures] = await Promise.all([
    readJson("contracts/prototype-fixture-contract.v1.json"),
    readJson("contracts/business-entity-api-contracts.v1.json"),
    readJson("contracts/state-machines.v1.json"),
    loadFixtures()
  ]);
  const prototypeStages = fixtureSchema.$defs.TourPrototype.properties.stage.enum;
  const canonicalStages = api.$defs.Tour.properties.state.enum;
  assert.ok(prototypeStages.every(stage => canonicalStages.includes(stage)));
  assert.ok(prototypeStages.every(stage => machines.machines.tour.states.includes(stage)));
  assert.deepEqual(fixtureSchema.$defs.TourPrototypePack.properties.status.enum, api.$defs.Tour.properties.offline_pack.properties.state.enum);
  const validateFixture = compileSchema(fixtureSchema);
  for (const drift of ["route_confirmed", "touring", "offline", "review", "pack_downloading", "ready"]) {
    const candidate = structuredClone(fixtures.get("tour"));
    candidate.states.normal.tour.stage = drift;
    assert.equal(validateFixture(candidate).valid, false, drift);
  }
});
