// Render the control room prototype surfaces and scenario navigation.
import {loadFixture, prepareFixtureForPresentation, SCENARIOS, SURFACES} from "./client.js";

const app = document.querySelector("#app");
const nav = document.querySelector("#primary-nav");
const scenarioSelect = document.querySelector("#scenario-select");
const environmentLabel = document.querySelector("#environment-label");
const viewStatus = document.querySelector("#view-status");

let surface = location.hash.replace("#", "") || "overview";
if (!SURFACES.some(item => item.id === surface)) surface = "overview";
let scenario = "normal";

function node(tag, options = {}, children = []) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (key === "class") element.className = value;
    else if (key === "text") element.textContent = value;
    else if (key === "dataset") Object.assign(element.dataset, value);
    else if (key.startsWith("aria-")) element.setAttribute(key, value);
    else element[key] = value;
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child == null) continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

function status(value) {
  return node("span", {class: `status ${String(value).toLowerCase().replaceAll(" ", "_")}`, text: String(value).replaceAll("_", " ")});
}

function panel(title, content, subtitle = "") {
  const header = node("div", {class: "panel-header"}, [
    node("div", {}, [node("h2", {text: title}), subtitle ? node("p", {text: subtitle}) : null])
  ]);
  return node("section", {class: "panel"}, [header, content]);
}

function list(items, render) {
  return node("ul", {class: "list"}, items.map((item, index) => node("li", {}, render(item, index))));
}

function definitionList(entries) {
  const dl = node("dl", {class: "definition-list"});
  for (const [term, value] of entries) {
    dl.append(node("dt", {text: term}), node("dd", {text: value == null ? "Unknown" : String(value)}));
  }
  return dl;
}

function pageHeading(kicker, title, description, fixture) {
  const evidence = node("div", {class: "evidence-strip"}, [
    evidenceItem("Source", fixture.meta.source.ref),
    evidenceItem("Observed", formatDate(fixture.meta.observed_at)),
    evidenceItem("Freshness", fixture.meta.freshness.state),
    evidenceItem("Correlation", fixture.meta.correlation_id)
  ]);
  return node("header", {class: "page-heading"}, [
    node("div", {}, [node("p", {class: "eyebrow", text: kicker}), node("h1", {text: title}), node("p", {class: "lede", text: description})]),
    evidence
  ]);
}

function evidenceItem(label, value) {
  return node("div", {class: "evidence-item"}, [node("span", {text: label}), node("strong", {text: value || "Unknown"})]);
}

function formatDate(value) {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", {month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC", timeZoneName: "short"}).format(date);
}

function linkTo(label, target) {
  return node("button", {type: "button", class: "link-button", text: label, dataset: {target}});
}

function renderNav() {
  nav.replaceChildren();
  const groups = [...new Set(SURFACES.map(item => item.group))];
  for (const group of groups) {
    const wrapper = node("div", {class: "nav-group"});
    wrapper.append(node("p", {class: "nav-group-title", text: group}));
    for (const item of SURFACES.filter(candidate => candidate.group === group)) {
      const button = node("button", {type: "button", class: "nav-button", text: item.label, dataset: {surface: item.id}});
      if (item.id === surface) button.setAttribute("aria-current", "page");
      wrapper.append(button);
    }
    nav.append(wrapper);
  }
}

function renderScenarioOptions(fixture) {
  scenarioSelect.replaceChildren();
  for (const name of SCENARIOS) {
    const item = fixture.scenarios[name];
    scenarioSelect.append(node("option", {value: name, text: item?.label || name}));
  }
  scenarioSelect.value = scenario;
}

function renderScenario(fixture) {
  viewStatus.replaceChildren();
  const config = fixture.scenarios[scenario];
  if (scenario === "normal") return true;
  const error = ["unauthorized", "conflict", "refusal"].includes(scenario);
  viewStatus.append(node("div", {class: `state-banner ${error ? "error" : ""}`}, [
    node("strong", {text: config.label}),
    node("span", {text: ` · ${config.message}`})
  ]));
  return ["partial", "stale", "offline", "conflict"].includes(scenario);
}

