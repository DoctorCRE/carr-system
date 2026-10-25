import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { compileSchema } from "./schema-validator.mjs";

const contractDir = new URL("./", import.meta.url);
const workspaceDir = new URL("../", contractDir);
const fixtureDir = new URL("fixtures/", workspaceDir);
const readJson = async (base, name) => JSON.parse(await readFile(new URL(name, base), "utf8"));
const contracts = {};
for (const name of (await readdir(contractDir)).filter(name => name.endsWith(".json"))) contracts[name] = await readJson(contractDir, name);
const fixtureNames = (await readdir(fixtureDir)).filter(name => name.endsWith(".json")).sort();
const fixtures = await Promise.all(fixtureNames.map(name => readJson(fixtureDir, name)));
const registry = contracts["cross-reference-registry.v1.json"];
const trace = contracts["phase0-traceability.v1.json"];
const threats = contracts["threat-model.v1.json"];
const events = contracts["notification-event-taxonomy.v1.json"];
const acceptance = contracts["phase0-acceptance.v1.json"];
const environment = contracts["environment-release-process.v1.json"];
const council = contracts["council-review-register.v1.json"];
const fixtureContract = contracts["prototype-fixture-contract.v1.json"];
const api = contracts["business-entity-api-contracts.v1.json"];
const stateContract = contracts["state-machines.v1.json"];
const surfaceMap = contracts["surface-registry-migration-map.v1.json"];
const manifest = contracts["phase0-manifest.v1.json"];
const tenantGovernance = contracts["tenant-workflow-governance.v1.json"];
const marketMap = contracts["market-map-route-planning.v1.json"];
const tenantDenialFixture = await readJson(new URL("test/fixtures/", workspaceDir), "tenant-boundary-denials.v1.json");
const sponsorIdentityFixture = await readJson(new URL("test/fixtures/", workspaceDir), "sponsor-runtime-identity.v1.json");
const hermesCandidate = await readJson(new URL("../../phase0/", contractDir), "hermes-runtime-council-candidate.v1.json");
const hermesFixture = await readJson(new URL("../../phase0/", contractDir), "hermes-runtime-evaluation-fixtures.v1.json");
const namingCandidate = await readJson(new URL("../../phase0/", contractDir), "platform-naming-council-candidate.v1.json");
const humanSeatCandidate = await readJson(new URL("../../phase0/", contractDir), "human-seat-workspace-isolation-council-candidate.v1.json");
const humanSeatFixture = await readJson(new URL("../../phase0/", contractDir), "human-seat-workspace-isolation-fixtures.v1.json");
const driveFreeCandidate = await readJson(new URL("../../phase0/", contractDir), "drive-free-system-council-candidate.v1.json");
const councilLaunch = await readJson(new URL("../../phase0/", contractDir), "council-launch-packet-2026-08-12.v1.json");
const councilLedger = await readJson(new URL("../../phase0/", contractDir), "council-disposition-ledger-template.v1.json");
const exact = (actual, expected, label) => assert.deepEqual([...actual].sort(), [...expected].sort(), label);
assert.equal(marketMap.version, "1.2.0", "AI-assisted map architecture contract version");
assert.equal(marketMap.status, "approved_architecture_not_implemented_in_production", "approved architecture keeps the production boundary explicit");
exact(marketMap.source.research_inputs.map(item => item.id), ["X-MAP-ONE-PROMPT", "X-WELLS-AGENT-WORKFLOW", "X-MAPGL-DETERMINISM", "MAPGL-PRIMARY", "CLEARFORK-PRIMARY", "PARKING-REFORM-PRIMARY"], "map architecture research inputs");
assert(marketMap.source.research_inputs.every(item => item.locator && item.evidence_class && item.incorporated_methods?.length), "map research inputs preserve locator, evidence class, and incorporated methods");
const mapBuild = marketMap.ai_assisted_map_build_contract;
assert.match(mapBuild.ai_role, /bounded.*research.*authoring/i, "AI map role is bounded");
assert.match(mapBuild.forbidden_authority.join(" "), /source of truth.*final geocoder.*route authority.*publish/i, "AI map authority exclusions");
assert.deepEqual(mapBuild.architecture_layers.map(item => item.id), ["canonical_record", "reviewed_enrichment", "deterministic_route", "interactive_presentation", "native_navigation"]);
assert.ok(mapBuild.structured_brief.required_fields.includes("verified_stop_dataset"), "structured brief requires verified stops");
assert.ok(mapBuild.promotion_receipt.required_checks.includes("map_list_route_offline_order_parity"), "promotion receipt requires order parity");
assert.ok(mapBuild.confirmation_gates.includes("publish_client_or_public_map"), "map publication is confirmation-gated");
exact(mapBuild.source_intake.recursive_source_types, ["post", "quoted_post", "thread", "linked_article", "linked_repository", "linked_documentation", "video_or_transcript", "dataset_metadata"], "map source graph traversal");
exact(mapBuild.source_intake.evidence_classes, ["direct_source", "linked_artifact", "public_mirror", "inference"], "map source evidence classes");
for (const field of ["source_id", "parent_source_id", "locator", "source_type", "evidence_class", "retrieved_at", "retrieval_status", "content_hash", "license_or_terms", "claims_supported", "review_state"]) assert(mapBuild.source_intake.required_fields.includes(field), `map source intake ${field}`);
assert.match(mapBuild.source_intake.inaccessible_content_rule, /never.*marked.*read/i, "inaccessible map sources fail visibly");
exact(mapBuild.spatial_authoring_workbench.allowed_operations, ["inspect", "import", "normalize", "join", "filter", "style", "compare", "validate", "export", "prototype"], "bounded spatial authoring operations");
exact(mapBuild.spatial_authoring_workbench.typed_output_formats, ["GeoJSON", "GeoPackage", "GeoParquet", "PMTiles", "promotion_receipt"], "typed map workbench outputs");
for (const field of ["property_id", "canonical_address_version", "approved_position", "position_role", "coordinate_source", "coordinate_precision", "coordinate_verified_by", "coordinate_verified_at", "route_eligible", "review_state"]) assert(mapBuild.canonical_stop_dataset.required_fields.includes(field), `canonical stop dataset ${field}`);
exact(mapBuild.entrance_verification.position_roles, ["primary_entrance", "driveway_entry", "parking_access", "parcel_centroid", "building_centroid"], "navigation position roles");
assert.match(mapBuild.entrance_verification.route_rule, /entrance-level.*approved.*downgrade.*exclude/i, "parking-lot and centroid pins cannot silently route");
exact(mapBuild.entrance_verification.remediation_workflow, ["resolve_authoritative_property_location", "inspect_access_imagery_or_site_evidence", "select_position_role", "human_approve_coordinate", "record_verification_evidence", "rerun_route"], "unverified pin remediation is explicit");
for (const field of ["route_stop_id", "property_id", "route_sequence", "route_label", "locked_state", "dwell_minutes", "buffer_minutes"]) assert(mapBuild.route_stop_identity.required_fields.includes(field), `route stop identity ${field}`);
assert.match(mapBuild.route_stop_identity.identity_invariant, /route_sequence.*route_label.*never.*property identity/i, "route letters are not property identity");
for (const field of ["old_route_version", "new_route_version", "property_id", "old_route_stop_id", "new_route_stop_id", "old_route_sequence", "new_route_sequence", "old_route_label", "new_route_label", "disposition"] ) assert(mapBuild.route_stop_identity.version_mapping_required_fields.includes(field), `route version mapping ${field}`);
exact(mapBuild.interchange_and_data_serving.formats.map(item => item.format), ["GeoJSON", "GeoPackage", "GeoParquet", "PMTiles"], "portable geospatial formats");
exact(mapBuild.product_modes.map(item => item.id), ["search", "tour"], "search and tour modes are explicit");
exact(mapBuild.map_event_contract.typed_events, ["feature_click", "bounds_change", "draw_result", "filter_state", "slider_state", "selected_record", "route_stop_change"], "typed map events");
assert.match(mapBuild.spatial_analysis_boundary.client_side, /Turf.*authorized.*loaded/i, "Turf is bounded to authorized loaded features");
assert.match(mapBuild.spatial_analysis_boundary.server_side, /legal.*large.*projection-sensitive/i, "authoritative GIS stays server-side");
exact(mapBuild.reviewed_enrichment_contract.layer_classes.map(item => item.id), ["competitor", "parcel", "zoning", "demographic", "flood", "imagery", "policy", "traffic"], "reviewed enrichment classes");
exact(mapBuild.component_contract.registry.map(item => item.id), ["MapShell", "MapListSync", "StopCard", "SourceBadge", "LayerControl", "RouteSummary", "ExclusionPanel", "NativeNavAction", "OfflineItinerary", "StoryTourController"], "tested map component registry");
for (const field of ["provider", "sku", "terms_url", "reviewed_at", "reviewer", "storage_class", "storable_fields", "retention_limit", "purge_rule", "attribution", "map_compatibility", "export_exit_path", "quota_cost_gate"]) assert(mapBuild.provider_rights_receipt.required_fields.includes(field), `provider rights receipt ${field}`);
for (const field of ["reviewer_human_id", "reviewed_at", "decision", "brief_version", "canonical_dataset_version", "selected_prototype_id", "component_registry_version", "route_version", "provider_rights_receipt_ids", "mobile_test_evidence", "native_navigation_test_evidence", "offline_test_evidence"]) assert(mapBuild.promotion_receipt.required_fields.includes(field), `promotion receipt ${field}`);
for (const field of ["platform", "approved_latitude", "approved_longitude", "route_stop_id", "route_version", "travel_mode", "return_uri", "generated_at", "expires_at", "device_test_receipt"]) assert(mapBuild.native_navigation_link_contract.required_fields.includes(field), `native navigation link ${field}`);
assert.match(mapBuild.outage_rule, /map-architecture.*unavailable.*block.*map delivery.*repair/i, "map method outage is fail-closed with a repair path");
for (const field of ["start_position", "end_position", "departure_time", "traffic_policy", "avoidances", "locked_appointments", "dwell_minutes", "buffer_minutes", "workday_and_daylight_bounds", "manual_priorities"]) assert(marketMap.day_trip_planning_contract.route_constraint_required_fields.includes(field), `route constraint ${field}`);
for (const field of ["provider", "sku", "request_fingerprint", "observed_at", "ordered_route_stop_ids", "geometry", "legs", "duration_evidence", "distance_evidence", "traffic_evidence", "excluded_stops", "warnings", "terms_receipt_id", "cost_event_count"]) assert(marketMap.day_trip_planning_contract.route_response_required_fields.includes(field), `route response ${field}`);
const validateFixture = compileSchema(fixtureContract);
assert.equal(hermesCandidate.status, "proposed_for_council_not_approved", "Hermes candidate remains advisory");
assert.match(hermesCandidate.recommended_evaluation.runtime, /Nous Portal Cloud.*Small.*Medium/i, "Portal Cloud is the measured evaluation host");
assert.match(hermesCandidate.non_negotiable_architecture.source_truth, /CARR record layer remain authoritative/i, "Hermes cannot become source truth");
assert.match(hermesCandidate.non_negotiable_architecture.authorization, /not authorization boundaries.*default-deny/i, "Hermes filters cannot authorize");
assert.match(hermesCandidate.non_negotiable_architecture.portal, /incremental.*Privacy Mode.*minimum-necessary/i, "Joe's Portal risk weighting is preserved");
assert.equal(hermesCandidate.council_decisions_requested.length, 5, "five Hermes council forks");
assert.match(hermesCandidate.proposed_end_state_role_split.hermes, /operations orchestrator and dispatcher.*typed capabilities.*does not own truth/i, "Hermes mature role is bounded orchestration");
assert.match(hermesCandidate.proposed_end_state_role_split.claude_code_and_codex, /engineering escalation plane.*new capabilities.*reviewed repairs/i, "Claude Code and Codex remain engineering");
assert.equal(hermesCandidate.engineering_handoff_graduation.evaluation_r0.durable_request_create, false, "R0 cannot create engineering request");
assert.equal(hermesCandidate.engineering_handoff_graduation.evaluation_r0.engineering_dispatch, false, "R0 cannot dispatch engineering work");
assert.equal(hermesCandidate.engineering_handoff_graduation.request_create_r1.status, "future_separate_gate", "request creation is future gated");
assert.equal(hermesCandidate.engineering_handoff_graduation.engineering_dispatch_r2.status, "future_separate_gate", "engineering dispatch is future gated");
assert.equal(namingCandidate.status, "proposed_for_council_not_approved", "platform naming remains advisory");
assert.equal(namingCandidate.options.filter(item => item.recommendation === true).length, 1, "one naming architecture recommendation");
assert.match(namingCandidate.recommendation, /independent platform name.*CARR as the launch tenant/i, "independent platform naming recommendation");
assert.equal(namingCandidate.selection_gate.do_not_invent_or_select_in_this_packet, true, "no unevidenced platform name");
assert.equal(humanSeatCandidate.status, "proposed_for_council_not_approved", "human-seat architecture remains advisory");
assert.equal(humanSeatCandidate.decision_record_id, "8d27333b-6544-4117-b6bd-e9b86d1b1c89", "human-seat ruling is durably linked");
assert.match(humanSeatCandidate.clarification, /human real estate professional.*not an AI agent/i, "agent vocabulary is unambiguous");
assert.match(humanSeatCandidate.recommendation, /more than 150.*human users.*Panhandle Team.*solo real estate agent.*team workspace.*zero business-workspace access/i, "CARR company-scale workspace architecture");
assert.match(humanSeatCandidate.seat_contract.seat_effect, /verified human.*licensed modules.*no business workspace.*by itself/i, "seat is not business-data authority");
assert.equal(humanSeatCandidate.panhandle_isolation.protected_surfaces.length, 10, "ten protected workspace surfaces");
assert.equal(humanSeatFixture.synthetic, true, "human workspace isolation fixture is synthetic");
assert.equal(new Set(humanSeatFixture.cases.map(item => item.id)).size, 18, "eighteen unique human workspace isolation cases");
assert.deepEqual(humanSeatFixture.cases.map(item => item.expected.effect), ["privacy_safe_not_found", "allow_role_fields", "privacy_safe_not_found", "privacy_safe_not_found", "privacy_safe_not_found", "refuse", "allow_shared_fields_only", "privacy_safe_not_found", "refuse", "refuse", "privacy_safe_not_found", "privacy_safe_not_found", "privacy_safe_not_found", "privacy_safe_not_found", "privacy_safe_not_found", "privacy_safe_not_found", "privacy_safe_not_found", "privacy_safe_not_found"], "exact human workspace isolation outcomes");
assert.equal(driveFreeCandidate.status, "proposed_for_council_not_approved", "Drive-free candidate remains advisory");
assert.match(driveFreeCandidate.source_context.joe_human_quotes.join(" "), /Hermes.*real game changer.*need hermes in our system/i, "Joe's Hermes position is preserved verbatim");
assert.match(driveFreeCandidate.recommendation, /No Google Drive file may be required.*Retire Cowork.*Hermes.*persistent routine operations.*Claude Code or Codex.*engineering/i, "Drive-free runtime split is explicit");
assert.match(driveFreeCandidate.joe_proposed_direction.protective_boundary, /Drive independence belongs to the governed platform, not to Hermes.*Hermes or Portal outage/i, "Hermes is not the recovery plane");
assert.deepEqual(driveFreeCandidate.acceptance_tests.map(item => item.id), ["DRIVE-FREE-001", "DRIVE-FREE-002", "DRIVE-FREE-003", "DRIVE-FREE-004", "DRIVE-FREE-005", "DRIVE-FREE-006"], "six Drive-free acceptance tests");
assert.deepEqual(driveFreeCandidate.authoritative_placement.google_drive, ["optional user-selected document exchange or convenience copy only"], "Drive is optional document exchange only");
assert.match(driveFreeCandidate.timing_boundary, /does not expand the October Mature Foundation commitment.*claim that complete Drive retirement occurs on August 21/i, "Drive retirement does not silently widen delivery scope");
assert.equal(councilLaunch.frozen_source_snapshot.content_commit, "d68226178866528c0f403891dedf581b4bcedb17", "council source snapshot is exact");
assert.equal(councilLedger.source_snapshot.content_commit, councilLaunch.frozen_source_snapshot.content_commit, "council ledger and launch packet share the frozen content snapshot");
assert.equal(councilLaunch.meeting_method_decision_id, "6b4feb08-7b82-4351-9173-98d7b4239b10", "council format is durably decided");
assert.deepEqual(councilLaunch.reviewer_lenses.map(item => item.seat), ["Claude frontier seat", "Codex frontier seat", "Grok frontier seat"], "three distinct runtime-family council seats");
assert.deepEqual(councilLaunch.model_selection_policy.seats.map(item => item.runtime_family), ["Claude", "Codex", "Grok"], "model selection is runtime-family based, not version pinned");
assert.match(councilLaunch.model_selection_policy.rule, /Do not hardcode model versions.*latest, most advanced generally available frontier model/i, "preflight resolves the latest qualified frontier model");
assert.match(councilLaunch.model_selection_policy.reasoning_effort, /exactly.*high.*floor and a ceiling.*below high.*max, ultra.*above high/i, "reasoning effort is fixed at exactly high");
assert.match(councilLaunch.model_selection_policy.fail_closed, /exactly high.*stop.*below high.*max\/ultra/i, "ambiguous or out-of-range effort fails closed");
assert.equal(councilLaunch.meeting_protocol.length, 8, "eight-stage meeting protocol");
assert.deepEqual(councilLaunch.forks_to_disposition.map(item => item.id), ["PROP-001", "PROP-002", "PROP-003", "PROP-004", "PROP-005", "PROP-006", "PROP-007", "PROP-008", "PROP-009", "PROP-010", "PROP-011"], "eleven ordered council forks");
assert.equal(councilLaunch.uniform_review_rubric.dimensions.filter(item => item.critical).length, 4, "critical objections cannot be averaged away");
assert.match(councilLaunch.authority.construction_gate, /Joe must approve.*durably/i, "council remains advisory");
assert.match(councilLaunch.copy_paste_prompts.independent_reviewer.join(" "), /Do not ask for or read another member's review.*uniform_independent_review_form/i, "independent prompt is sealed and uniform");
assert.deepEqual(councilLaunch.progressive_disclosure_review_order.pass_1_full_dependency_review.map(item => item.source_slug), ["carr-production-maturity-baseline", "carr-workspace-bduf", "carr-control-room-bduf", "carr-mature-software-end-state-bduf"], "full roadmap review follows dependency order");
assert.deepEqual(councilLaunch.progressive_disclosure_review_order.pass_2_cross_cutting_proposals.map(item => item.source), ["phase0/platform-naming-council-candidate.v1.json", "phase0/human-seat-workspace-isolation-council-candidate.v1.json", "phase0/drive-free-system-council-candidate.v1.json", "phase0/hermes-runtime-council-candidate.v1.json", "phase0/workspace-market-map-council-candidate.v1.json"], "cross-cutting proposals include market-map amendment after system boundaries");
assert.match(councilLaunch.progressive_disclosure_review_order.anti_anchoring_rules.join(" "), /No score.*pass 0.*No reviewer may skip.*Hermes and Portal are evaluated after naming, workspace isolation, and Drive independence/i, "review order prevents anchoring and runtime-defined authority");
assert.match(councilLaunch.single_session_council_orchestration.controlling_mode, /Claude frontier model at high effort.*Codex and Grok frontier models at high effort.*fresh isolated CLI/i, "one-session council uses frontier models at high effort in isolated CLI contexts");
assert.match(councilLaunch.single_session_council_orchestration.execution_order.map(item => item.action).join(" "), /complete the Claude frontier-seat.*before.*reading either external review.*Freeze the Claude frontier-seat review.*Codex and Grok frontier models at high effort.*Cross-disclose/i, "Claude review is sealed before joint deliberation");
assert.match(councilLaunch.single_session_council_orchestration.stop_condition, /exact high effort.*neither downward fallback nor max\/ultra escalation.*fresh-context isolation.*stop and restart/i, "one-session orchestration fails closed on model or effort drift");
assert.deepEqual(councilLedger.allowed_dispositions, ["accept", "modify", "defer_with_named_reopen_trigger", "reject"], "closed disposition vocabulary");
assert.equal(councilLedger.fork_dispositions.length, 11, "ledger covers every fork");
assert.ok(councilLedger.fork_dispositions.every(item => item.disposition === null && item.dissent.length === 0), "ledger is an honest blank template");
assert.ok(hermesCandidate.stop_conditions.some(item => /cross-brain.*cross-tenant.*capability/i.test(item)), "identity stop condition");
assert.equal(hermesFixture.synthetic, true, "Hermes fixture is synthetic");
assert.equal(hermesFixture.status, "phase0_candidate_not_deployed", "Hermes fixture is not deployment evidence");
exact(hermesFixture.capability_manifest.include, hermesCandidate.machine_evaluable_contract.exact_proposed_read_tools, "Hermes exact read tools");
exact(hermesFixture.capability_manifest.exclude_classes, hermesCandidate.machine_evaluable_contract.absent_capability_classes, "Hermes excluded capability classes");
assert.equal(hermesFixture.capability_manifest.scheduler_present, false, "Hermes scheduler absent");
assert.equal(hermesFixture.capability_manifest.channel_delivery_present, false, "Hermes channel delivery absent");
assert.equal(hermesFixture.capability_manifest.personal_rule_bodies_returned, false, "Hermes personal rule bodies absent");
assert.equal(hermesFixture.authoritative_server_context.humanOnly_authority, false, "Hermes humanOnly absent");
assert.equal(new Set(hermesFixture.cases.map(item => item.id)).size, 12, "twelve unique Hermes policy cases");
assert.deepEqual(hermesFixture.cases.map(item => item.expected.effect), ["allow", "refuse", "refuse", "refuse", "refuse", "refuse", "refuse", "refuse", "refuse", "allow_shared_metadata_only", "refuse", "refuse"], "Hermes exact policy outcomes");
assert.equal(api.api_policy.prototype_fixture_schema, fixtureContract.$id, "prototype fixture/API schema linkage");
exact(api.api_policy.prototype_methods, ["GET", "HEAD", "OPTIONS"], "prototype method contract");
for (const schema of Object.values(api.$defs)) compileSchema(api, schema);
for (const [index, fixture] of fixtures.entries()) {
  const result = validateFixture(fixture);
  assert.equal(result.valid, true, `${fixtureNames[index]} schema errors:\n${result.errors.join("\n")}`);
}

const requirementIds = new Set(trace.entries.map(entry => entry.id));
const aliases = new Map(registry.requirement_aliases.map(alias => [alias.id, alias.canonical]));
for (const [id, canonical] of aliases) {
  assert(requirementIds.has(canonical), `requirement alias ${id} -> ${canonical}`);
  assert(!aliases.has(canonical), `requirement alias may not chain: ${id}`);
}
for (const fixture of fixtures) for (const id of fixture.requirement_ids) assert(requirementIds.has(id) || aliases.has(id), `fixture requirement ${id}`);
for (const flow of acceptance.primary_uncoached_journeys) for (const id of flow.requirements) assert(requirementIds.has(id) || aliases.has(id), `flow requirement ${id}`);

const testIds = new Set(registry.test_ids);
const futureGateIds = new Set(environment.runtime_verification_gates.map(item => item.test_id));
assert.equal(futureGateIds.size, environment.runtime_verification_gates.length, "unique future gate IDs");
for (const gate of environment.runtime_verification_gates) {
  assert(testIds.has(gate.test_id), `future gate ${gate.test_id} must be in the test catalog`);
  assert.equal(gate.status, "future_gate_not_implemented_in_phase0_static_prototype", `${gate.test_id} future status`);
  assert(gate.required_evidence.length >= 2, `${gate.test_id} future evidence`);
}
for (const entry of trace.entries) for (const id of entry.tests) assert(testIds.has(id), `trace test ${id}`);
for (const threat of threats.threats) for (const id of threat.tests) assert(testIds.has(id), `threat test ${id}`);
for (const event of events.events) for (const id of event.test_ids) assert(testIds.has(id), `event test ${id}`);
const eventIds = new Set(events.events.map(event => event.name));
for (const entry of trace.entries) for (const id of entry.event) assert(eventIds.has(id), `trace event ${id}`);
const councilIds = new Set([council.review_event.id, ...council.open_items.map(item => item.id)]);
for (const entry of trace.entries) for (const id of entry.open_decisions) assert(councilIds.has(id), `council ID ${id}`);
const settledIds = new Set(council.settled_inputs.map(item => item.id));
const councilLinkIds = new Set([...councilIds, ...settledIds]);
for (const adr of council.adr_candidates) for (const id of adr.linked) assert(councilLinkIds.has(id), `ADR ${adr.id} link ${id}`);
assert.equal(new Set(council.current_evidence_inputs.map(item => item.id)).size, council.current_evidence_inputs.length, "unique council evidence IDs");
const expectedPlanningSourceIds = new Set([
  "c7f31740-7f4b-47e9-ab93-c7f2854bacc6",
  "11fdc56f-9af5-47c9-92a7-bb392ca60bd6",
  "15d2250c-4821-4f83-9dc5-063f9470139d",
  "10d25f48-916b-4a7f-a1a6-d231274fed4b"
]);
exact(manifest.canonical_planning_sources.map(item => item.document_id), expectedPlanningSourceIds, "manifest canonical planning sources");
exact(trace.canonical_planning_set.map(item => item.document_id), expectedPlanningSourceIds, "trace canonical planning sources");
for (const id of expectedPlanningSourceIds) assert(council.review_event.inputs.some(item => item.includes(id)), `council input ${id}`);
assert.equal(manifest.source.doctrine_generation, 344, "Workspace doctrine generation");
assert.equal(manifest.source.doctrine_sections, 35, "Workspace doctrine section count");
assert.equal(manifest.source.timing_section_key, "s23", "Workspace timing section key");
assert.equal(manifest.source.timing_section_version, 4, "Workspace timing section version");
const matureSource = manifest.canonical_planning_sources.find(item => item.role === "integrated_mature_end_state_roadmap");
const controlRoomSource = manifest.canonical_planning_sources.find(item => item.role === "operations_and_safety_roadmap");
const productionBaselineSource = manifest.canonical_planning_sources.find(item => item.role === "governing_production_baseline");
assert.equal(controlRoomSource.verified_generation, 345, "Control Room roadmap generation");
assert.equal(controlRoomSource.active_unique_sections, 40, "Control Room roadmap section count");
assert.equal(matureSource.verified_generation, 346, "mature roadmap generation");
assert.equal(matureSource.active_unique_sections, 42, "mature roadmap section count");
exact(matureSource.dependency_section.verified_edges, ["carr-workspace-bduf", "carr-control-room-bduf", "carr-production-maturity-baseline"], "mature roadmap dependency edges");
assert.equal(productionBaselineSource.verified_generation, 336, "production baseline generation");
assert.equal(productionBaselineSource.active_unique_sections, 28, "production baseline section count");
const program = manifest.integrated_delivery_program;
assert.equal(program.decision_id, "69512a40-99ec-483f-8528-5e05a4969551", "integrated delivery decision");
assert.equal(program.mature_foundation_v1.target_date, "2026-10-05", "foundation target");
assert.equal(program.workspace_web_timing.planning_estimate, "approximately 12 weeks", "Workspace web timing");
assert.equal(program.workspace_web_timing.evidence_range, "12–16 weeks", "Workspace web evidence range");
assert.equal(program.full_multi_platform_timing, "4–6 months as an evidence-gated program", "mature program timing");
assert.deepEqual(program.pricing_evidence_bands_usd_per_month, {
  phase0_or_pilot_incremental: {min: 5, max: 20},
  mature_two_partner_web_operations: {min: 62, max: 84},
  before_paid_incident_response: {min: 28, max: 50},
  with_apple_reserve_approximate: {min: 70, max: 93},
  high_intentional_use: {min: 300, max: 500}
}, "pricing evidence bands");
assert(requirementIds.has("PROGRAM-SEQUENCE-001"), "integrated program trace requirement");
assert(testIds.has("ROADMAP-HARMONY-001"), "integrated roadmap harmony test ID");
const baselineIds = new Set(acceptance.baseline_measure_plan.map(item => item.id));
exact(contracts["surface-registry-migration-map.v1.json"].baseline_measure_ids, baselineIds, "surface baseline refs");
assert.equal(acceptance.baseline_measure_plan.length, 5, "five baseline measures");
for (const baseline of acceptance.baseline_measure_plan) {
  for (const field of ["measure", "baseline_method", "bound_product_action", "post_launch_comparison"]) assert(baseline[field], `${baseline.id}.${field}`);
  assert(!("value" in baseline), `${baseline.id} must not invent a value`);
}
assert.equal(contracts["domain-glossary.v1.json"].operating_objective.objectives.length, 8, "eight objectives");
const cutoverOwners = new Map(surfaceMap.cutover_owner_assignments.map(item => [item.id, item]));
assert.equal(cutoverOwners.size, 4, "four explicit surface cutover owner assignments");
for (const surface of surfaceMap.surfaces.filter(item => item.cutover_owner.startsWith("OPEN-SURFACE-OWNER-"))) assert.equal(cutoverOwners.get(surface.cutover_owner)?.surface_id, surface.id, `surface owner ${surface.cutover_owner}`);
assert.equal(tenantGovernance.status, "phase0_contract_only_no_production_enforcement_claim", "tenant governance Phase 0 status");
assert.equal(tenantGovernance.canonical_sources.find(item => item.document_id === manifest.source.durable_doctrine_id).generation, 344, "tenant governance Workspace generation");
exact(tenantGovernance.tenant_context_contract.propagation_channels, ["browser", "api", "background_job", "local_edge_sync", "search", "export", "attachment", "doc", "ai"], "tenant propagation channels");
exact(tenantGovernance.tenant_context_contract.scoped_resource_classes, ["records", "events", "search", "files_and_attachments", "calls", "ai_memory_and_retrieval", "queues", "integrations", "audit", "offline_packs"], "tenant scoped resource classes");
exact(tenantDenialFixture.cases.map(item => item.channel), tenantGovernance.cross_tenant_denial_contract.required_channels, "tenant denial fixture channels");
exact(tenantDenialFixture.cases.map(item => item.expected), ["deny_before_render", "deny_before_object_lookup_response", "deny_before_job_execution", "deny_before_sync_acceptance", "zero_cross_tenant_hits", "deny_before_export_creation", "deny_before_metadata_or_content", "deny_before_context_assembly", "deny_before_prompt_or_tool_context"], "tenant denial fixture semantics");
assert.notEqual(tenantDenialFixture.authoritative_context.tenant_id, tenantDenialFixture.other_tenant.tenant_id, "tenant denial fixture tenant separation");
assert(tenantDenialFixture.cases.every(item => item.target_tenant_id === tenantDenialFixture.other_tenant.tenant_id), "tenant denial target must be foreign tenant");
assert.equal(tenantDenialFixture.expected_response.matches_nonexistent_target, true, "tenant denial privacy parity");
assert.equal(tenantDenialFixture.expected_response.returns_business_payload, false, "tenant denial business payload");
assert.equal(tenantDenialFixture.expected_response.returns_object_metadata, false, "tenant denial object metadata");
assert.match(tenantGovernance.bounded_workflow_trial_acceptance.duration, /at most five business days; shorter trials are allowed/, "workflow trial duration semantics");
assert(tenantGovernance.bounded_workflow_trial_acceptance.maximum_duration_business_days <= 5, "workflow trial maximum duration");
exact(tenantGovernance.bounded_workflow_trial_acceptance.safety_clause, ["trial creates no new source of truth", "trial performs no destructive migration", "trial creates no parallel writer", "trial does not introduce or depend on a generic workflow engine"], "workflow trial safety clause");
for (const exclusion of ["generic workflow engine", "plugin marketplace", "customer scripting"]) {
  assert(tenantGovernance.launch_topology.explicit_exclusions.includes(exclusion), `launch exclusion ${exclusion}`);
  assert(tenantGovernance.tenant_configuration_contract.forbidden_configuration.includes(exclusion), `configuration exclusion ${exclusion}`);
}
exact(stateContract.machines.workflow_definition.states, ["experimental", "pilot", "approved", "standard", "retired"], "workflow lifecycle");
assert.equal(tenantGovernance.maintenance_accounting_contract.observed_baseline_hours, null, "maintenance observed baseline");
assert.equal(tenantGovernance.maintenance_accounting_contract.normal_internal_target_human_hours_per_month.minimum, 3, "maintenance target minimum");
assert.equal(tenantGovernance.maintenance_accounting_contract.normal_internal_target_human_hours_per_month.maximum, 5, "maintenance target maximum");
assert.equal(tenantGovernance.maintenance_accounting_contract.escalation_gate, "more than five normal internal maintenance hours in each of two consecutive months creates prioritized toil-reduction work", "maintenance escalation gate");
assert.equal(tenantGovernance.maintenance_accounting_contract.low_toil_mature_claim_gate, "a low-toil or mature claim requires three complete consecutive normal months at three to five human hours, with required control evidence intact and exceptions reported separately", "maintenance claim gate");
assert.equal(tenantGovernance.maintenance_accounting_contract.required_control_evidence_for_claim.length, 7, "maintenance claim control evidence");
assert.equal(api.tenant_context_policy.client_tenant_fields_authoritative, false, "client tenant authority");
assert(api.$defs.VersionedRecord.required.includes("tenant_id"), "tenant ID on versioned records");
exact(sponsorIdentityFixture.immutable_dimensions, ["organization_tenant_id", "sponsoring_human_id", "partner_id", "agent_principal_id", "runtime_principal", "session_capability_profile", "personal_brain_scope", "personal_brain_version"], "sponsor/runtime identity dimensions");
assert.match(sponsorIdentityFixture.status, /reconciled_to_verified_runtime_repair/, "identity runtime evidence status");
const identityCases = new Map(sponsorIdentityFixture.cases.map(item => [item.id, item]));
assert.equal(identityCases.size, 8, "identity fixture case count");
const validatePrincipalExecutionContext = compileSchema(api, api.$defs.PrincipalExecutionContext);
for (const [id, item] of identityCases) {
  const validation = validatePrincipalExecutionContext(item.server_context);
  assert.equal(validation.valid, true, `${id} PrincipalExecutionContext schema errors: ${validation.errors.join("; ")}`);
}
for (const id of ["joe_codex_rules", "joe_claude_rules"]) {
  assert.equal(identityCases.get(id).expected.shared_rule_count, 144, `${id} shared count`);
  assert.equal(identityCases.get(id).expected.personal_rule_count, 30, `${id} Joe-personal count`);
}
assert.equal(identityCases.get("dell_codex_rules").expected.personal_rule_count_source, "fresh_resolved_dell_personal_artifact", "Dell count source");
assert.equal(identityCases.get("dell_codex_rules").expected.zero_requires_explicit_resolved_scope, true, "Dell explicit zero resolution semantics");
assert.equal(identityCases.get("cross_brain_read").expected.safe_error_code, "PERSONAL_SCOPE_REFUSED", "cross-brain refusal");
assert.equal(identityCases.get("unattended_agent").expected.human_only_capability, false, "unattended humanOnly refusal");
assert.equal(identityCases.get("unattended_agent").server_context.personal_brain_scope, "none", "unattended personal brain scope");
assert.equal(identityCases.get("unattended_agent").server_context.personal_rule_count, 0, "unattended explicit no-personal count");
assert.equal(identityCases.get("unattended_agent").server_context.shared_rule_count, 144, "unattended shared rule count");
assert.equal(identityCases.get("dell_codex_rules").server_context.personal_rule_count, 0, "resolved Dell explicit zero");
assert.equal(identityCases.get("missing_scope").expected.status, "Unknown", "missing scope status");
assert.equal(identityCases.get("missing_scope").expected.personal_rule_count, null, "missing scope null count");
assert.equal(identityCases.get("connector_fallback_parity").expected.connector_counts_equal_fallback_counts, true, "connector fallback count parity");
assert.equal(identityCases.get("personal_rules_non_escalation").expected.silent_promotion, false, "personal rule silent promotion");
assert.equal(api.sponsor_runtime_identity_policy.failure_semantics.includes("successful zero"), true, "identity failure semantics");
assert(api.$defs.PrincipalExecutionContext.required.includes("sponsoring_human_id") && api.$defs.PrincipalExecutionContext.required.includes("agent_principal_id") && api.$defs.PrincipalExecutionContext.required.includes("personal_brain_scope"), "principal execution context fields");
const identityEnvelopeFields = new Set(events.event_envelope.required);
for (const field of ["organization_tenant_id", "agent_principal_id", "runtime_principal", "sponsoring_human_id", "partner_id", "personal_brain_scope", "personal_brain_version", "shared_rule_count", "personal_rule_count", "session_capability_profile", "identity_resolution_status"]) assert(identityEnvelopeFields.has(field), `identity audit field ${field}`);
assert(events.event_envelope.forbidden.includes("personal_rule_bodies") && events.event_envelope.forbidden.includes("shared_rule_bodies"), "identity audit body exclusion");
assert.match(acceptance.sponsor_runtime_identity_acceptance.current_gate_status, /^passed_live_and_deterministic_matrix/, "identity acceptance must record verified gate");

const endpointFields = api.api_policy.required_endpoint_declarations.map(field => field === "idempotency" ? "idempotency_or_read_semantics" : field === "response_schema" ? "response_schema" : field);
for (const endpoint of api.prototype_read_routes) for (const field of endpointFields) assert(field in endpoint, `${endpoint.operation_id}.${field}`);
for (const endpoint of api.prototype_read_routes) assert(api.$defs[endpoint.response_schema.split("/").at(-1)], `${endpoint.operation_id} response schema`);
for (const endpoint of api.prototype_read_routes) assert(api.$defs[endpoint.request_schema.split("/").at(-1)], `${endpoint.operation_id} request schema`);
for (const group of api.domain_groups) for (const entity of group.entities) {
  const schema = api.$defs[entity];
  assert(schema, `domain entity ${entity}`);
  assert.equal(schema.type, "object", `domain entity ${entity} must be an object contract`);
  assert.equal(schema.additionalProperties, false, `domain entity ${entity} must refuse undeclared fields`);
  assert(schema.required.length >= 3, `domain entity ${entity} must declare Phase 0 fields`);
}
assert.equal(api.$defs.CallCandidate.properties.evidence_excerpt["x-maxWords"], 15, "evidence word limit");

for (const [machineName, machine] of Object.entries(stateContract.machines)) {
  const states = new Set(machine.states);
  const terminal = new Set(machine.terminal);
  for (const transition of machine.allowed) {
    const from = Array.isArray(transition.from) ? transition.from : [transition.from];
    assert(!from.includes("*"), `${machineName} allowed wildcard`);
    for (const state of from) {
      assert(states.has(state), `${machineName} source ${state}`);
      assert(!terminal.has(state), `${machineName} terminal source ${state}`);
    }
    assert(states.has(transition.to), `${machineName} destination ${transition.to}`);
  }
}
exact(api.$defs.CallSummary.properties.processing_state.enum, stateContract.machines.call_session.states, "call lifecycle schema/machine");

const expectedSurfaces = fixtureContract.required_surfaces;
const expectedStates = fixtureContract.required_states;
exact(fixtures.map(fixture => fixture.surface), expectedSurfaces, "fixture surfaces");
const topKeys = ["schema_version", "surface", "requirement_ids", "synthetic", "states"];
const stateKeys = {
  "call-review": { normal: ["freshness", "call"] },
  "command-center": { normal: ["freshness", "headline", "items", "metrics"], default: ["freshness", "message", "items"] },
  "deal-room": { normal: ["freshness", "record", "parking_reasons"], partial: ["freshness", "message", "record"], conflict: ["freshness", "message", "conflict"] },
  "doc-request": { normal: ["freshness", "context", "answer", "request"] },
  "lead-board": { normal: ["freshness", "items"] },
  "market-map": { normal: ["freshness", "market"] },
  marketing: { normal: ["freshness", "items"] },
  more: { normal: ["freshness", "destinations", "registry_only"] },
  notifications: { normal: ["freshness", "items"] },
  tour: { normal: ["freshness", "tour"], offline: ["freshness", "message", "tour"] }
};
for (const fixture of fixtures) {
  exact(Object.keys(fixture), topKeys, `${fixture.surface} top keys`);
  assert.equal(fixture.schema_version, "workspace-fixture/v1");
  assert.equal(fixture.synthetic, true);
  exact(Object.keys(fixture.states), expectedStates, `${fixture.surface} states`);
  for (const state of expectedStates) {
    const expected = stateKeys[fixture.surface][state] || stateKeys[fixture.surface].default || (state === "normal" ? stateKeys[fixture.surface].normal : ["freshness", "message"]);
    exact(Object.keys(fixture.states[state]), expected, `${fixture.surface}.${state} keys`);
    assert(fixture.states[state].freshness, `${fixture.surface}.${state}.freshness`);
  }
}

console.log(JSON.stringify({ok: true, contracts: Object.keys(contracts).length, fixtures: fixtures.length, states: expectedStates.length, requirements: requirementIds.size, aliases: aliases.size, test_catalog: testIds.size, future_gates: futureGateIds.size, phase0_executable_tests: testIds.size - futureGateIds.size, events: eventIds.size, council_ids: councilIds.size, baselines: baselineIds.size, planning_sources: expectedPlanningSourceIds.size, machines: Object.keys(stateContract.machines).length, endpoints: api.prototype_read_routes.length, domain_groups: api.domain_groups.length}, null, 2));
