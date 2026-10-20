import { authoredStates, isActionableState, loadFixture } from "./client.js";

const surfaces = {
  "command-center": { label: "Command Center", short: "Home", icon: "⌂", fixture: "command-center", description: "Recent updates, pending decisions, and Doc activity." },
  "lead-board": { label: "The Lead Board", short: "Leads", icon: "◎", fixture: "lead-board", description: "Named prospecting reminders live only here." },
  "deal-room": { label: "The Deal Room", short: "Deals", icon: "▤", fixture: "deal-room", description: "Active, parked, and closed work with preserved provenance." },
  "call-review": { label: "Calls", short: "Calls", icon: "◉", fixture: "call-review", description: "Structured facts, decisions, deliverables, drafts, and agent work history—not a transcript library." },
  "marketing": { label: "Marketing", short: "Marketing", icon: "◇", fixture: "marketing", description: "Human-reviewed content and performance decisions." },
  "more": { label: "More", short: "More", icon: "•••", fixture: "more", description: "Tours, notifications, people, documents, activity, and settings." },
  "market-map": { label: "Market Map & Trip Planner", short: "Map", icon: "⌖", fixture: "market-map", description: "See prospects, active clients, and in-process projects across the supported market, then prepare a governed day-trip proposal." },
  "doc-request": { label: "Doc · Improvement request", short: "Doc", icon: "D", fixture: "doc-request", description: "Conversation becomes a governed request, never a direct production change." },
  "tour": { label: "Tour Mode", short: "Tour", icon: "⌖", fixture: "tour", description: "Locked appointments, ordered route, offline pack, notes, and post-tour review." },
  "notifications": { label: "Notification Center", short: "Notices", icon: "◎", fixture: "notifications", description: "The authoritative inbox for unresolved human decisions and failures." }
};

const primaryRoutes = ["command-center", "lead-board", "deal-room", "call-review", "marketing", "more"];
const mobileRoutes = ["command-center", "lead-board", "deal-room", "call-review", "more"];
const model = {
  route: "command-center",
  state: "normal",
  fixtureCache: new Map(),
  recordState: "Active Work",
  recordHistory: [],
  activePressure: null,
  parkingPrepared: false,
  parkingReason: null,
  parkingContext: "",
  dealError: "",
  callSession: null,
  engineeringRequest: null,
  tourSession: null,
  marketFilters: { prospect: true, active_client: true, project_in_process: true, active_project: true, visits: true },
  marketSelectedLocationIds: [],
  marketDetailLocationId: null,
  marketTrip: null,
  marketTripConfirmed: false,
  marketPlanError: "",
  marketTripInputs: { start: "Mobile office demo", end: "Mobile office demo", dwell_minutes: 45, buffer_minutes: 20 },
  marketScope: "all",
  marketHorizonDays: 30,
  marketFreshness: "all",
  marketChangedDays: 30,
  docOpen: false,
  docContextIncluded: true,
  docContextResult: "Page context is included. Remove it at any time.",
  timer: null,
  seconds: 0
};

const main = document.querySelector("#workspace-main");
const statePicker = document.querySelector("#state-picker");
const callControl = document.querySelector("#call-mode");
const docDrawer = document.querySelector("#doc-drawer");
const docLauncher = document.querySelector("#doc-launcher");