function renderOverview(fixture) {
  const data = fixture.data;
  const envCards = node("div", {class: "grid four"}, data.environments.map(env => {
    const title = node("div", {class: "environment-title"}, [
      node("strong", {text: env.name, dataset: {shape: env.shape === "hexagon" ? "⬢" : env.shape === "diamond" ? "◆" : env.shape === "square" ? "■" : "●"}}),
      status(env.status)
    ]);
    return node("article", {class: `panel environment-card ${env.status}`}, [
      title,
      node("p", {class: "small", text: `${env.source} · freshness ${env.freshness} · verified ${formatDate(env.last_verified)}`}),
      env.affected_workflows.length ? node("p", {text: `Affected: ${env.affected_workflows.join(", ")}`}) : node("p", {text: "No affected workflows reported"}),
      node("p", {class: "next-action", text: `Next: ${env.next_action}`})
    ]);
  }));
  const metrics = node("div", {class: "grid five"}, Object.entries(data.queue).map(([label, value]) =>
    node("div", {class: "panel flat"}, [node("div", {class: "metric", text: value}), node("div", {class: "metric-label", text: label.replaceAll("_", " ")})])
  ));
  metrics.style.gridTemplateColumns = "repeat(5, minmax(0, 1fr))";
  const monitoring = node("div", {class: "callout danger section-gap"}, [
    node("h2", {text: data.monitoring_gap.title}),
    node("p", {text: data.monitoring_gap.detail}),
    linkTo(data.monitoring_gap.action, "service")
  ]);
  return [
    pageHeading("Overview", data.title, "The five answers an operator needs before opening an engineering tool.", fixture),
    envCards,
    monitoring,
    node("div", {class: "section-gap"}, [panel("Work queue", metrics, "Exact synthetic counts by lifecycle")]),
    node("div", {class: "grid two section-gap"}, [
      panel("Needs Joe", list(data.needs_joe, item => [node("div", {class: "list-title"}, [node("span", {text: item.title}), status(item.risk)]), node("p", {text: item.next_action})])),
      panel("Active incident", list(data.incidents, item => [node("div", {class: "list-title"}, [linkTo(item.id, "incident"), status(item.severity)]), node("p", {text: item.impact}), node("p", {text: `Next: ${item.next_action}`})]))
    ]),
    node("div", {class: "grid two section-gap"}, [
      panel("Changes in flight", data.changes ? list(data.changes, item => [node("div", {class: "list-title"}, [linkTo(item.name, "deployment"), status(item.state)]), node("p", {text: `${item.environment} · source ${item.commit} · rollback ${item.rollback_state}`})]) : node("p", {text: "Change evidence withheld in this partial view."})),
      panel("Agent activity", list(data.agent_activity, item => [node("div", {class: "list-title"}, [node("span", {text: item.executor}), status(item.phase)]), node("p", {text: `${item.service} · ${item.environment} · ${item.elapsed}`}), node("p", {text: `Evidence: ${item.last_evidence}`})]))
    ])
  ];
}

function renderService(fixture) {
  const data = fixture.data;
  const table = node("div", {class: "table-wrap"}, [
    node("table", {}, [
      node("thead", {}, node("tr", {}, ["Environment", "State", "Version", "Schema", "Configuration", "Source", "Freshness", "Last verified"].map(text => node("th", {text})))),
      node("tbody", {}, data.environments.map(env => node("tr", {}, [
        node("td", {text: env.label ? `${env.id} · ${env.label}` : env.id}),
        node("td", {}, status(env.status)),
        node("td", {class: "mono", text: env.version || "Not deployed"}),
        node("td", {text: env.schema_version || "Not deployed"}),
        node("td", {text: env.configuration_fingerprint || "Not deployed"}),
        node("td", {text: env.source}),
        node("td", {}, status(env.freshness)),
        node("td", {text: formatDate(env.last_verified)})
      ])))
    ])
  ]);
  return [
    pageHeading("Systems", data.name, data.purpose, fixture),
    node("div", {class: "grid two"}, [
      panel("Service identity", definitionList([["Owner", data.owner], ["Criticality", data.criticality], ["Runtime", data.runtime], ["Current version", data.current_version], ["Authentication boundary", data.authentication_boundary]])),
      panel("Supported workflows", list(data.business_workflows, item => [node("span", {text: item})]))
    ]),
    node("div", {class: "section-gap"}, [panel("Environment comparison", table, "Environment is explicit and planned Staging is never green")]),
    node("div", {class: "grid two section-gap"}, [
      panel("Dependency blast radius", list(data.dependencies, item => [node("div", {class: "list-title"}, [node("span", {text: item.name}), status(item.direction)]), node("p", {text: item.blast_radius})]), "Accessible list equivalent"),
      panel("Health evidence", list(data.health_checks, item => [node("div", {class: "list-title"}, [node("span", {text: item.name}), status(item.result)]), node("p", {text: `${item.source} · ${formatDate(item.last_verified)}`})]))
    ]),
    node("div", {class: "grid two section-gap"}, [
      panel("Registered actuator metadata", list(data.actuators, item => [node("div", {class: "list-title"}, [node("span", {class: "mono", text: item.name}), status(item.phase0_mode)]), node("p", {text: "No invocation route exists in Phase 0."})])),
      node("div", {class: "callout danger"}, [node("h2", {text: data.monitoring_gap.title}), node("p", {text: data.monitoring_gap.action})])
    ])
  ];
}

