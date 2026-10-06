import { escapeHtml as esc } from "./esc.js";
const HUMAN_REF = /^WR-[0-9]{4,12}$/;


const humanize = (value) => String(value || "").replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());

export function validateHumanRef(value) {
  const normalized = String(value || "").trim().toUpperCase();
  if (!HUMAN_REF.test(normalized)) throw new Error("Enter a valid Work Request reference such as WR-000123.");
  return normalized;
}

export function actionForCard(card) {
  if (!card || card.source?.freshness !== "current" || !["captured", "triaged", "ready"].includes(card.state)) return null;
  if (card.state === "captured") return { kind: "triage", label: "Confirm classification" };
  if (card.state === "triaged" && !card.plan) return { kind: "prepare-plan", label: "Prepare bounded plan" };
  if (card.state === "triaged") return { kind: "accept-plan", label: "Accept this plan" };
  if (card.pending_outcome_feedback) return { kind: "accept-outcome", label: "Accept this outcome record" };
  return { kind: "record-outcome", label: "Record what happened" };
}

export function lifecycleForCard(card) {
  let position = card?.state === "captured" ? 1 : card?.state === "triaged" ? 2 : card?.state === "ready" ? 4 : 0;
  if (card?.state === "triaged" && card?.plan) position = 3;
  if (card?.state === "ready" && card?.pending_outcome_feedback) position = 5;
  if (card?.state === "ready" && card?.outcome_feedback) position = 7;
  return ["Source concern", "Captured", "Human triage", "Bounded plan", "Plan accepted", "Outcome proposed", "Outcome accepted"]
    .map((label, index) => ({ label, status: index < position ? "recorded" : index === position ? "current" : "upcoming" }));
}

function renderLifecycle(card) {
  return `<ol class="system-work-flow" aria-label="System work lifecycle">${lifecycleForCard(card).map((step) =>
    `<li class="flow-${step.status}"><span aria-hidden="true"></span><strong>${esc(step.label)}</strong><small>${step.status}</small></li>`).join("")}</ol>`;
}

function renderPlan(plan) {
  if (!plan) return "";
  const caps = plan.caps || {};
  return `<section class="system-work-evidence"><p class="eyebrow">Bounded plan</p>
    <h2>${esc(plan.runbook_label || plan.runbook_ref || "Pre-authored routine")}</h2>
    <p>${esc(plan.scope_summary || "The server prepared the approved bounded routine.")}</p>
    <dl><div><dt>Runbook revision</dt><dd>${esc(plan.runbook_version ?? plan.runbook_revision_id ?? "current")}</dd></div>
    <div><dt>Caps</dt><dd>${esc(caps.max_steps ?? "bounded")} steps · ${esc(caps.max_duration_minutes ?? caps.max_minutes ?? "bounded")} minutes</dd></div>
    <div><dt>Safe stop</dt><dd>${esc(plan.recovery_label || plan.recovery_ref || "Use the pre-authored recovery path")}</dd></div></dl>
    ${plan.accepted_at ? `<p class="receipt">Accepted by ${esc(humanize(plan.accepted_by_actor_slug))} · ${esc(plan.accepted_at)}</p>` : ""}</section>`;
}

function renderOutcome(feedback) {
  if (!feedback) return "";
  return `<section class="system-work-evidence accepted"><p class="eyebrow">Observation accepted</p>
    <h2>${esc(humanize(feedback.outcome || feedback.proposed_outcome))}</h2>
    <p>${esc(feedback.result_summary || "Observed outcome recorded.")}</p>
    <dl><div><dt>Measured time</dt><dd>${esc(feedback.observed_minutes ?? "Not recorded")} minutes</dd></div>
    <div><dt>AI session</dt><dd>${feedback.heavy_session_used ? "Heavy AI session used" : "No heavy AI session"}</dd></div>
    <div><dt>Handoffs</dt><dd>${esc(feedback.manual_context_transfers ?? 0)} manual context transfers</dd></div></dl>
    <p class="receipt">Accepted by ${esc(humanize(feedback.accepted_by_actor_slug))} · ${esc(feedback.accepted_at)}</p>
    <p class="truth-note">This accepted an observation. It did not execute or close the work.</p></section>`;
}