function escape(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function formatState(state) {
  return state.replaceAll("-", " ").replace(/\b\w/g, character => character.toUpperCase());
}

function navMarkup(routes) {
  return routes.map(route => {
    const item = surfaces[route];
    return `<a class="nav-link" href="#${route}" data-route="${route}" ${route === model.route || (route === "more" && ["market-map", "tour", "notifications", "doc-request"].includes(model.route)) ? 'aria-current="page"' : ""}><span class="nav-icon" aria-hidden="true">${item.icon}</span><span class="nav-label">${item.short}</span></a>`;
  }).join("");
}

function renderNav() {
  document.querySelector("#primary-nav").innerHTML = navMarkup(primaryRoutes);
  document.querySelector("#mobile-nav").innerHTML = navMarkup(mobileRoutes);
}

function fixtureStatus(data) {
  const freshness = data?.freshness ?? { status: "unknown", as_of: null };
  const asOf = freshness.as_of ? ` · as of ${new Date(freshness.as_of).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}` : "";
  return `<span class="freshness" data-status="${escape(freshness.status)}">${escape(formatState(freshness.status))}${escape(asOf)}</span>`;
}

function pageHeading(title, description, data, requirementIds = []) {
  return `<header class="page-heading"><div><p class="eyebrow">${escape(requirementIds.join(" · ") || "Phase 0 journey")}</p><h1>${escape(title)}</h1><p class="lede">${escape(description)}</p></div>${fixtureStatus(data)}</header>`;
}

function genericState(data) {
  const isError = ["unauthorized", "conflict", "refusal", "retry"].includes(model.state);
  const content = data.message ?? (model.state === "loading" ? "Loading…" : "No fixture message supplied.");
  return `<section class="state-panel"><div class="notice ${isError ? "error" : ""}" role="${isError ? "alert" : "status"}"><strong>${escape(formatState(model.state))}</strong><p>${escape(content)}</p>${model.state === "retry" ? '<button class="button ghost" type="button" data-action="retry-read">Retry safe read</button>' : ""}</div><div class="card"><h2>Truthful behavior</h2><p>Source, freshness, permissions, and recovery are explicit. Consequential controls are unavailable unless the canonical fixture is fresh and complete.</p></div></section>`;
}

// Sterile mode hides .internal-only via CSS (app.css). Hiding it SILENTLY is the
// defect Phase 0 journey 4 caught: the engineering-request panel holds the only
// control that satisfies the completion evidence gate, so with the card hidden
// the request loops between in-progress and needs-answer with nothing on screen
// explaining why Completed is unreachable. The whole-market surface already
// handles the same situation correctly by refusing BY NAME and saying how to
// proceed; this helper is that notice, reused, so suppression is never silent.
function sterileRefusal(what, why) {
  return `<div class="notice error" role="alert"><strong>${escape(what)} refused in Sterile mode</strong><p>${escape(why)} Exit Sterile mode to use it.</p></div>`;
}

function isSterile() {
  return document.body.classList.contains("sterile");
}

function journeySteps(labels, current) {
  return `<ol class="journey-steps" aria-label="Journey progress">${labels.map((label, index) => `<li class="${index === current ? "current" : ""}" ${index === current ? 'aria-current="step"' : ""}>${index + 1}. ${escape(label)}</li>`).join("")}</ol>`;
}

function renderCommandCenter(data) {
  const itemCards = data.items.map(item => `<article class="card"><span class="tag warning">Needs ${escape(item.owner)}</span><h3>${escape(item.title)}</h3><p>${escape(item.why)}</p><p class="meta">Owner: ${escape(item.owner)}</p><footer><button class="button ghost" type="button" data-route="${escape(item.destination.surface)}">${escape(item.next_action)}</button></footer></article>`).join("");
  const metrics = data.metrics.map(metric => `<button class="card metric" type="button" data-route="${escape(metric.destination)}"><span>${escape(metric.label)}</span><strong>${escape(metric.value)}</strong></button>`).join("");
  return `${journeySteps(["See what needs Joe", "Understand why", "Open owning source"], 0)}<section aria-labelledby="needs-heading"><h2 id="needs-heading">${escape(data.headline)}</h2><div class="grid">${itemCards}</div></section><section aria-labelledby="glance-heading" style="margin-top:28px"><h2 id="glance-heading">Business at a glance</h2><div class="grid">${metrics}</div><p class="source-line">Each metric opens its owning module. Named lead outreach remains on The Lead Board.</p></section>`;
}

function renderDealRoom(data) {
  const record = data.record;
  if (!model.activePressure) model.activePressure = structuredClone(record.active_pressure);
  const parked = model.recordState === "Parked";
  const history = [...record.history, ...model.recordHistory];
  const pressure = Object.entries(model.activePressure).map(([name, included]) => `<li><strong>${escape(formatState(name))}:</strong> ${included ? "Included" : "Excluded"}</li>`).join("");
  return `${journeySteps(["Find exact record", "Prepare parking reason", "Confirm", "Verify exclusion and preserved history", "Restore"], parked ? 3 : model.parkingPrepared ? 2 : 0)}<div class="grid two"><article class="card"><span class="tag ${parked ? "warning" : "success"}">${escape(model.recordState)}</span><h2>${escape(record.name)}</h2><p>${escape(record.why)}</p><dl><dt>Owner</dt><dd>${escape(record.owner)}</dd><dt>Source</dt><dd>${escape(record.source)}</dd><dt>Version</dt><dd>${escape(record.version + model.recordHistory.length)}</dd></dl><h3>Preserved history</h3><ul class="detail-list">${history.map(item => `<li>${escape(item)}</li>`).join("")}</ul><p class="source-line">Participants: ${escape(record.participants.join(", "))} · Documents: ${escape(record.documents.join(", "))}</p><h3>Active-pressure read-back</h3><ul class="detail-list">${pressure}</ul></article><section class="card"><h2>${parked ? "Restore active work" : "Park this work record"}</h2>${parked ? `<p>Restoring returns the record to active pressure without rewriting its history or provenance.</p><button class="button secondary" type="button" data-action="restore-record">Confirm synthetic restore</button>` : `<p>Parking is reversible. It excludes the record from active counts, needs-attention, gone-quiet, missing-next-step, and weekly-review pressure.</p><label class="field">Reason<select id="parking-reason">${data.parking_reasons.map(reason => `<option ${reason === model.parkingReason ? "selected" : ""}>${escape(reason)}</option>`).join("")}</select></label><label class="field">Context ${model.parkingReason === "Other" ? "(required for Other)" : ""}<textarea id="parking-context">${escape(model.parkingContext || "Timing remains unknown in this synthetic scenario.")}</textarea></label>${model.dealError ? `<div class="notice error" role="alert">${escape(model.dealError)}</div>` : ""}${model.parkingPrepared ? `<div class="notice"><strong>Proposal ready</strong><p>Reason: ${escape(model.parkingReason)}. Context: ${escape(model.parkingContext)}. Park this exact record, retain provenance and history, and create an attributed event.</p></div><button class="button secondary" type="button" data-action="confirm-park">Confirm synthetic parking</button> <button class="button ghost" type="button" data-action="cancel-park">Edit proposal</button>` : '<button class="button" type="button" data-action="prepare-park">Prepare parking proposal</button>'}`}</section></div>`;
}

function callCandidates(call) {
  return Object.entries(call.sections)
    .filter(([group]) => group !== "initiated_work_history")
    .flatMap(([, items]) => items);
}

function renderCall(data) {
  if (!model.callSession) model.callSession = structuredClone(data.call);
  const call = model.callSession;
  const stageMap = { ready: 0, recording: 1, review: 2, complete: 3 };
  const candidates = callCandidates(call);
  const pending = candidates.filter(candidate => candidate.disposition === "pending").length;
  const sectionTitles = {
    facts: "Itemized facts",
    decisions: "Decisions",
    objectives_deliverables: "Objectives and deliverables",
    joe_actions: "Joe actions",
    dell_actions: "Dell actions",
    outside_party_needs: "What outside parties need",
    draft_communications: "Draft communications"
  };
  const sections = Object.entries(sectionTitles).map(([group, title]) => `<section class="call-section"><h3>${title}</h3>${call.sections[group].map(candidate => `<div class="candidate"><div><p>${escape(candidate.text)}</p><span class="meta">Exact target: ${escape(candidate.record_id)} · ${escape(candidate.disposition)}</span></div><div class="candidate-actions">${call.capture_stage === "review" && candidate.disposition === "pending" ? `<button class="button" type="button" data-candidate="${candidate.id}" data-disposition="confirmed">Confirm</button><button class="button ghost" type="button" data-candidate="${candidate.id}" data-disposition="skipped">Skip</button>` : ""}</div></div>`).join("")}</section>`).join("");
  let controls = `<label class="field"><span><input id="consent-confirmed" type="checkbox" ${call.consent_confirmed ? "checked" : ""}> Everyone was notified; microphone sources and speaker mapping were checked.</span></label><button class="button secondary" type="button" data-action="begin-recording">Begin demo recording</button>`;
  if (call.capture_stage === "recording") controls = `<div class="notice"><strong>Recording · demo only</strong><p>The persistent Call Mode control remains visible across routes. This prototype captures no audio.</p></div><button class="button secondary" type="button" data-action="stop-recording">Stop and prepare review</button>`;
  if (call.capture_stage === "review") controls = `<p>${pending} candidate${pending === 1 ? "" : "s"} unresolved. Each proposal stays independently visible.</p><button class="button secondary" type="button" data-action="complete-call" ${pending ? "disabled" : ""}>Confirm aggregate summary and finish review</button>`;
  if (call.capture_stage === "complete") controls = `<div class="notice"><strong>Review complete</strong><p>Structured review is complete. Retention eligibility: ${call.retention.purge_eligible ? "Eligible" : "Blocked"}. ${escape(call.retention.reason)} No raw capture exists in this prototype.</p></div>`;
  const initiated = isSterile()
    ? sterileRefusal("Agent work history", "This internal section shows which agent acted, its scope, and its evidence.")
    : call.sections.initiated_work_history.map(item => `<article class="card internal-only"><span class="tag warning">${escape(item.outcome)}</span><h3>${escape(item.request)}</h3><p>${escape(item.why)}</p><p class="meta">Executor: ${escape(item.executor)} · Started: ${escape(item.started_at ?? "Not started")} · Evidence: ${item.evidence.length ? escape(item.evidence.join(", ")) : "None"}</p></article>`).join("");
  return `${journeySteps(["Consent gate", "Recording", "Independent structured review", "Retention eligibility"], stageMap[call.capture_stage])}<div class="grid two"><section class="card"><h2>${escape(call.title)}</h2><p class="meta">Participants: ${escape(call.participants.join(", "))}</p><p>Active records: ${escape(call.active_records.join(", "))}</p><p>Parked records excluded: ${escape(call.parked_records_excluded.join(", "))}</p>${controls}<h3>Retention gate</h3><p><strong>${call.retention.purge_eligible ? "Eligible" : "Blocked"}:</strong> ${escape(call.retention.reason)}</p><p class="meta">Candidates dispositioned: ${call.retention.all_candidates_dispositioned ? "Yes" : "No"} · Aggregate confirmed: ${call.retention.aggregate_summary_confirmed ? "Yes" : "No"} · Legal hold explicitly false: ${call.retention.legal_hold === false ? "Yes" : "No"} · Policy resolved: ${call.retention.policy_resolved ? "Yes" : "No"} · Recovery window elapsed: ${call.retention.recovery_window_elapsed ? "Yes" : "No"}</p></section><section class="card"><h2>Structured call detail</h2>${sections}<h3>Related records and files</h3><p>${escape(call.related_records.map(item => item.label).join(", "))} · ${escape(call.related_files.map(item => item.label).join(", "))}</p><p class="boundary-note">Outside communication remains a draft. Sending is structurally absent. There is no transcript library.</p></section></div><section style="margin-top:24px"><h2>Work initiated by Doc, Claude, or Codex</h2><div class="grid">${initiated}</div></section>`;
}

function renderDocRequest(data) {
  if (!model.engineeringRequest) model.engineeringRequest = structuredClone(data.request);
  const request = model.engineeringRequest;
  const evidenceFresh = request.evidence.fresh_tests && request.evidence.fresh_read_back && request.evidence.requester_visible_outcome;
  const next = {
    draft: [{ action: "request-queue", label: "Confirm and queue synthetic request" }],
    queued: [{ action: "request-claim", label: "Claim with bounded task contract" }],
    claimed: [{ action: "request-start", label: "Start bounded prototype work" }],
    in_progress: [{ action: "request-needs-answer", label: "Record needs-answer question" }, ...(evidenceFresh ? [{ action: "request-complete", label: "Complete with fresh proof" }] : [])],
    needs_answer: [{ action: "request-resume", label: "Record answer and resume" }],
    completed: [], declined: [], failed: [], superseded: []
  }[request.state] ?? [];
  const stageIndex = ["draft", "queued", "claimed", "in_progress", "needs_answer", "completed"].indexOf(request.state);
  const actionButtons = next.map(item => `<button class="button secondary" type="button" data-action="${item.action}">${item.label}</button>`).join(" ");
  return `${journeySteps(["Review draft", "Queue", "Claim", "Work or needs answer", "Fresh verification", "Complete"], Math.max(0, stageIndex))}<div class="grid two"><section class="card"><span class="tag">${escape(data.context.label)}</span><h2>Ask Doc</h2><p>${escape(data.answer.text)}</p><p class="source-line">Source: ${escape(data.answer.source)}</p><div class="doc-message"><strong>Proposed improvement</strong><p>${escape(request.desired_outcome)}</p><ul>${request.acceptance_criteria.map(item => `<li>${escape(item)}</li>`).join("")}</ul></div>${actionButtons || "<p>No backward transition is available. A terminal request requires a new linked request for more work.</p>"}</section>${isSterile() ? sterileRefusal("Request detail", "This internal panel holds the request ID, executor, completion evidence gate, and the control that satisfies it — without it, Completed cannot be reached.") : ""}<section class="card internal-only"><span class="tag ${request.state === "completed" ? "success" : "warning"}">${escape(formatState(request.state))}</span><h2>${escape(request.id)}</h2><dl><dt>Origin</dt><dd>${escape(request.origin)}</dd><dt>Reporter</dt><dd>${escape(request.reporter)}</dd><dt>Owner</dt><dd>${escape(request.owner)}</dd><dt>Executor</dt><dd>${escape(request.executor ?? "Unclaimed")}</dd></dl><h3>Completion evidence gate</h3><ul class="detail-list"><li>Fresh tests: ${request.evidence.fresh_tests ? "Present" : "Missing"}</li><li>Fresh read-back: ${request.evidence.fresh_read_back ? "Present" : "Missing"}</li><li>Requester-visible outcome: ${request.evidence.requester_visible_outcome ? "Present" : "Missing"}</li><li>Commit/deployment: ${escape(formatState(request.evidence.commit_or_deployment))}</li></ul>${request.state === "in_progress" && !evidenceFresh ? '<button class="button ghost" type="button" data-action="request-add-evidence">Attach synthetic fresh verification evidence</button>' : ""}<p class="boundary-note">Completed is structurally unavailable until fresh tests, read-back, and requester-visible outcome exist. Lifecycle transitions are forward-only except the explicit needs-answer return to in-progress.</p></section></div>`;
}

function renderTour(data) {
  if (!model.tourSession) model.tourSession = structuredClone(data.tour);
  const tour = model.tourSession;
  const stageMap = { route_proposed: 0, confirmed: 1, in_progress: 2, paused: 3, review_ready: 4, complete: 5 };
  const stops = tour.stops.map(stop => `<article class="card stop ${stop.locked ? "locked" : ""}"><span class="route-order">${stop.order}</span><div><span class="tag ${stop.locked ? "warning" : ""}">Marker ${stop.marker} · ${escape(stop.appointment)}</span><h3>${escape(stop.name)}</h3><p>${escape(stop.facts.join(" · "))}</p><button class="button ghost" type="button" data-stop="${stop.id}">Open exact note page</button></div></article>`).join("");
  let controls = `<div class="notice"><strong>Route version ${tour.route_version} proposed</strong><p>Map markers and itinerary order match 1–3. Locked appointments remain at stops 1 and 3.</p></div><button class="button secondary" type="button" data-action="confirm-route">Confirm proposed route</button>`;
  if (tour.stage === "confirmed" && tour.offline_pack.status === "not_requested") controls = `<div class="notice"><strong>Route confirmed</strong><p>The offline pack must be downloaded and hash-verified before Tour Mode can start.</p></div><button class="button secondary" type="button" data-action="download-tour-pack">Download synthetic offline pack</button>`;
  if (tour.stage === "confirmed" && tour.offline_pack.status === "downloading") controls = `<div class="notice"><strong>Pack downloading</strong><p>Route text, selected documents, note pages, and itinerary are not yet eligible for offline use.</p></div><button class="button secondary" type="button" data-action="verify-tour-pack">Verify pack hash and completeness</button>`;
  if (tour.stage === "confirmed" && tour.offline_pack.status === "ready") controls = `<div class="notice"><strong>Verified pack ready</strong><p>Hash ${escape(tour.offline_pack.content_hash)} was checked after all required pack contents arrived.</p></div><button class="button secondary" type="button" data-action="start-tour">Start synthetic Tour Mode</button>`;
  if (tour.stage === "in_progress") controls = `<label class="field">Pencil-equivalent note<textarea id="tour-note">Synthetic handwritten-equivalent note at ${escape(tour.resume.stop_id)}.</textarea></label><p class="meta">${tour.resume.acknowledged_strokes} acknowledged stroke batch(es) and ${tour.resume.photos} photo(s) remain local.</p><button class="button ghost" type="button" data-action="capture-note">Acknowledge note stroke batch</button> <button class="button ghost" type="button" data-action="capture-photo">Add synthetic photo</button> <button class="button ghost" type="button" data-action="tour-offline">Simulate offline interruption</button> <button class="button secondary" type="button" data-action="finish-tour">Finish tour</button>`;
  if (tour.stage === "paused" && tour.sync_state === "Local") controls = `<div class="notice"><strong>Saved on this device, not yet synced</strong><p>Exact resume: ${escape(tour.resume.stop_id)} / ${escape(tour.resume.note_cursor)}. ${tour.resume.acknowledged_strokes} acknowledged stroke batch(es) and ${tour.resume.photos} photo(s) remain local.</p></div><button class="button ghost" type="button" data-action="tour-conflict">Simulate newer server route</button> <button class="button secondary" type="button" data-action="tour-resume">Resume exact stop offline</button>`;
  if (tour.stage === "paused" && tour.sync_state === "Needs review") controls = `<div class="notice error"><strong>Route conflict requires a person</strong><p>Server route version ${tour.conflict.server_route_version}; local route version ${tour.conflict.local_route_version}. Notes and photos remain preserved. No last-write-wins.</p></div><button class="button secondary" type="button" data-action="resolve-tour-conflict">Use server route and merge append-only evidence</button>`;
  if (tour.stage === "review_ready") controls = `<div class="notice"><strong>Post-tour proposals</strong><p>Every item must be independently confirmed or skipped before completion.</p></div>${tour.review_items.map(item => `<div class="candidate"><p>${escape(item.text)}</p><span class="meta">${escape(item.disposition)}</span>${item.disposition === "pending" ? `<button class="button secondary" type="button" data-tour-review="${escape(item.id)}" data-disposition="confirmed">Confirm</button> <button class="button ghost" type="button" data-tour-review="${escape(item.id)}" data-disposition="skipped">Skip</button>` : ""}</div>`).join("")}<button class="button secondary" type="button" data-action="complete-tour-review" ${tour.review_items.some(item => item.disposition === "pending") ? "disabled" : ""}>Finish reviewed tour</button>`;
  if (tour.stage === "complete") controls = `<div class="notice"><strong>Tour review complete</strong><p>Original note/photo evidence remains preserved. No business record changed outside this browser.</p><p class="meta">Exact resume: ${escape(tour.resume.stop_id)} / ${escape(tour.resume.note_cursor)}. ${tour.resume.acknowledged_strokes} acknowledged stroke batch(es) and ${tour.resume.photos} photo(s) remain local.</p></div>`;
  const mapMarkers = tour.stops.map((stop, index) => `<span class="map-marker" data-stop-id="${escape(stop.id)}" style="left:${18 + index * 31}%;top:${index % 2 ? 58 : 30}%" aria-label="Map marker ${stop.marker}: ${escape(stop.name)}">${stop.marker}</span>`).join("");
  return `${journeySteps(["Confirm route", "Download and verify pack", "Capture note/photo", "Offline exact resume/conflict", "Review proposals", "Finish"], stageMap[tour.stage])}<section class="card"><h2>${escape(tour.name)}</h2><p>Offline pack: ${escape(tour.offline_pack.status)} and ${tour.offline_pack.verified ? "verified" : "unverified"} · includes ${escape(tour.offline_pack.includes.join(", "))}.</p>${controls}</section><section style="margin-top:20px"><h2>Map/list parity</h2><p class="source-line">Synthetic map markers and the equivalent ordered itinerary share the same marker, stop ID, and order.</p><div class="synthetic-map" role="img" aria-label="Synthetic map showing the same three numbered stops as the itinerary"><span class="map-route" aria-hidden="true"></span>${mapMarkers}</div><div class="grid">${stops}</div></section>`;
}

function marketLocations(records) {
  return records.flatMap(record => record.locations.map(location => ({ record, location })));
}

function marketKindCode(kind) {
  return { prospect: "P", active_client: "C", project_in_process: "J", active_project: "A", visits: "V" }[kind] ?? "?";
}

function marketLocationVisible(record, location) {
  return location.location_role === "appointment_or_visit_stop" ? model.marketFilters.visits : model.marketFilters[record.record_type];
}

function marketAppointment(market, location) {
  return market.visit_appointments.find(item => item.map_item_id === location.map_item_id) ?? { locked: false, label: "No appointment", sort_minutes: null };
}

function renderMarketMap(data) {
  const market = data.market;
  const allLocations = marketLocations(market.records);
  const filteredRecords = market.records.filter(record =>
    (model.marketScope === "all" || record.business_scope === model.marketScope) &&
    record.planning_day_offset <= model.marketHorizonDays &&
    record.changed_days_ago <= model.marketChangedDays &&
    (model.marketFreshness === "all" || record.freshness.status === model.marketFreshness)
  );
  const visibleLocations = marketLocations(filteredRecords).filter(({ record, location }) => marketLocationVisible(record, location));
  const visibleRecords = market.records.filter(record => visibleLocations.some(item => item.record.record_id === record.record_id));
  const mappedLocations = visibleLocations.filter(item => item.location.position);
  const unmappedLocations = visibleLocations.filter(item => !item.location.position);
  const currentRecords = visibleRecords.filter(record => record.freshness.status === "fresh");
  const staleRecords = visibleRecords.filter(record => record.freshness.status === "stale");
  const unknownRecords = visibleRecords.filter(record => record.freshness.status === "unknown");
  const mixedTruth = staleRecords.length || unknownRecords.length || unmappedLocations.length;
  if (!model.marketDetailLocationId) model.marketDetailLocationId = mappedLocations[0]?.location.map_item_id ?? unmappedLocations[0]?.location.map_item_id ?? null;
  const detail = allLocations.find(item => item.location.map_item_id === model.marketDetailLocationId) ?? null;
  const selected = model.marketSelectedLocationIds.map(id => allLocations.find(item => item.location.map_item_id === id)).filter(Boolean);
  const selectedHasUnreadyEvidence = selected.some(item => ["stale", "unknown"].includes(item.location.freshness) || !item.location.position);
  const excludedSelected = selected.filter(item => ["stale", "unknown"].includes(item.location.freshness) || !item.location.position);

  const filters = [
    ["prospect", "Prospects"],
    ["active_client", "Active clients"],
    ["project_in_process", "Projects in process"],
    ["active_project", "Active projects"],
    ["visits", "Visits / appointments"]
  ].map(([kind, label]) => `<label class="market-filter"><input type="checkbox" data-market-filter="${kind}" ${model.marketFilters[kind] ? "checked" : ""}> <span class="legend-code kind-${kind}" aria-hidden="true">${marketKindCode(kind)}</span>${label}</label>`).join("");
  const planningControls = `<fieldset class="market-filterbar"><legend>Planning controls</legend><label>Scope<select data-market-control="scope"><option value="all" ${model.marketScope === "all" ? "selected" : ""}>Team Book + National Accounts</option><option value="team_book" ${model.marketScope === "team_book" ? "selected" : ""}>Team Book</option><option value="national_accounts" ${model.marketScope === "national_accounts" ? "selected" : ""}>National Accounts</option></select></label><label>Horizon<select data-market-control="horizon"><option value="0" ${model.marketHorizonDays === 0 ? "selected" : ""}>Today</option><option value="7" ${model.marketHorizonDays === 7 ? "selected" : ""}>7 days</option><option value="30" ${model.marketHorizonDays === 30 ? "selected" : ""}>30 days</option></select></label><label>Freshness<select data-market-control="freshness"><option value="all">All truth states</option><option value="fresh" ${model.marketFreshness === "fresh" ? "selected" : ""}>Fresh only</option><option value="stale" ${model.marketFreshness === "stale" ? "selected" : ""}>Stale only</option><option value="unknown" ${model.marketFreshness === "unknown" ? "selected" : ""}>Unknown only</option></select></label><label>Changed since<select data-market-control="changed"><option value="0" ${model.marketChangedDays === 0 ? "selected" : ""}>Today</option><option value="7" ${model.marketChangedDays === 7 ? "selected" : ""}>7 days</option><option value="30" ${model.marketChangedDays === 30 ? "selected" : ""}>30 days</option></select></label></fieldset>`;

  const presets = [["all", "All layers"], ["prospect", "Prospects"], ["active_client", "Clients"], ["project_in_process", "Projects in process"], ["active_project", "Active projects"], ["visits", "Visits"]]
    .map(([preset, label]) => `<button class="button ghost market-preset" type="button" data-market-preset="${preset}">${label}</button>`).join("");
  const visibleMapItemIds = new Set(visibleLocations.map(item => item.location.map_item_id));
  const crossLayerReady = ["prospect", "active_client", "project_in_process", "active_project", "visits"].every(layer => model.marketFilters[layer]);
  const visibleOpportunities = market.travel_opportunities.filter(opportunity => opportunity.source_rows.every(row => visibleMapItemIds.has(row.location_id)));
  const travelInsight = crossLayerReady && visibleOpportunities.length ? visibleOpportunities.map(opportunity => `<section class="card travel-opportunity"><div><p class="eyebrow">Travel opportunity · explainable cross-layer match</p><h2>${escape(opportunity.area)}</h2><p><strong>${escape(opportunity.label)}</strong></p><p>${escape(opportunity.rationale)}</p><p class="source-line">${escape(opportunity.score_note)}</p></div><div><h3>Exact source rows</h3><ul>${opportunity.source_rows.map(row => `<li><button class="location-detail" type="button" data-market-list-location="${escape(row.location_id)}"><strong>${escape(row.layer)}</strong><span>${escape(row.label)}</span><small>${escape(row.source)} · ${escape(row.record_id)} · ${escape(row.location_id)}</small></button></li>`).join("")}</ul></div></section>`).join("") : '<p class="source-line">Travel opportunity unavailable: every cited source row must remain visible under the current layer, scope, horizon, freshness, and Changed Since filters.</p>';

  const markers = mappedLocations.map(({ record, location }) => {
    const selectedClass = model.marketSelectedLocationIds.includes(location.map_item_id) ? " selected" : "";
    const detailClass = model.marketDetailLocationId === location.map_item_id ? " active-detail" : "";
    const roleLabel = formatState(location.location_role);
    return `<button class="market-marker kind-${location.location_role === "appointment_or_visit_stop" ? "visits" : record.record_type}${selectedClass}${detailClass}" type="button" data-market-location="${escape(location.map_item_id)}" style="left:${location.position.x}%;top:${location.position.y}%" aria-current="${model.marketDetailLocationId === location.map_item_id ? "location" : "false"}" aria-label="${escape(record.record_type_label)}: ${escape(record.display_label)}. ${escape(roleLabel)} at ${escape(location.display_label)}. Open details."><span aria-hidden="true">${location.location_role === "appointment_or_visit_stop" ? "V" : marketKindCode(record.record_type)}</span><small aria-hidden="true">${escape(roleLabel.split(" ")[0])}</small></button>`;
  }).join("");

  const list = visibleRecords.map(record => {
    const locations = record.locations.filter(location => marketLocationVisible(record, location)).map(location => {
      const selectedStop = model.marketSelectedLocationIds.includes(location.map_item_id);
      const detailSelected = model.marketDetailLocationId === location.map_item_id;
      return `<li class="market-location-row ${detailSelected ? "active-detail" : ""}" data-market-row="${escape(location.map_item_id)}" tabindex="-1"><button class="location-detail" type="button" data-market-list-location="${escape(location.map_item_id)}" aria-current="${detailSelected ? "location" : "false"}"><strong>${escape(formatState(location.location_role))}</strong><span>${escape(location.display_label)}</span><small>${location.position ? `Mapped · ${escape(formatState(location.location_precision))}` : "Unmapped · needs location"} · ${escape(formatState(location.freshness))}${location.observed_at ? ` at ${escape(location.observed_at)}` : " · as-of Unknown"}</small><small>Source: ${escape(location.location_source.type)} · ${escape(location.location_source.id)} · v${escape(location.location_source.version)} · projection ${location.location_projection_version === null ? "Unknown" : `v${escape(location.location_projection_version)}`} · ${escape(location.redaction_profile)}</small><small>Trip ${location.trip_eligible ? "eligible" : "ineligible"}: ${escape(location.trip_eligibility_reason)}</small></button><a class="button ghost" href="${escape(location.deep_link)}">Open source</a><button class="button ${selectedStop ? "secondary" : "ghost"}" type="button" data-market-stop="${escape(location.map_item_id)}" aria-pressed="${selectedStop}" ${selectedStop || location.trip_eligible ? "" : 'aria-describedby="trip-ineligible-note"'}>${selectedStop ? "Remove visit stop" : "Add visit stop"}</button></li>`;
    }).join("");
    return `<article class="card market-record" data-kind="${escape(record.record_type)}"><header><span class="tag kind-label">${marketKindCode(record.record_type)} · ${escape(record.record_type_label)}</span><span class="freshness" data-status="${escape(record.freshness.status)}">${escape(formatState(record.freshness.status))}</span></header><h3>${escape(record.display_label)}</h3><p>${escape(record.summary)}</p><p class="meta">${escape(record.market)} · Owner: ${escape(record.owner)}</p><ul class="market-locations">${locations}</ul><button class="button ghost" type="button" data-route="${escape(record.source.surface)}">${escape(record.source.label)}</button></article>`;
  }).join("");

  const detailAppointment = detail ? marketAppointment(market, detail.location) : null;
  const detailPanel = detail ? `<aside class="card market-detail" aria-live="polite"><p class="eyebrow">Selected map detail</p><span class="tag">${marketKindCode(detail.record.record_type)} · ${escape(detail.record.record_type_label)}</span><h2>${escape(detail.record.display_label)}</h2><dl><dt>Location role</dt><dd>${escape(formatState(detail.location.location_role))}</dd><dt>Location</dt><dd>${escape(detail.location.display_label)}</dd><dt>Appointment</dt><dd>${escape(detailAppointment.label)}${detailAppointment.locked ? " · cannot be moved by planner" : ""}</dd><dt>Record identity</dt><dd>${escape(detail.location.record_type)} · ${escape(detail.location.record_id)} · v${escape(detail.location.record_version)}</dd><dt>Location source</dt><dd>${escape(detail.location.location_source.type)} · ${escape(detail.location.location_source.id)} · v${escape(detail.location.location_source.version)}</dd><dt>Projection</dt><dd>${detail.location.location_projection_version === null ? "Unknown" : `v${escape(detail.location.location_projection_version)}`} · ${escape(formatState(detail.location.location_precision))} · ${escape(detail.location.redaction_profile)}</dd><dt>Location freshness</dt><dd>${escape(formatState(detail.location.freshness))}${detail.location.observed_at ? ` · ${escape(detail.location.observed_at)}` : " · as-of Unknown"}</dd></dl><p><strong>Next:</strong> ${escape(detail.record.next_step)}</p><a class="button ghost" href="${escape(detail.location.deep_link)}">Open exact owning record</a></aside>` : `<aside class="card market-detail"><h2>No visible location</h2><p>Change the filters or resolve an unmapped record.</p></aside>`;

  let planner = `<p>Select at least two eligible location markers or list rows to prepare one day-trip proposal.</p>`;
  if (model.marketPlanError) planner += `<div class="notice error" role="alert"><strong>Visit stop not added</strong><p>${escape(model.marketPlanError)}</p></div>`;
  if (selected.length) planner = `<ol class="selected-stops">${selected.map(({ record, location }, index) => `<li><span class="route-order">${index + 1}</span><div><strong>${escape(record.display_label)}</strong><span>${escape(formatState(location.location_role))} · ${escape(location.display_label)}</span><small>${escape(marketAppointment(market, location).label)} · ${escape(formatState(location.freshness))}</small></div></li>`).join("")}</ol><p class="source-line">Selection order is not a routing claim. Locked appointments remain fixed anchors.</p>`;
  const canPrepare = selected.length >= 2 && !selectedHasUnreadyEvidence && selected.every(item => item.location.trip_eligible);
  if (!model.marketTrip) planner += `${selectedHasUnreadyEvidence ? `<div class="notice error" role="alert"><strong>Planning refused</strong><p>Resolve every selected stale, Unknown, or unmapped location before preparing travel.</p><ul>${excludedSelected.map(item => `<li>${escape(item.record.display_label)} · ${escape(item.location.display_label)} · omitted because ${!item.location.position ? "location is unmapped" : `location is ${item.location.freshness}`}</li>`).join("")}</ul></div>` : ""}<div class="trip-inputs"><label>Start<input id="trip-start" value="${escape(model.marketTripInputs.start)}"></label><label>End<input id="trip-end" value="${escape(model.marketTripInputs.end)}"></label><label>Visit duration (minutes)<input id="trip-dwell" type="number" min="15" step="5" value="${escape(model.marketTripInputs.dwell_minutes)}"></label><label>Travel buffer (minutes)<input id="trip-buffer" type="number" min="0" step="5" value="${escape(model.marketTripInputs.buffer_minutes)}"></label></div><button class="button secondary" type="button" data-action="prepare-market-trip" ${canPrepare ? "" : "disabled"}>Prepare multi-stop day-trip proposal</button>`;
  if (model.marketTrip) {
    const locked = model.marketTrip.stops.filter(item => marketAppointment(market, item.location).locked).sort((a, b) => marketAppointment(market, a.location).sort_minutes - marketAppointment(market, b.location).sort_minutes);
    const flexible = model.marketTrip.stops.filter(item => !marketAppointment(market, item.location).locked);
    planner = `<div class="notice"><strong>${model.marketTripConfirmed ? "Trip proposal confirmed" : "Trip proposal ready for human review"}</strong><p>This is a proposal, not a live or optimal route. It uses no live traffic or verified travel-time service.</p></div><dl><dt>Start</dt><dd>${escape(model.marketTrip.inputs.start)}</dd><dt>End</dt><dd>${escape(model.marketTrip.inputs.end)}</dd><dt>Default visit duration</dt><dd>${escape(model.marketTrip.inputs.dwell_minutes)} minutes</dd><dt>Travel buffer</dt><dd>${escape(model.marketTrip.inputs.buffer_minutes)} minutes</dd><dt>Route evidence</dt><dd>Provider: ${escape(model.marketTrip.provider)} · method: ${escape(model.marketTrip.method)} · inputs as of ${escape(model.marketTrip.input_as_of)}</dd></dl><h3>Locked schedule anchors</h3>${locked.length ? `<ol class="trip-schedule">${locked.map(item => `<li><strong>${escape(marketAppointment(market, item.location).label)}</strong> · ${escape(item.record.display_label)} · ${escape(item.location.display_label)}</li>`).join("")}</ol>` : "<p>No locked appointments selected.</p>"}<h3>Flexible visit stops</h3>${flexible.length ? `<ul class="detail-list">${flexible.map(item => `<li>${escape(item.record.display_label)} · ${escape(item.location.display_label)} · sequence pending verified travel estimates</li>`).join("")}</ul>` : "<p>No flexible stops selected.</p>"}<h3>Omitted or infeasible stops</h3><ul>${model.marketTrip.skipped_stops.map(item => `<li>${escape(item.map_item_id)} · ${escape(item.reason)}</li>`).join("")}</ul><p class="source-line">Alternative route comparison, route version, and planning fingerprint are future construction gates. This synthetic prototype does not claim or fabricate them.</p><p class="boundary-note">The proposal preserves locked appointments. A routing provider or human must verify drive time, opening hours, and feasibility before departure.</p>${model.marketTripConfirmed ? '<button class="button secondary" type="button" data-route="tour">Open separate Tour Mode for downstream execution</button>' : '<button class="button secondary" type="button" data-action="confirm-market-trip">Confirm this planning proposal</button> <button class="button ghost" type="button" data-action="edit-market-trip">Edit selected stops</button>'}`;
  }

  return `${journeySteps(["See the whole market", "Filter and inspect", "Select visit stops", "Review proposal", "Confirm for Tour Mode"], model.marketTripConfirmed ? 4 : model.marketTrip ? 3 : selected.length ? 2 : 0)}${document.body.classList.contains("sterile") ? '<div class="notice error" role="alert"><strong>Whole-market view refused in Sterile mode</strong><p>This internal planning surface may expose prospect, client, appointment, and travel-pattern context. Exit Sterile mode to use it.</p></div>' : `<section class="market-summary card"><div><p class="eyebrow">Supported market · ${mixedTruth ? "Mixed / Partial" : "Current"}</p><h2>${escape(market.name)}</h2><p>${escape(market.coverage_note)}</p><p><strong>Planning horizon:</strong> ${escape(market.planning_horizon)}</p><p class="source-line">Source: ${escape(market.source)} · projection v${escape(market.source_version)}</p></div><div class="market-counts" aria-label="Visible map coverage"><span><strong>${currentRecords.length}</strong> current records</span><span><strong>${staleRecords.length}</strong> stale records</span><span><strong>${unknownRecords.length}</strong> Unknown records</span><span><strong>${mappedLocations.length}</strong> mapped markers</span><span class="unmapped-count"><strong>${unmappedLocations.length}</strong> unlocated / needs location</span></div></section><div class="market-presets" aria-label="Layer presets">${presets}</div><fieldset class="market-filterbar"><legend>Business record layers</legend>${filters}</fieldset>${planningControls}${travelInsight}<div class="market-visual-grid"><section><div class="market-map" aria-label="Interactive synthetic supported-market map. Use Tab to reach every marker."><span class="market-water" aria-hidden="true"></span><span class="market-corridor corridor-one" aria-hidden="true"></span><span class="market-corridor corridor-two" aria-hidden="true"></span><span class="market-label label-mobile" aria-hidden="true">Mobile</span><span class="market-label label-pensacola" aria-hidden="true">Pensacola</span><span class="market-label label-dothan" aria-hidden="true">Dothan</span><span class="market-label label-panama" aria-hidden="true">Panama City</span>${markers}</div><div class="map-legend" aria-label="Map symbol legend"><span><b class="legend-code kind-prospect">P</b> Prospect</span><span><b class="legend-code kind-active_client">C</b> Active client</span><span><b class="legend-code kind-project_in_process">J</b> Project in process</span><span><b class="legend-code kind-active_project">A</b> Active project</span><span><b class="legend-code kind-visits">V</b> Visit / appointment</span><span><b class="legend-lock">▣</b> Locked appointment appears in details</span></div><p class="source-line">Map/list parity: every mapped marker appears in the list with the same map item ID, role, source, and business record. Unmapped locations remain counted and visible below.</p><p class="source-line">Phase 0 limitation: dense-point clustering and pan/zoom are not simulated. Every synthetic marker remains an individual keyboard-focusable 48-pixel control and its synchronized list row remains available.</p></section>${detailPanel}</div><section class="market-workspace"><div><h2>Visible records and sourced locations</h2><p id="trip-ineligible-note" class="source-line">Trip-ineligible locations stay visible for market context; their reason appears on the location row.</p><div class="market-record-list">${list || '<div class="notice">No business record layers selected.</div>'}</div></div><aside class="card trip-planner"><p class="eyebrow">Planning only</p><h2>Day-trip proposal</h2>${planner}</aside></section><p class="boundary-note">Tour Mode is separate. It executes only a confirmed trip with its own offline pack, route confirmation, notes, and review gates.</p>`}`;
}

function renderLeadBoard(data) {
  return `<div class="grid">${data.items.map(item => `<article class="card"><span class="tag">${escape(item.state)}</span><h2>${escape(item.name)}</h2><p>${escape(item.why)}</p><p class="meta">Owner: ${escape(item.owner)} · Next: ${escape(item.next_action)}</p><footer><button class="button ghost" type="button">Review evidence</button><button class="button" type="button">Log synthetic outcome</button></footer></article>`).join("")}</div><p class="boundary-note">Named lead outreach reminders appear only here. Conversion requires duplicate review and a human confirmation.</p>`;
}

function renderMarketing(data) {
  return `<div class="grid">${data.items.map(item => `<article class="card"><span class="tag warning">${escape(item.state)}</span><h2>${escape(item.title)}</h2><p>Audience: ${escape(item.audience)} · ${escape(item.channel)}</p><p>Source: ${escape(item.source)}</p><p class="meta">Owner: ${escape(item.owner)}</p><footer><button class="button ghost" type="button">Edit draft</button><button class="button" type="button">Record review decision</button></footer></article>`).join("")}</div><p class="boundary-note">Publishing, media spend, audience changes, and outbound action are structurally unavailable.</p>`;
}

function renderNotifications(data) {
  return `<div class="grid two">${data.items.map(item => `<article class="card"><span class="tag ${item.type === "Urgent" ? "warning" : ""}">${escape(item.type)}</span><h2>${escape(item.title)}</h2><p>${escape(item.why)}</p><footer><button class="button ghost" type="button" data-route="${escape(item.destination)}">Open exact source</button></footer></article>`).join("")}</div><p class="source-line">Badges count unresolved human decisions, not raw activity. Named lead reminders are excluded.</p>`;
}

function renderMore(data) {
  const destinations = data.destinations.filter(item => !item.baselines).map(item => `<button class="card" type="button" data-route="${item.route}"><h2>${item.title}</h2><p>${item.text}</p></button>`).join("");
  const cutover = data.destinations.find(item => item.baselines);
  return `<div class="grid">${destinations}</div>${cutover ? `<section class="card" style="margin-top:20px"><p class="eyebrow">AI-session displacement acceptance</p><h2>${escape(cutover.title)}</h2><p class="lede"><strong>${escape(cutover.text)}</strong></p><p>No prior routine surface retires until Joe completes the uncoached journey. Dell may independently validate the same journey, but his participation never gates system change or retirement. Heavy AI remains appropriate for exploration, genuinely complex investigation, and new construction.</p><div class="grid">${cutover.baselines.map(item => `<article><span class="tag warning">${escape(item.status)}</span><h3>${escape(item.id)}</h3><p>${escape(item.measure)}</p><p><strong>Bound product action:</strong> ${escape(item.bound_product_action)}</p><p><strong>Post-launch comparison:</strong> ${escape(item.post_launch_comparison)}</p><p class="meta">Next: ${escape(item.next_action)}</p></article>`).join("")}</div></section>` : ""}<section class="card" style="margin-top:20px"><h2>Also in More</h2><p>${escape(data.registry_only.join(" · "))}</p><p class="source-line">These registry destinations are represented without inventing operational data or production writers.</p></section>`;
}

function normalContent(surface, data) {
  if (surface === "command-center") return renderCommandCenter(data);
  if (surface === "deal-room") return renderDealRoom(data);
  if (surface === "call-review") return renderCall(data);
  if (surface === "doc-request") return renderDocRequest(data);
  if (surface === "tour") return renderTour(data);
  if (surface === "market-map") return renderMarketMap(data);
  if (surface === "lead-board") return renderLeadBoard(data);
  if (surface === "marketing") return renderMarketing(data);
  if (surface === "notifications") return renderNotifications(data);
  if (surface === "more") return renderMore(data);
  return "";
}

async function render() {
  renderNav();
  const config = surfaces[model.route];
  document.querySelector("#doc-context").textContent = model.docContextIncluded ? config.label : "No page context";
  document.querySelector("#doc-context-result").textContent = model.docContextResult;
  main.innerHTML = `<div class="notice" role="status">Loading local synthetic fixture…</div>`;
  try {
    let fixture = model.fixtureCache.get(config.fixture);
    if (!fixture) {
      fixture = await loadFixture(config.fixture);
      model.fixtureCache.set(config.fixture, fixture);
    }
    const data = fixture.states[model.state];
    main.innerHTML = `${pageHeading(config.label, config.description, data, fixture.requirement_ids)}${isActionableState(model.state) ? normalContent(model.route, data) : genericState(data)}`;
    bindActions();
  } catch (error) {
    main.innerHTML = `<div class="notice error" role="alert"><strong>Fixture unavailable</strong><p>${escape(error.message)}</p><button class="button ghost" type="button" data-action="retry-read">Retry local fixture</button></div>`;
    bindActions();
  }
}

function navigate(route) {
  if (!surfaces[route]) return;
  model.route = route;
  model.state = "normal";
  model.docContextIncluded = true;
  model.docContextResult = `Page context changed to ${surfaces[route].label}. Remove it at any time.`;
  statePicker.value = model.state;
  if (location.hash.slice(1) !== route) location.hash = route;
  else render();
}

function setCallMode(active) {
  callControl.setAttribute("aria-pressed", String(active));
  callControl.querySelector(".call-label").textContent = active ? "Recording" : "Call Mode";
  if (!active) {
    clearInterval(model.timer);
    model.timer = null;
    callControl.querySelector(".call-timer").textContent = "";
    return;
  }
  model.seconds = 0;
  callControl.querySelector(".call-timer").textContent = "0:00";
  model.timer = setInterval(() => {
    model.seconds += 1;
    const minutes = Math.floor(model.seconds / 60);
    const seconds = String(model.seconds % 60).padStart(2, "0");
    callControl.querySelector(".call-timer").textContent = `${minutes}:${seconds}`;
  }, 1000);
}

function handleAction(action, element) {
  if (action === "retry-read") {
    model.fixtureCache.delete(surfaces[model.route].fixture);
    model.state = "normal";
    statePicker.value = "normal";
  } else if (action === "prepare-park") {
    model.parkingReason = document.querySelector("#parking-reason")?.value ?? "";
    model.parkingContext = document.querySelector("#parking-context")?.value.trim() ?? "";
    model.dealError = model.parkingReason === "Other" && !model.parkingContext ? "Context is required when the parking reason is Other." : "";
    model.parkingPrepared = !model.dealError;
  } else if (action === "cancel-park") {
    model.parkingPrepared = false;
    model.dealError = "";
  }
  else if (action === "confirm-park") {
    model.recordState = "Parked";
    model.recordHistory.push(`Parked in prototype · ${model.parkingReason} · ${model.parkingContext} · attributed to Joe`);
    model.activePressure = Object.fromEntries(Object.keys(model.activePressure).map(key => [key, false]));
    model.parkingPrepared = false;
  } else if (action === "restore-record") {
    model.recordState = "Active Work";
    model.recordHistory.push("Restored to Active Work in prototype · attributed to Joe");
    model.activePressure = Object.fromEntries(Object.keys(model.activePressure).map(key => [key, true]));
  } else if (action === "begin-recording") {
    if (!document.querySelector("#consent-confirmed")?.checked) {
      document.querySelector("#consent-confirmed")?.focus();
      document.querySelector("#consent-confirmed")?.setAttribute("aria-invalid", "true");
      return;
    }
    model.callSession.consent_confirmed = true;
    model.callSession.capture_stage = "recording";
    model.callSession.summary_status = "Recording";
    setCallMode(true);
  } else if (action === "stop-recording") {
    model.callSession.capture_stage = "review";
    model.callSession.summary_status = "Review ready";
    setCallMode(false);
  } else if (action === "complete-call") {
    if (callCandidates(model.callSession).some(candidate => candidate.disposition === "pending")) return;
    model.callSession.capture_stage = "complete";
    model.callSession.summary_status = "Complete";
    model.callSession.retention.all_candidates_dispositioned = true;
    model.callSession.retention.aggregate_summary_confirmed = true;
    const retention = model.callSession.retention;
    retention.purge_eligible = retention.all_candidates_dispositioned && retention.aggregate_summary_confirmed && retention.legal_hold === false && retention.policy_resolved === true && retention.recovery_window_elapsed === true;
    retention.reason = retention.purge_eligible ? "Every mechanical gate passed, including an explicit no-legal-hold decision." : "Structured review is complete, but purge remains blocked until legal hold is explicitly false, policy is resolved, and the recovery window has elapsed.";
  } else if (action === "request-queue" && model.engineeringRequest.state === "draft") model.engineeringRequest.state = "queued";
  else if (action === "request-claim" && model.engineeringRequest.state === "queued") {
    model.engineeringRequest.state = "claimed";
    model.engineeringRequest.executor = "Synthetic bounded Codex task";
  } else if (action === "request-start" && model.engineeringRequest.state === "claimed") model.engineeringRequest.state = "in_progress";
  else if (action === "request-needs-answer" && model.engineeringRequest.state === "in_progress") model.engineeringRequest.state = "needs_answer";
  else if (action === "request-resume" && model.engineeringRequest.state === "needs_answer") model.engineeringRequest.state = "in_progress";
  else if (action === "request-add-evidence" && model.engineeringRequest.state === "in_progress") {
    model.engineeringRequest.evidence.fresh_tests = true;
    model.engineeringRequest.evidence.fresh_read_back = true;
    model.engineeringRequest.evidence.requester_visible_outcome = true;
  } else if (action === "request-complete" && model.engineeringRequest.state === "in_progress") {
    const evidence = model.engineeringRequest.evidence;
    if (!(evidence.fresh_tests && evidence.fresh_read_back && evidence.requester_visible_outcome)) return;
    model.engineeringRequest.state = "completed";
  } else if (action === "confirm-route" && model.tourSession.stage === "route_proposed") {
    model.tourSession.route_confirmed = true;
    model.tourSession.stage = "confirmed";
  } else if (action === "download-tour-pack" && model.tourSession.stage === "confirmed" && model.tourSession.offline_pack.status === "not_requested") {
    model.tourSession.offline_pack.status = "downloading";
    model.tourSession.offline_pack.verified = false;
  } else if (action === "verify-tour-pack" && model.tourSession.stage === "confirmed" && model.tourSession.offline_pack.status === "downloading") {
    model.tourSession.offline_pack.status = "ready";
    model.tourSession.offline_pack.verified = true;
  } else if (action === "start-tour" && model.tourSession.stage === "confirmed" && model.tourSession.offline_pack.status === "ready" && model.tourSession.offline_pack.verified) model.tourSession.stage = "in_progress";
  else if (action === "capture-note" && model.tourSession.stage === "in_progress") model.tourSession.resume.acknowledged_strokes += 1;
  else if (action === "capture-photo" && model.tourSession.stage === "in_progress") model.tourSession.resume.photos += 1;
  else if (action === "tour-offline" && model.tourSession.stage === "in_progress") {
    model.tourSession.stage = "paused";
    model.tourSession.sync_state = "Local";
  } else if (action === "tour-conflict" && model.tourSession.stage === "paused" && model.tourSession.sync_state === "Local") model.tourSession.sync_state = "Needs review";
  else if (action === "resolve-tour-conflict" && model.tourSession.stage === "paused" && model.tourSession.sync_state === "Needs review") {
    model.tourSession.route_version = model.tourSession.conflict.server_route_version;
    model.tourSession.conflict.resolution = "server_route_with_append_only_local_evidence";
    model.tourSession.sync_state = "Local";
  } else if (action === "tour-resume" && model.tourSession.stage === "paused" && model.tourSession.sync_state === "Local") model.tourSession.stage = "in_progress";
  else if (action === "finish-tour" && ["in_progress", "paused"].includes(model.tourSession.stage)) model.tourSession.stage = "review_ready";
  else if (action === "complete-tour-review" && model.tourSession.stage === "review_ready" && model.tourSession.review_items.every(item => item.disposition !== "pending")) model.tourSession.stage = "complete";
  else if (action === "prepare-market-trip") {
    const fixture = model.fixtureCache.get("market-map");
    const allLocations = marketLocations(fixture.states.normal.market.records);
    const stops = model.marketSelectedLocationIds.map(id => allLocations.find(item => item.location.map_item_id === id)).filter(Boolean);
    const invalid = stops.some(item => !item.location.trip_eligible || ["stale", "unknown"].includes(item.location.freshness) || !item.location.position);
    if (stops.length >= 2 && !invalid) {
      model.marketTripInputs = {
        start: document.querySelector("#trip-start")?.value.trim() || "Unknown start",
        end: document.querySelector("#trip-end")?.value.trim() || "Unknown end",
        dwell_minutes: Number(document.querySelector("#trip-dwell")?.value) || 45,
        buffer_minutes: Number(document.querySelector("#trip-buffer")?.value) || 20
      };
      model.marketTrip = {
        stops,
        inputs: structuredClone(model.marketTripInputs),
        provider: fixture.states.normal.market.route_evidence.provider,
        method: fixture.states.normal.market.route_evidence.method,
        input_as_of: fixture.states.normal.market.route_evidence.input_as_of,
        live_traffic: false,
        optimality_claim: false,
        skipped_stops: fixture.states.normal.market.route_evidence.skipped_stops
      };
      model.marketTripConfirmed = false;
      model.marketPlanError = "";
    }
  } else if (action === "confirm-market-trip" && model.marketTrip) model.marketTripConfirmed = true;
  else if (action === "edit-market-trip") {
    model.marketTrip = null;
    model.marketTripConfirmed = false;
  }
  render();
}

function bindActions() {
  main.querySelectorAll("[data-route]").forEach(element => element.addEventListener("click", () => navigate(element.dataset.route)));
  main.querySelectorAll("[data-action]").forEach(element => element.addEventListener("click", () => handleAction(element.dataset.action, element)));
  main.querySelectorAll("[data-candidate]").forEach(element => element.addEventListener("click", () => {
    const candidate = callCandidates(model.callSession).find(item => item.id === element.dataset.candidate);
    if (candidate && model.callSession.capture_stage === "review") candidate.disposition = element.dataset.disposition;
    render();
  }));
  main.querySelectorAll("[data-stop]").forEach(element => element.addEventListener("click", () => {
    model.tourSession.resume.stop_id = element.dataset.stop;
    model.tourSession.resume.note_cursor = `note-${element.dataset.stop}`;
    element.textContent = "Exact note page selected";
  }));
  main.querySelectorAll("[data-tour-review]").forEach(element => element.addEventListener("click", () => {
    const reviewItem = model.tourSession.review_items.find(item => item.id === element.dataset.tourReview);
    if (reviewItem && model.tourSession.stage === "review_ready") reviewItem.disposition = element.dataset.disposition;
    render();
  }));
  main.querySelectorAll("[data-market-filter]").forEach(element => element.addEventListener("change", () => {
    model.marketFilters[element.dataset.marketFilter] = element.checked;
    model.marketSelectedLocationIds = [];
    model.marketDetailLocationId = null;
    model.marketTrip = null;
    model.marketTripConfirmed = false;
    render();
  }));
  main.querySelectorAll("[data-market-control]").forEach(element => element.addEventListener("change", () => {
    const control = element.dataset.marketControl;
    if (control === "scope") model.marketScope = element.value;
    if (control === "horizon") model.marketHorizonDays = Number(element.value);
    if (control === "freshness") model.marketFreshness = element.value;
    if (control === "changed") model.marketChangedDays = Number(element.value);
    model.marketSelectedLocationIds = [];
    model.marketDetailLocationId = null;
    model.marketTrip = null;
    model.marketTripConfirmed = false;
    render();
  }));
  main.querySelectorAll("[data-market-preset]").forEach(element => element.addEventListener("click", () => {
    const preset = element.dataset.marketPreset;
    for (const key of Object.keys(model.marketFilters)) model.marketFilters[key] = preset === "all" || key === preset;
    model.marketSelectedLocationIds = [];
    model.marketDetailLocationId = null;
    model.marketTrip = null;
    model.marketTripConfirmed = false;
    render();
  }));
  main.querySelectorAll("[data-market-location]").forEach(element => element.addEventListener("click", () => {
    model.marketDetailLocationId = element.dataset.marketLocation;
    render().then(() => main.querySelector(`[data-market-row="${CSS.escape(model.marketDetailLocationId)}"]`)?.focus());
  }));
  main.querySelectorAll("[data-market-list-location]").forEach(element => element.addEventListener("click", () => {
    model.marketDetailLocationId = element.dataset.marketListLocation;
    render().then(() => main.querySelector(`[data-market-location="${CSS.escape(model.marketDetailLocationId)}"]`)?.focus());
  }));
  main.querySelectorAll("[data-market-stop]").forEach(element => element.addEventListener("click", () => {
    const id = element.dataset.marketStop;
    const fixture = model.fixtureCache.get("market-map");
    const candidate = marketLocations(fixture.states.normal.market.records).find(item => item.location.map_item_id === id);
    const alreadySelected = model.marketSelectedLocationIds.includes(id);
    if (!alreadySelected && (!candidate?.location.trip_eligible || !candidate.location.position || ["stale", "unknown"].includes(candidate.location.freshness))) {
      model.marketPlanError = candidate?.location.trip_eligibility_reason ?? "This location is not eligible for a visit stop.";
    } else {
      model.marketSelectedLocationIds = alreadySelected
        ? model.marketSelectedLocationIds.filter(selectedId => selectedId !== id)
        : [...model.marketSelectedLocationIds, id];
      model.marketPlanError = "";
    }
    model.marketDetailLocationId = id;
    model.marketTrip = null;
    model.marketTripConfirmed = false;
    render();
  }));
}

function toggleDoc(open) {
  model.docOpen = open;
  docDrawer.hidden = !open;
  docLauncher.setAttribute("aria-expanded", String(open));
  if (open) document.querySelector("#doc-close").focus();
  else docLauncher.focus();
}

function initialize() {
  statePicker.innerHTML = authoredStates.map(state => `<option value="${state}">${formatState(state)}</option>`).join("");
  statePicker.addEventListener("change", event => {
    model.state = event.target.value;
    render();
  });
  window.addEventListener("hashchange", () => {
    const route = location.hash.slice(1);
    model.route = surfaces[route] ? route : "command-center";
    model.state = "normal";
    statePicker.value = "normal";
    render();
  });
  callControl.addEventListener("click", () => {
    if (model.callSession?.capture_stage === "recording") {
      navigate("call-review");
      return;
    }
    navigate("call-review");
    setTimeout(() => document.querySelector("#consent-confirmed")?.focus(), 0);
  });
  docLauncher.addEventListener("click", () => toggleDoc(!model.docOpen));
  document.querySelector("#doc-close").addEventListener("click", () => toggleDoc(false));
  document.querySelector("[data-doc-route]").addEventListener("click", event => {
    toggleDoc(false);
    navigate(event.currentTarget.dataset.docRoute);
  });
  document.querySelector("#doc-remove-context").addEventListener("click", () => {
    model.docContextIncluded = false;
    model.docContextResult = "Page context removed. Doc will not use or name this page until you explicitly re-add it.";
    document.querySelector("#doc-context").textContent = "No page context";
    document.querySelector("#doc-context-result").textContent = model.docContextResult;
  });
  document.querySelector("#doc-explain-page").addEventListener("click", () => {
    model.docContextResult = model.docContextIncluded
      ? `Explaining only the visible ${surfaces[model.route].label} synthetic fixture, with its source and freshness. No other record context was added.`
      : "Refused: page context was removed. Re-open Doc or explicitly re-add context before asking for a page-specific explanation.";
    document.querySelector("#doc-context-result").textContent = model.docContextResult;
  });
  document.querySelector("#notification-launcher").addEventListener("click", () => navigate("notifications"));
  document.querySelector("#color-assist").addEventListener("click", event => {
    const active = document.body.classList.toggle("color-assist");
    event.currentTarget.setAttribute("aria-pressed", String(active));
  });
  document.querySelector("#sterile-mode").addEventListener("click", event => {
    const active = document.body.classList.toggle("sterile");
    event.currentTarget.setAttribute("aria-pressed", String(active));
    render();
  });
  model.route = surfaces[location.hash.slice(1)] ? location.hash.slice(1) : "command-center";
  render();
}

initialize();