function renderWorkRequest(fixture) {
  const data = fixture.data;
  return [
    pageHeading("Work", `${data.id} · ${data.title}`, data.desired_outcome, fixture),
    node("div", {class: "grid two"}, [
      panel("Request", definitionList([["State", data.state], ["Requester", data.requester], ["Service", data.service], ["Environment", data.environment], ["Priority", data.priority], ["Risk", data.risk_class], ["Next action", data.next_action]])),
      panel("Safe business origin", definitionList([["Type", data.origin.type], ["Surface", data.origin.safe_label], ["Origin ref", data.origin.deep_link_ref], ["Business payload copied", String(data.origin.business_payload_copied)]]))
    ]),
    node("div", {class: "grid two section-gap"}, [
      panel("Acceptance criteria", list(data.acceptance_criteria, item => [node("div", {class: "list-title"}, [node("span", {text: item.text}), status(item.status)]), node("p", {text: item.evidence_ref ? `Evidence ${item.evidence_ref}` : "Evidence pending"})])),
      panel("Scoped executor", data.executor ? definitionList([["Runtime", data.executor.runtime], ["Session", data.executor.session_id], ["Phase", data.executor.phase], ["Scope", data.executor.scope.join(" · ")], ["Capability", data.executor.capability], ["Expires", formatDate(data.executor.expires_at)]]) : node("p", {text: "Executor evidence withheld in this partial view."}))
    ]),
    node("div", {class: "grid two section-gap"}, [
      panel("Safe Workspace return", definitionList([["Request ID", data.safe_workspace_return.request_id], ["State", data.safe_workspace_return.state], ["Status", data.safe_workspace_return.plain_status], ["Next action", data.safe_workspace_return.next_action]])),
      panel("Lifecycle", node("ol", {class: "timeline"}, data.history.map(item => node("li", {}, [node("strong", {text: item.state.replaceAll("_", " ")}), node("p", {text: `${formatDate(item.at)} · ${item.actor}`})]))))
    ])
  ];
}

function renderPlan(fixture) {
  const data = fixture.data;
  const revision = data.current_revision;
  return [
    pageHeading("Plans & approvals", `${data.id} · revision ${revision.revision}`, "A material revision changed the plan hash and invalidated the earlier approval.", fixture),
    node("div", {class: "callout danger"}, [node("h2", {text: "Prior approval is invalid"}), node("p", {text: `${data.approval.id} binds revision ${data.approval.revision} and ${data.approval.plan_hash}. It cannot authorize revision ${revision.revision}.`})]),
    node("div", {class: "grid two section-gap"}, [
      panel("Current immutable revision", definitionList([["State", revision.state], ["Hash", revision.hash], ["Target", `${data.environment} · ${data.resource_scope.join(", ")}`], ["Material change", revision.material_change], ["Rollback", revision.rollback], ["Cost", revision.material_cost]])),
      panel("Invalidated approval", definitionList([["State", data.approval.state], ["Approver", data.approval.approver], ["Approved", formatDate(data.approval.approved_at)], ["Invalidated", formatDate(data.approval.invalidated_at)], ["Reason", data.approval.reason]]))
    ]),
    node("div", {class: "section-gap"}, [panel("Ordered steps", list(revision.steps, step => [node("div", {class: "list-title"}, [node("span", {text: `${step.order}. ${step.decision}`}), status(step.risk)]), node("p", {text: `Expected evidence: ${step.expected_evidence}`})]))]),
    node("div", {class: "grid two section-gap"}, [
      panel("Session scope", definitionList([["Session", data.session_scope.session_id], ["Sponsor", data.session_scope.sponsoring_human_id], ["Capability", data.session_scope.session_capability_profile], ["Resources", data.session_scope.resources.join(", ")], ["Environment", data.session_scope.environment], ["Expires", formatDate(data.session_scope.expires_at)]])),
      node("div", {class: "callout warning"}, [node("h2", {text: "No production control"}), node("p", {text: data.phase0_notice})])
    ])
  ];
}

