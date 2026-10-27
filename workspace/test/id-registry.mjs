// Register workspace requirement and test checks.
function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}

const checks = {
  accessibility(context) {
    requireCondition(context.html.includes('class="skip-link"'), "skip link missing");
    requireCondition(context.css.includes("min-height: 44px"), "touch-target floor missing");
    requireCondition(context.css.includes("prefers-reduced-motion"), "reduced-motion contract missing");
  },
  aiBoundary(context) {
    requireCondition(context.html.includes("Doc cannot send, publish, spend, change ownership, move phases, or deploy"), "Doc authority boundary missing");
    requireCondition(!/method:\s*["'](?:POST|PUT|PATCH|DELETE)["']/i.test(context.app + context.client), "mutation transport present");
  },
  aiDisplacement(context) {
    const more = context.fixtures.get("more").states.normal;
    const cutover = more.destinations.find(item => Array.isArray(item.baselines));
    requireCondition(cutover?.text === "Can Joe or Dell now complete and understand this routine without opening Claude Code or Codex?", "exact cutover question missing");
    requireCondition(cutover.baselines.length === 5 && cutover.baselines.every(item => item.value === null && item.status === "Unknown — not yet measured"), "baseline values must remain unknown");
  },
  call(context) {
    const call = context.fixtures.get("call-review").states.normal.call;
    for (const group of ["facts", "decisions", "objectives_deliverables", "joe_actions", "dell_actions", "outside_party_needs", "draft_communications", "initiated_work_history"]) requireCondition(Array.isArray(call.sections[group]), `call section ${group} missing`);
    requireCondition(call.retention.purge_eligible === false, "retention must begin gated");
    requireCondition(context.app.includes("consent-confirmed") && context.app.includes("complete-call"), "call journey controls missing");
  },
  commandCenter(context) {
    const home = context.fixtures.get("command-center").states.normal;
    requireCondition(home.headline === `${home.items.length} decisions need Joe`, "headline count mismatch");
    requireCondition(home.items.every(item => item.owner === "Joe"), "partner-specific owner mismatch");
    requireCondition(!JSON.stringify(home.items).match(/lead outreach/i), "named lead outreach leaked to home");
  },
  deal(context) {
    const deal = context.fixtures.get("deal-room").states.normal;
    requireCondition(deal.parking_reasons.includes("Other"), "Other parking reason missing");
    requireCondition(Object.values(deal.record.active_pressure).every(Boolean), "active-pressure starting twin invalid");
    requireCondition(context.app.includes("Context is required when the parking reason is Other"), "Other context guard missing");
    requireCondition(context.app.includes("Confirm synthetic restore"), "restore control missing");
  },
  doc(context) {
    const request = context.fixtures.get("doc-request").states.normal.request;
    requireCondition(request.state === "draft", "request must start draft");
    requireCondition(context.app.includes("request-add-evidence") && context.app.includes("request-complete"), "evidence-gated lifecycle missing");
    requireCondition(!context.app.includes("request-state"), "unsafe lifecycle dropdown present");
  },
  externalBoundary(context) {
    requireCondition(!/method:\s*["'](?:POST|PUT|PATCH|DELETE)["']/i.test(context.app + context.client), "external mutation method present");
    requireCondition(context.app.includes("Sending is structurally absent"), "no-send statement missing");
  },
  fixtureContract(context) {
    for (const fixture of context.fixtures.values()) {
      requireCondition(fixture.synthetic === true, "non-synthetic fixture");
      requireCondition(Object.keys(fixture.states).length === 10, "fixture state count mismatch");
    }
  },
  lead(context) {
    requireCondition(context.fixtures.get("lead-board").states.normal.items.length > 0, "lead twin missing");
    requireCondition(context.app.includes("Named lead outreach reminders appear only here"), "lead ownership boundary missing");
  },
  marketing(context) {
    requireCondition(context.app.includes("Publishing, media spend, audience changes, and outbound action are structurally unavailable"), "marketing gate missing");
  },
  marketMap(context) {
    const contract = context.marketMapContract;
    const fixture = context.fixtures.get("market-map").states.normal.market;
    requireCondition(contract.status.includes("not_implemented"), "market-map contract must not claim production implementation");
    requireCondition(contract.authority_and_truth.map_role.includes("read projection") && contract.authority_and_truth.writer_rule.includes("never becomes a source of truth"), "map authority boundary missing");
    requireCondition(["practice_or_current_site", "target_market_or_search_area", "candidate_property", "confirmed_project_site", "appointment_or_visit_stop"].every(role => contract.location_contract.location_roles.includes(role)), "location-role contract incomplete");
    requireCondition(contract.location_contract.cardinality_rule.includes("zero, one, or many") && contract.location_contract.unlocated_rule.includes("never silently omits"), "multi-location or unlocated semantics missing");
    requireCondition(["organization_tenant_id", "business_workspace_id", "record_type", "record_id", "record_version", "location_role", "location_source", "location_projection_version", "location_precision", "position", "observed_at", "freshness", "redaction_profile", "deep_link", "trip_eligible", "trip_eligibility_reason"].every(field => contract.location_contract.required_fields.includes(field)), "critical map-item field contract missing");
    requireCondition(contract.visual_layers.includes("Visits and appointments"), "visit/appointment visual layer missing");
    requireCondition(contract.authority_and_truth.freshness_rule.includes("Mixed/Partial") && ["current", "stale", "Unknown", "unlocated"].every(label => contract.authority_and_truth.freshness_rule.includes(label)), "mixed aggregate truth missing");
    requireCondition(contract.visual_interaction_contract.map_list_parity.includes("bidirectionally synchronized") && contract.visual_interaction_contract.cluster_rule.includes("only after tenant/workspace authorization"), "authorized map/list/cluster parity missing");
    requireCondition(contract.visual_interaction_contract.temporal_filter_rule.includes("Today, 7 days, 30 days") && contract.visual_interaction_contract.temporal_filter_rule.includes("separate from record freshness"), "planning-horizon/freshness separation missing");
    requireCondition(contract.day_trip_planning_contract.guards.some(item => item.includes("Unknown or unauthorized locations are excluded")) && contract.day_trip_planning_contract.guards.some(item => item.includes("flexible stop cannot displace a locked appointment")), "trip exclusion or locked-stop guard missing");
    requireCondition(contract.privacy_and_security.sterile_mode.includes("structurally unavailable") && contract.provider_portability_and_terms_gate.construction_gate.length >= 7, "sterile/provider terms gate missing");
    requireCondition(["prospect", "active_client", "project_in_process", "active_project"].every(type => fixture.records.some(record => record.record_type === type)), "market coverage fixture incomplete");
    requireCondition(fixture.records.some(record => record.locations.length > 1) && fixture.records.some(record => record.locations.some(location => location.position === null)), "multi-location or unlocated fixture case missing");
  },
  notification(context) {
    const notices = context.fixtures.get("notifications").states.normal.items;
    requireCondition(notices.every(item => item.destination), "notification deep link missing");
  },
  offline(context) {
    for (const fixture of context.fixtures.values()) requireCondition(fixture.states.offline.freshness.status === "offline", `${fixture.surface} offline truth missing`);
  },
  release(context) {
    requireCondition(context.server.includes('new Set(["GET", "HEAD", "OPTIONS"])'), "static method allow-list missing");
    requireCondition(context.server.includes("405"), "unsupported-method refusal missing");
  },
  roadmapHarmony(context) {
    const sources = new Map(context.manifest.canonical_planning_sources.map(item => [item.slug, item]));
    requireCondition(sources.size === 4, "canonical planning source count mismatch");
    requireCondition(sources.get("carr-workspace-bduf")?.verified_generation === 344, "Workspace doctrine generation mismatch");
    requireCondition(sources.get("carr-workspace-bduf")?.active_unique_sections === 35, "Workspace doctrine section count mismatch");
    requireCondition(sources.get("carr-workspace-bduf")?.timing_section?.version === 4, "Workspace s23 version mismatch");
    requireCondition(sources.get("carr-control-room-bduf")?.verified_generation === 345, "Control Room doctrine generation mismatch");
    requireCondition(sources.get("carr-mature-software-end-state-bduf")?.verified_generation === 346, "mature roadmap generation mismatch");
    requireCondition(sources.get("carr-production-maturity-baseline")?.verified_generation === 336, "production baseline generation mismatch");
    const program = context.manifest.integrated_delivery_program;
    requireCondition(program.mature_foundation_v1.target_date === "2026-10-05", "foundation target mismatch");
    requireCondition(program.workspace_web_timing.planning_estimate === "approximately 12 weeks", "Workspace web plan mismatch");
    requireCondition(program.workspace_web_timing.evidence_range === "12–16 weeks", "Workspace web evidence range mismatch");
    requireCondition(program.full_multi_platform_timing.startsWith("4–6 months"), "mature program timing mismatch");
    requireCondition(program.hard_predecessors.join(" > ").includes("Website Completion"), "Website Completion predecessor missing");
    requireCondition(program.construction_gate.includes("Joe approves") && program.construction_gate.includes("council output"), "Joe approval of the council output is missing");
    requireCondition(context.trace.entries.some(item => item.id === "PROGRAM-SEQUENCE-001"), "program sequence trace missing");
    requireCondition(context.council.review_event.inputs.some(item => item.includes("15d2250c-4821-4f83-9dc5-063f9470139d")), "mature roadmap council input missing");
    requireCondition(context.acceptance.phase0_exit.not_complete_until.some(item => item.includes("Joe approves") && item.includes("all three roadmap versions")), "Joe approval of all three roadmap versions is missing");
  },
  security(context) {
    const fixtures = JSON.stringify([...context.fixtures.values()]);
    requireCondition(!/@[a-z0-9.-]+\.[a-z]{2,}/i.test(fixtures), "email-shaped fixture value");
    requireCondition(!/\b(?:sk|pk)_(?:live|test)_[a-z0-9]+\b/i.test(fixtures), "credential-shaped fixture value");
    requireCondition(context.html.includes("synthetic prototype"), "synthetic boundary missing");
  },
  tenantGovernance(context) {
    const governance = context.governance;
    const exactTenantClasses = ["records", "events", "search", "files_and_attachments", "calls", "ai_memory_and_retrieval", "queues", "integrations", "audit", "offline_packs"];
    const requiredExclusions = ["generic workflow engine", "plugin marketplace", "customer scripting"];
    const exactTrialSafety = ["trial creates no new source of truth", "trial performs no destructive migration", "trial creates no parallel writer", "trial does not introduce or depend on a generic workflow engine"];
    const exactDenials = new Map([
      ["browser", "deny_before_render"],
      ["api", "deny_before_object_lookup_response"],
      ["background_job", "deny_before_job_execution"],
      ["local_edge_sync", "deny_before_sync_acceptance"],
      ["search", "zero_cross_tenant_hits"],
      ["export", "deny_before_export_creation"],
      ["attachment", "deny_before_metadata_or_content"],
      ["doc", "deny_before_context_assembly"],
      ["ai", "deny_before_prompt_or_tool_context"]
    ]);
    requireCondition(governance.status === "phase0_contract_only_no_production_enforcement_claim", "tenant governance Phase 0 boundary missing");
    requireCondition(governance.launch_topology.authorized_people.join("|") === "Joe Bookout|Dell McCraney", "launch people mismatch");
    requireCondition(governance.launch_topology.explicit_exclusions.includes("public SaaS") && governance.launch_topology.explicit_exclusions.includes("client-supplied tenant selector"), "launch exclusions missing");
    requireCondition(requiredExclusions.every(item => governance.launch_topology.explicit_exclusions.includes(item)), "workflow product exclusions missing");
    requireCondition(governance.tenant_context_contract.authoritative_source.startsWith("server derives"), "server tenant derivation missing");
    requireCondition(JSON.stringify(governance.tenant_context_contract.scoped_resource_classes) === JSON.stringify(exactTenantClasses), "tenant resource class contract drift");
    requireCondition(governance.tenant_configuration_contract.forbidden_configuration.includes("arbitrary code") && governance.tenant_configuration_contract.forbidden_configuration.includes("SQL"), "unsafe tenant configuration remains");
    requireCondition(requiredExclusions.every(item => governance.tenant_configuration_contract.forbidden_configuration.includes(item)), "forbidden workflow configuration missing");
    requireCondition(JSON.stringify(governance.mature_rail_boundary.locked).includes("tenant isolation"), "locked tenant rail missing");
    requireCondition(governance.bounded_workflow_trial_acceptance.duration === "at most five business days; shorter trials are allowed", "bounded trial duration semantics mismatch");
    requireCondition(governance.bounded_workflow_trial_acceptance.maximum_duration_business_days <= 5, "workflow trial exceeds five business days");
    requireCondition(JSON.stringify(governance.bounded_workflow_trial_acceptance.safety_clause) === JSON.stringify(exactTrialSafety), "workflow trial safety clause drift");
    requireCondition(governance.workflow_definition_contract.lifecycle.join(",") === "experimental,pilot,approved,standard,retired", "workflow lifecycle mismatch");
    requireCondition(governance.maintenance_accounting_contract.observed_baseline_hours === null, "maintenance baseline was invented");
    requireCondition(governance.maintenance_accounting_contract.normal_internal_target_human_hours_per_month.minimum === 3 && governance.maintenance_accounting_contract.normal_internal_target_human_hours_per_month.maximum === 5, "maintenance target mismatch");
    requireCondition(governance.maintenance_accounting_contract.escalation_gate === "more than five normal internal maintenance hours in each of two consecutive months creates prioritized toil-reduction work", "two-month toil escalation gate drift");
    requireCondition(governance.maintenance_accounting_contract.low_toil_mature_claim_gate === "a low-toil or mature claim requires three complete consecutive normal months at three to five human hours, with required control evidence intact and exceptions reported separately", "three-month low-toil/mature claim gate drift");
    requireCondition(governance.maintenance_accounting_contract.required_control_evidence_for_claim.length === 7 && governance.maintenance_accounting_contract.required_control_evidence_for_claim.every(Boolean), "required maintenance control evidence missing");
    requireCondition(requiredExclusions.every(item => context.acceptance.tenant_boundary_acceptance.configuration_refusals.includes(item)), "acceptance workflow product exclusions missing");
    requireCondition(context.acceptance.workflow_lifecycle_acceptance.five_business_day_trial.maximum_duration_business_days <= 5, "acceptance workflow trial exceeds five business days");
    requireCondition(JSON.stringify(context.acceptance.workflow_lifecycle_acceptance.five_business_day_trial.safety_clause) === JSON.stringify(["no new source of truth", "no destructive migration", "no parallel writer", "no generic workflow engine"]), "acceptance workflow trial safety drift");
    requireCondition(context.acceptance.maintenance_measure_plan.escalation_gate === "More than five normal internal maintenance hours in each of two consecutive months creates prioritized toil-reduction work.", "acceptance two-month toil gate drift");
    requireCondition(context.acceptance.maintenance_measure_plan.low_toil_mature_claim_gate === "A low-toil or mature claim requires three complete consecutive normal months at three to five human hours, with required control evidence intact and exceptions reported separately.", "acceptance three-month claim gate drift");
    requireCondition(context.acceptance.maintenance_measure_plan.required_control_evidence_for_claim.length === 7, "acceptance claim control evidence drift");
    requireCondition(context.tenantDenial.authoritative_context.tenant_id !== context.tenantDenial.other_tenant.tenant_id, "tenant denial fixture uses same authoritative and foreign tenant");
    requireCondition(context.tenantDenial.cases.length === exactDenials.size, "tenant denial case count drift");
    requireCondition(context.tenantDenial.cases.every(item => exactDenials.get(item.channel) === item.expected && item.target_tenant_id === context.tenantDenial.other_tenant.tenant_id), "tenant denial path semantics drift or allow introduced");
    requireCondition(context.tenantDenial.expected_response.code === "TENANT_SCOPE_REFUSED" && context.tenantDenial.expected_response.http === 404 && context.tenantDenial.expected_response.matches_nonexistent_target === true && context.tenantDenial.expected_response.returns_business_payload === false && context.tenantDenial.expected_response.returns_object_metadata === false, "tenant denial response semantics drift");
    const tenantTrace = context.trace.entries.find(item => item.id === "TENANT-BOUNDARY-001");
    const workflowTrace = context.trace.entries.find(item => item.id === "WORKFLOW-LIFECYCLE-001");
    const maintenanceTrace = context.trace.entries.find(item => item.id === "MAINTENANCE-ACCOUNTING-001");
    requireCondition(["records", "events", "search", "files and attachments", "calls", "AI memory and retrieval", "queues", "integrations", "audit", "offline packs"].every(item => tenantTrace.requirement.toLowerCase().includes(item.toLowerCase())), "tenant trace resource classes drift");
    requireCondition(["no new source of truth", "destructive migration", "parallel writer", "generic workflow engine"].every(item => workflowTrace.requirement.includes(item)), "workflow trace safety clause drift");
    requireCondition(maintenanceTrace.requirement.includes("two consecutive months creates prioritized toil-reduction work") && maintenanceTrace.requirement.includes("three complete consecutive normal months"), "maintenance trace gates drift");
    const settled = new Map(context.council.settled_inputs.map(item => [item.id, item.decision]));
    requireCondition(["no new source of truth", "destructive migration", "parallel writer", "generic workflow engine", "plugin marketplaces", "customer scripting"].every(item => settled.get("SET-013").includes(item)), "council workflow safety decision drift");
    requireCondition(settled.get("SET-014").includes("each of two consecutive months creates prioritized toil-reduction work") && settled.get("SET-014").includes("three complete consecutive normal months"), "council maintenance decision drift");
    const threatMap = new Map(context.threat.threats.map(item => [item.id, item]));
    requireCondition(["no new source of truth", "no destructive migration", "no parallel writer"].every(item => threatMap.get("T14").mitigations.includes(item)), "workflow threat safety rails drift");
    requireCondition(threatMap.get("T15").mitigations.some(item => item.includes("two consecutive months")) && threatMap.get("T15").mitigations.some(item => item.includes("three complete consecutive normal months")) && threatMap.get("T15").mitigations.some(item => item.includes("required tenant, authorization, approval, audit, backup/restore, release/rollback, workflow, retention, and exception evidence")), "maintenance threat gates drift");
    const eventMap = new Map(context.events.events.map(item => [item.name, item.outcomes]));
    requireCondition(JSON.stringify(eventMap.get("workspace.workflow.promoted")) === JSON.stringify(["pilot", "approved", "standard", "refused"]), "workflow promotion event drift");
    requireCondition(JSON.stringify(eventMap.get("workspace.workflow.retired")) === JSON.stringify(["retired", "refused"]), "workflow retirement event drift");
    requireCondition(JSON.stringify(eventMap.get("workspace.maintenance.toil_triggered")) === JSON.stringify(["triggered_after_two_consecutive_over_five_normal_months"]), "maintenance toil trigger event drift");
    requireCondition(JSON.stringify(eventMap.get("workspace.maintenance.remedy_opened")) === JSON.stringify(["prioritized_toil_reduction_work_opened"]), "maintenance remedy event drift");
    const pilotGuard = context.machines.machines.workflow_definition.allowed.find(item => item.from === "experimental" && item.to === "pilot").guard;
    requireCondition(pilotGuard.includes("maximum is five business days and may be shorter") && ["no new source of truth", "destructive migration", "parallel writer", "generic workflow engine"].every(item => pilotGuard.includes(item)), "workflow state-machine pilot guard drift");
    const workflowGate = context.environment.runtime_verification_gates.find(item => item.test_id === "WORKFLOW-PROMOTION-001");
    requireCondition(workflowGate.required_evidence.some(item => item.includes("no more than five business days") && item.includes("shorter is allowed")) && workflowGate.required_evidence.some(item => ["no new source of truth", "destructive migration", "parallel writer", "generic workflow engine"].every(label => item.includes(label))), "workflow runtime gate drift");
    const maintenanceGate = context.environment.runtime_verification_gates.find(item => item.test_id === "MAINTENANCE-MEASURE-001");
    requireCondition(maintenanceGate.required_evidence.some(item => item.includes("each of two consecutive months") && item.includes("prioritized toil-reduction work")) && maintenanceGate.required_evidence.some(item => item.includes("three complete consecutive normal months") && item.includes("three to five hours")) && maintenanceGate.required_evidence.some(item => item.includes("tenant, authorization, approval, audit, backup/restore, release/rollback, workflow, retention, and exception evidence")), "maintenance runtime gate drift");
  },
  sponsorRuntimeIdentity(context) {
    const fixture = context.sponsorIdentity;
    const cases = new Map(fixture.cases.map(item => [item.id, item]));
    const dimensions = ["organization_tenant_id", "sponsoring_human_id", "partner_id", "agent_principal_id", "runtime_principal", "session_capability_profile", "personal_brain_scope", "personal_brain_version"];
    requireCondition(fixture.status.includes("reconciled_to_verified_runtime_repair"), "identity runtime evidence status drift");
    requireCondition(JSON.stringify(fixture.immutable_dimensions) === JSON.stringify(dimensions), "identity dimensions drift");
    for (const id of ["joe_codex_rules", "joe_claude_rules"]) {
      requireCondition(cases.get(id).expected.shared_rule_count === 144 && cases.get(id).expected.personal_rule_count === 30, `${id} counts drift`);
      requireCondition(cases.get(id).server_context.personal_brain_scope === "joe-personal", `${id} brain drift`);
    }
    requireCondition(cases.get("dell_codex_rules").expected.personal_rule_count_source === "fresh_resolved_dell_personal_artifact", "Dell count must be fresh-derived");
    requireCondition(cases.get("dell_codex_rules").expected.zero_requires_explicit_resolved_scope === true, "Dell zero semantics missing");
    requireCondition(cases.get("cross_brain_read").expected.safe_error_code === "PERSONAL_SCOPE_REFUSED", "cross-brain read not refused");
    requireCondition(cases.get("unattended_agent").expected.human_only_capability === false && cases.get("unattended_agent").expected.may_claim_human_sponsor === false && cases.get("unattended_agent").expected.personal_brain_scope === "none" && cases.get("unattended_agent").expected.personal_rule_count === 0, "unattended identity semantics drift");
    requireCondition(cases.get("missing_scope").expected.status === "Unknown" && cases.get("missing_scope").expected.personal_rule_count === null && cases.get("missing_scope").expected.successful_zero === false, "missing scope must be Unknown, not zero");
    requireCondition(cases.get("connector_fallback_parity").expected.connector_scope_equals_fallback_scope === true && cases.get("connector_fallback_parity").expected.connector_counts_equal_fallback_counts === true, "connector/fallback parity missing");
    const nonEscalation = cases.get("personal_rules_non_escalation");
    requireCondition(nonEscalation.expected.organization_tenant_id === nonEscalation.server_context.organization_tenant_id && nonEscalation.expected.session_capability_profile === nonEscalation.server_context.session_capability_profile && nonEscalation.expected.personal_brain_scope === nonEscalation.server_context.personal_brain_scope, "personal rule changed immutable identity");
    requireCondition(fixture.audit_expectation.forbidden_fields.includes("personal_rule_bodies") && fixture.audit_expectation.required_safe_fields.includes("agent_principal_id") && fixture.audit_expectation.required_safe_fields.includes("sponsoring_human_id"), "safe identity audit contract missing");
    requireCondition(context.api.sponsor_runtime_identity_policy.failure_semantics.includes("Unknown") && context.api.sponsor_runtime_identity_policy.non_escalation.includes("cannot select tenant"), "API identity policy missing");
    requireCondition(context.authority.identity_boundary.personal_rule_resolution.unsponsored_background.includes("Shared rules and/or server-attached task-scoped rules"), "authority unsponsored rule missing");
    requireCondition(context.acceptance.sponsor_runtime_identity_acceptance.current_gate_status.startsWith("passed_live_and_deterministic_matrix"), "acceptance identity gate status drift");
  },
  surface(context) {
    requireCondition(context.fixtures.size === 9, "surface count mismatch");
    requireCondition(context.app.includes("surface-registry") || context.app.includes("registry destinations"), "surface registry/cutover disclosure missing");
  },
  tour(context) {
    const tour = context.fixtures.get("tour").states.normal.tour;
    requireCondition(tour.stops.every(stop => stop.marker === stop.order), "map/list parity mismatch");
    requireCondition(tour.stops.filter(stop => stop.locked).length >= 1, "locked appointment missing");
    for (const action of ["confirm-route", "capture-note", "capture-photo", "tour-offline", "resolve-tour-conflict", "tour-resume", "complete-tour-review"]) requireCondition(context.app.includes(action), `tour action ${action} missing`);
  }
};

function publicBoundary(context) {
  requireCondition(context.server.includes("const publicFiles = new Map"), "public asset allow-list missing");
  requireCondition(context.server.includes("const fixtureFiles = new Set"), "fixture allow-list missing");
  requireCondition(!context.server.includes("pathname.slice(1)"), "arbitrary workspace path serving remains");
}

function secretScan(context) {
  const fixtureText = JSON.stringify([...context.fixtures.values()]);
  requireCondition(!/\b(?:sk|pk)_(?:live|test)_[a-z0-9]+\b/i.test(fixtureText), "credential-shaped fixture value");
  requireCondition(!/"(?:password|access_token|refresh_token|client_secret)"\s*:/i.test(fixtureText), "secret-shaped fixture key");
}

const groups = {
  accessibility: ["A11Y-001", "RESPONSIVE-001"],
  aiBoundary: ["AI-AUTH-001", "AI-INJECT-001", "AI-LEAK-001", "AI-SAFE-001", "AI-TARGET-001", "DOC-PROP-001", "STATE-REFUSE-001"],
  aiDisplacement: ["AI-DISPLACEMENT-001"],
  call: ["CALL-CAPTURE-001", "CALL-CONSENT-001", "CALL-RET-001", "CALL-REVIEW-001", "CALL-REVIEW-002", "CALL-SPEAKER-001", "FLOW-WS-03"],
  commandCenter: ["CC-FLOW-001", "CC-NO-LEAD-001", "FLOW-WS-01", "OBS-ROUTE-001"],
  deal: ["DEAL-PARK-001", "DEAL-PARK-002", "DEAL-RESTORE-001", "DEAL-UX-001", "FLOW-WS-02"],
  doc: ["BRIDGE-SAFE-001", "DOC-ENG-001", "FLOW-WS-04"],
  externalBoundary: ["EXT-NO-PUBLISH-001", "EXT-NO-SEND-001"],
  fixtureContract: ["CONTRACT-WRITE-001"],
  lead: ["LEAD-CONVERT-001", "LEAD-TOUCH-001"],
  marketing: ["MKT-DECISION-001"],
  marketMap: ["FLOW-WS-06", "MAP-COVERAGE-001", "MAP-PARITY-001", "MAP-TRUTH-001"],
  notification: ["NOTIFY-001"],
  offline: ["OFFLINE-TRUTH-001", "SYNC-CONFLICT-001"],
  release: ["AUTH-KV-001", "AUTH-RECOVERY-001", "RELEASE-GATE-001"],
  roadmapHarmony: ["ROADMAP-HARMONY-001"],
  security: ["PRIV-REDACT-001", "PRIV-STERILE-001", "SEC-AUTH-001", "SEC-AUTHZ-001", "SEC-CSRF-001", "SEC-DEVICE-001", "SEC-PUBLIC-001", "SEC-REPLAY-001", "SEC-SECRET-001"],
  tenantGovernance: ["TENANT-CONTRACT-001", "TENANT-DENIAL-FIXTURE-001", "WORKFLOW-CONTRACT-001", "MAINTENANCE-CONTRACT-001", "OWNER-CONTROL-001"],
  sponsorRuntimeIdentity: ["IDENTITY-CONTRACT-001", "IDENTITY-FIXTURE-001"],
  surface: ["SURFACE-PARITY-001", "SURFACE-WRITER-001"],
  tour: ["FLOW-WS-05", "TOUR-ROUTE-001", "TOUR-SYNC-001"]
};

export const testRegistry = new Map(Object.entries(groups).flatMap(([checkName, ids]) => ids.map(id => [id, checks[checkName]])));
export const futureGateIds = new Set([
  "MAP-A11Y-001",
  "MAP-ADOPTION-001",
  "MAP-PRIVACY-001",
  "MAP-TENANT-001",
  "MAP-TRIP-001",
  "FLOW-WS-06",
  "AI-INJECT-001", "AI-LEAK-001", "AI-TARGET-001", "CALL-SPEAKER-001", "CONTRACT-WRITE-001",
  "SURFACE-PARITY-001", "SURFACE-WRITER-001",
  "SEC-AUTH-001", "SEC-AUTHZ-001", "SEC-CSRF-001", "SEC-DEVICE-001", "SEC-REPLAY-001",
  "AUTH-KV-001", "AUTH-RECOVERY-001", "RELEASE-GATE-001"
  , "TENANT-CROSS-DENIAL-001", "WORKFLOW-PROMOTION-001", "MAINTENANCE-MEASURE-001"
  , "IDENTITY-JOE-RULES-001", "IDENTITY-DELL-RULES-001", "IDENTITY-CROSS-BRAIN-001", "IDENTITY-AUDIT-001", "IDENTITY-UNSPONSORED-001", "IDENTITY-SCOPE-UNKNOWN-001", "IDENTITY-CONNECTOR-PARITY-001", "IDENTITY-NONESCALATION-001"
]);
for (const id of futureGateIds) testRegistry.delete(id);
testRegistry.set("SEC-PUBLIC-001", publicBoundary);
testRegistry.set("SEC-SECRET-001", secretScan);