export function renderSystemWorkCard(card) {
  if (!card) return `<section class="system-work-empty"><h2>Open a system concern</h2><p>Enter its Work Request reference, or report a new one.</p></section>`;
  const action = actionForCard(card);
  const criteria = Array.isArray(card.acceptance_criteria) ? card.acceptance_criteria : [];
  const source = card.source || {};
  return `${renderLifecycle(card)}<article class="system-work-card" data-state="${esc(card.state)}">
    <header><div><p class="eyebrow">${esc(card.human_ref)} · ${esc(humanize(card.state))}</p><h1>${esc(card.title)}</h1></div>
      <span class="source-freshness">${esc(source.freshness || "unknown freshness")}</span></header>
    <section class="system-work-source"><p class="eyebrow">Current shared source</p><strong>${esc(source.label || "Source unavailable")}</strong><small>${esc(source.provenance || "")}</small></section>
    <section><h2>Desired result</h2><p>${esc(card.desired_outcome)}</p><h3>How we’ll know</h3>
      <ul>${criteria.map((item) => `<li>${esc(item.text || item.label || item.id)}</li>`).join("")}</ul></section>
    ${card.triage ? `<section><h2>Human triage</h2><p>${esc(humanize(card.triage.classification))}</p><small>${esc(humanize(card.triage.human_actor_slug || card.triage.actor_slug))}${card.triage.triaged_at || card.triage.decided_at ? ` · ${esc(card.triage.triaged_at || card.triage.decided_at)}` : ""}</small></section>` : ""}
    ${renderPlan(card.plan)}
    ${card.pending_outcome_feedback ? `<section class="system-work-evidence pending"><p class="eyebrow">Pending human acceptance</p><h2>${esc(humanize(card.pending_outcome_feedback.proposed_outcome))}</h2><p>${esc(card.pending_outcome_feedback.result_summary)}</p></section>` : ""}
    ${renderOutcome(card.outcome_feedback)}
    <footer><p>${Number(card.accepted_feedback_count || 0)} accepted observation${Number(card.accepted_feedback_count || 0) === 1 ? "" : "s"}</p>
      ${card.source?.freshness !== "current" ? `<p class="truth-note">This Work Request is read-only until its source is current again.</p>` : ""}
      ${action ? `<button type="button" class="system-work-primary" data-system-action="${esc(action.kind)}">${esc(action.label)}</button>` : ""}</footer>
  </article>`;
}

export function renderCurrentWorkRequests(items) {
  if (!Array.isArray(items) || !items.length) {
    return `<section class="system-work-empty"><p class="eyebrow">No current system work</p><h2>No eligible Work Requests right now.</h2><p>This is a safe empty state, not a failure. Report a concern only when you observed a system behavior that blocks, degrades, or makes a governed outcome unsafe or unverifiable.</p><p>A report is not an idea, routine question, or a demo. It is sourced from current shared doctrine and still requires human review.</p></section>`;
  }
  return `<section class="system-work-current"><p class="eyebrow">Current system work</p><h2>Eligible Work Requests</h2><p>These are real, current sourced requests that can take the next bounded human step. Opening one does not execute work.</p><ul>${items.map((item) => `<li><button type="button" data-open-work-request="${esc(item.human_ref)}"><strong>${esc(item.human_ref)} · ${esc(item.title)}</strong><span>${esc(humanize(item.state))} · ${esc(item.source?.freshness || "unknown")} source</span><small>${esc(item.next_human_action || "Review current record")}</small></button></li>`).join("")}</ul></section>`;
}