function renderDeployment(fixture) {
  const data = fixture.data;
  return [
    pageHeading("Changes", `${data.id} · ${data.release.name}`, "One synthetic rehearsal deployment, observed through registered evidence only.", fixture),
    node("div", {class: "grid three"}, [
      panel("Release", definitionList([["Source SHA", data.release.source_sha], ["Schema", data.release.schema_version], ["Configuration", data.release.configuration_fingerprint]])),
      panel("Current state", definitionList([["Environment", data.environment], ["State", data.state], ["Plan hash", data.plan.hash], ["Browser owns operation", String(data.browser_owns_operation)]])),
      panel("Rollback readiness", definitionList([["State", data.rollback.state], ["Strategy", data.rollback.strategy], ["Evidence", data.rollback.evidence_ref]]))
    ]),
    node("div", {class: "grid two section-gap"}, [
      panel(
        "Authoritative event replay",
        node("ol", {class: "timeline"}, data.events.map(item =>
          node("li", {}, [
            node("strong", {text: item.type}),
            node("p", {text: `${item.cursor} · ${formatDate(item.at)} · ${item.result}`})
          ])
        ))
      ),
      panel("Verification", list(data.verification, item => [node("div", {class: "list-title"}, [node("span", {text: item.name}), status(item.result)]), node("p", {text: `Expected: ${item.expected}`}), node("p", {text: `Actual: ${item.actual ?? "Pending"}`})]))
    ]),
    node("div", {class: "section-gap"}, [
      panel("Registered actuator metadata", list([data.actuator], item => [node("div", {class: "list-title"}, [node("span", {class: "mono", text: item.name}), status(item.phase0_mode)]), node("p", {text: "No invocation route exists in Phase 0."})]))
    ]),
    node("div", {class: "callout warning section-gap"}, [node("h2", {text: "Completion gate"}), node("p", {text: data.completion_rule}), node("p", {text: `Next: ${data.next_action}`})])
  ];
}

function renderIncident(fixture) {
  const data = fixture.data;
  return [
    pageHeading("Reliability", `${data.id} · ${data.title}`, data.business_impact, fixture),
    node("div", {class: "grid three"}, [
      panel("Incident state", definitionList([["Severity", data.severity], ["State", data.state], ["Owner", data.owner], ["Detected", formatDate(data.detected_at)], ["Next action", data.next_action]])),
      panel("Affected workflows", list(data.affected_workflows, item => [node("span", {text: item})])),
      panel("Safe Workspace banner", definitionList([["Impact", data.workspace_banner.impact], ["Workaround", data.workspace_banner.workaround], ["Status", data.workspace_banner.status]]))
    ]),
    node("div", {class: "grid two section-gap"}, [
      panel("Facts", list(data.facts, item => [node("div", {class: "list-title"}, [node("span", {text: item.text}), status("fact")]), node("p", {text: `${item.source} · ${formatDate(item.recorded_at)}`})])),
      panel("Hypotheses", data.hypotheses ? list(data.hypotheses, item => [node("div", {class: "list-title"}, [node("span", {text: item.text}), status(item.confidence)]), node("p", {text: "Unconfirmed and displayed separately from facts."})]) : node("p", {text: "Restricted hypotheses withheld in this partial view."}))
    ]),
    node("div", {class: "grid two section-gap"}, [
      panel(
        "Timeline",
        node("ol", {class: "timeline"}, data.timeline.map(item =>
          node("li", {}, [
            node("strong", {text: item.type}),
            node("p", {text: `${formatDate(item.at)} · ${item.actor} · ${item.evidence_ref}`})
          ])
        ))
      ),
      node("div", {class: "callout danger"}, [node("h2", {text: "Resolution gate not met"}), node("p", {text: `${data.resolution_gate.monitoring_interval_required} monitoring and recovery evidence are required before Resolved.`}), linkTo("Trace the evidence chain", "audit")])
    ])
  ];
}

function renderAudit(fixture) {
  const data = fixture.data;
  return [
    pageHeading("Governance", data.title, "One immutable synthetic chain from origin through verification and incident disposition.", fixture),
    node("div", {class: "grid two"}, [
      panel("Audit policy", definitionList([["Append-only", String(data.append_only)], ["Environment", data.filters.environment], ["Correlation", data.filters.correlation_id], ["Update", String(data.mutation_controls.update)], ["Delete", String(data.mutation_controls.delete)], ["Export", String(data.mutation_controls.export)]])),
      panel("Redaction declaration", definitionList([["Profile", data.redaction_declaration.profile], ["Withheld", data.redaction_declaration.withheld.join(", ")], ["Secret values present", String(data.redaction_declaration.secret_values_present)], ["Plan versus execution", data.plan_vs_execution.result]]))
    ]),
    node("div", {class: "section-gap"}, [
      panel(
        "Origin to outcome",
        node("ol", {class: "timeline"}, data.chain.map(item =>
          node("li", {}, [
            node("div", {class: "list-title"}, [node("span", {text: `${item.stage} · ${item.event_type}`}), status(item.result)]),
            node("p", {text: `${item.event_id} · ${formatDate(item.occurred_at)} · ${item.actor}`}),
            node("p", {class: "mono", text: `resource ${item.resource_id} · cause ${item.causation_id || "origin"} · evidence ${item.evidence_refs.join(", ")}`})
          ])
        ))
      )
    ])
  ];
}

const renderers = {
  overview: renderOverview,
  service: renderService,
  "work-request": renderWorkRequest,
  "plan-approval": renderPlan,
  deployment: renderDeployment,
  incident: renderIncident,
  audit: renderAudit
};

async function render() {
  renderNav();
  app.replaceChildren(node("div", {class: "panel", text: "Loading synthetic evidence…"}));
  viewStatus.replaceChildren();
  try {
    const {fixture: sourceFixture, schema} = await loadFixture(surface);
    renderScenarioOptions(sourceFixture);
    const fixture = prepareFixtureForPresentation(sourceFixture, scenario, schema);
    environmentLabel.textContent = fixture.meta.environment_scope[0].toUpperCase() + fixture.meta.environment_scope.slice(1);
    const showData = renderScenario(fixture);
    if (!showData) {
      app.replaceChildren(node("section", {class: "panel"}, [
        node("p", {class: "eyebrow", text: fixture.surface}),
        node("h1", {text: fixture.scenarios[scenario].label}),
        node("p", {class: "lede", text: fixture.scenarios[scenario].message}),
        fixture.scenarios[scenario].action ? node("button", {class: "link-button", type: "button", text: fixture.scenarios[scenario].action, dataset: {retry: "true"}}) : null
      ]));
    } else {
      app.replaceChildren(...renderers[surface](fixture));
    }
    viewStatus.setAttribute("aria-label", `${fixture.surface} prototype state: ${scenario}`);
  } catch (error) {
    app.replaceChildren(node("section", {class: "panel"}, [node("h1", {text: "Prototype fixture unavailable"}), node("p", {text: error.message})]));
  }
}

nav.addEventListener("click", event => {
  const button = event.target.closest("[data-surface]");
  if (!button) return;
  surface = button.dataset.surface;
  scenario = "normal";
  location.hash = surface;
  render();
});

scenarioSelect.addEventListener("change", () => {
  scenario = scenarioSelect.value;
  render();
});

document.addEventListener("click", event => {
  const target = event.target.closest("[data-target]");
  if (target) {
    surface = target.dataset.target;
    scenario = "normal";
    location.hash = surface;
    render();
  }
  if (event.target.closest("[data-retry]")) render();
});

addEventListener("hashchange", () => {
  const next = location.hash.replace("#", "");
  if (SURFACES.some(item => item.id === next) && next !== surface) {
    surface = next;
    scenario = "normal";
    render();
  }
});

render();
