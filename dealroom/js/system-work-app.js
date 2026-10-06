import { escapeHtml as esc } from "./esc.js";
import { createSystemWorkClient } from "./system-work-client.js";
import { actionForCard, renderCurrentWorkRequests, renderSystemWorkCard, validateHumanRef } from "./system-work-view.js";

const client = createSystemWorkClient();
const state = { card: null, proposed: null, current: [] };
const $ = (selector) => document.querySelector(selector);

function alert(message, kind = "error") {
  const node = $("#systemWorkAlert");
  node.hidden = !message;
  node.className = `system-work-alert ${kind}`;
  node.textContent = message || "";
}

function render() {
  $("#systemWorkStage").innerHTML = state.card ? renderSystemWorkCard(state.card) : renderCurrentWorkRequests(state.current);
  const ref = state.card?.human_ref;
  if (ref) {
    history.replaceState(null, "", `/system-work.html?work_request=${encodeURIComponent(ref)}`);
    $("#workRequestRef").value = ref;
  }
}

function refusal(error) {
  if (error.code === "program6_actions_disabled") return "System work is not enabled in this environment yet. No change was made.";
  if (error.code === "version_conflict") return "This request changed. No change was made. The latest card is loading; review it before trying again.";
  if (["authority_connection_unavailable", "human_only", "reauth_required"].includes(error.code))
    return `Signed in as ${client.session?.actor?.display || "a partner"}. A fresh Joe or Dell confirmation is required. No change was made.`;
  if (/source|runbook|plan_hash|feedback_hash|stale/.test(error.code || ""))
    return "The evidence behind this step changed. It was not accepted. Review the current card and prepare it again.";
  if (error.status === 404) return "That Work Request was not found.";
  return error.message || "No receipt was confirmed. No success has been shown.";
}

async function refresh(ref = state.card?.human_ref) {
  if (!ref) { state.card = null; render(); return; }
  state.card = await client.read(validateHumanRef(ref));
  state.proposed = state.card.pending_outcome_feedback || null;
  render();
}

function field(label, control, hint = "") {
  return `<label class="system-work-field"><span>${esc(label)}</span>${control}${hint ? `<small>${esc(hint)}</small>` : ""}</label>`;
}

function openForm({ eyebrow, title, submit, body, onSubmit }) {
  const dialog = $("#systemWorkDialog");
  $("#systemWorkEyebrow").textContent = eyebrow;
  $("#systemWorkTitle").textContent = title;
  $("#systemWorkSubmit").textContent = submit;
  $("#systemWorkFields").innerHTML = body;
  $("#systemWorkFormError").hidden = true;
  const form = $("#systemWorkForm");
  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = $("#systemWorkSubmit");
    button.disabled = true;
    try { await onSubmit(new FormData(form)); dialog.close(); alert(""); }
    catch (error) {
      $("#systemWorkFormError").textContent = refusal(error);
      $("#systemWorkFormError").hidden = false;
      if (error.code === "reauth_required" && client.session?.reauth_url) {
        const link = document.createElement("a");
        link.href = `/auth/reauth?return_to=${encodeURIComponent(location.pathname + location.search)}`;
        link.textContent = "Re-authenticate now";
        $("#systemWorkFormError").append(" ", link);
      }
      if (error.code === "version_conflict") await refresh().catch(() => {});
    } finally { button.disabled = false; }
  };
  dialog.showModal();
}

function reportForm() {
  openForm({ eyebrow: "Source first", title: "Report a system problem", submit: "Record concern",
    body: field("Situation", `<textarea name="situation" maxlength="1000" required></textarea>`, "Describe the system concern so current shared doctrine can be matched.") +
      field("Short name", `<input name="title" maxlength="200" required>`) +
      field("Desired result", `<textarea name="desired_outcome" maxlength="2000" required></textarea>`) +
      field("How we’ll know", `<textarea name="criteria" maxlength="2000" required></textarea>`, "One measurable criterion per line; 1–12 lines."),
    onSubmit: async (data) => {
      const criteria = String(data.get("criteria")).split("\n").map((value) => value.trim()).filter(Boolean);
      if (!criteria.length || criteria.length > 12) throw new Error("Enter between 1 and 12 criteria.");
      const result = await client.report({ situation: data.get("situation"), title: data.get("title"),
        desired_outcome: data.get("desired_outcome"),
        acceptance_criteria: criteria.map((text, index) => ({ id: `CRITERION-${index + 1}`, text })) });
      await refresh(result.human_ref);
    } });
}

function triageForm() {
  openForm({ eyebrow: state.card.human_ref, title: "Confirm classification", submit: "Confirm classification",
    body: field("Classification", `<select name="classification" required><option value="operational">Routine operations</option><option value="needs_judgment">Needs partner judgment</option><option value="safety_review">Safety review</option></select>`, "This classifies the concern. It does not assign or execute it."),
    onSubmit: async (data) => { await client.triage(state.card.human_ref,
      { base_version: state.card.version, classification: data.get("classification") }); await refresh(); } });
}

function planForm() {
  openForm({ eyebrow: state.card.human_ref, title: "Prepare bounded plan", submit: "Prepare plan",
    body: field("Scope", `<textarea name="scope_summary" maxlength="1000" required></textarea>`,
      "Describe only the bounded observation scope. The server selects the fixed runbook, caps, safe stop, and observability references."),
    onSubmit: async (data) => { await client.preparePlan(state.card.human_ref,
      { base_version: state.card.version, scope_summary: data.get("scope_summary") }); await refresh(); } });
}

function acceptPlanForm() {
  const plan = state.card.plan;
  openForm({ eyebrow: "Fresh human confirmation", title: "Accept this exact bounded plan", submit: "Accept this plan",
    body: `<div class="system-work-review"><strong>${esc(plan.scope_summary)}</strong><p>${esc(plan.runbook_label || plan.runbook_ref)}</p><small>Acceptance records this exact plan. It does not execute it.</small></div>`,
    onSubmit: async () => { await client.acceptPlan(state.card.human_ref,
      { base_version: state.card.version, plan_hash: plan.plan_hash }); await refresh(); } });
}

function outcomeForm() {
  const criteria = state.card.acceptance_criteria || [];
  const criterionFields = criteria.map((item) => field(item.text || item.id,
    `<select name="criterion:${esc(item.id)}" required><option value="met">Met</option><option value="not_met">Not met</option><option value="not_observed">Not observed</option></select>`)).join("");
  openForm({ eyebrow: state.card.human_ref, title: "Record what happened", submit: "Prepare outcome record",
    body: criterionFields +
      field("Safe evidence references", `<textarea name="evidence_refs" required placeholder="safe:workspace:receipt-123"></textarea>`, "One safe: reference per line; no raw client or business payload.") +
      field("Blocker", `<select name="blocker_code"><option value="none">None</option><option value="criterion_not_met">Criterion not met</option><option value="evidence_missing">Evidence missing</option><option value="external_dependency">External dependency</option><option value="system_error">System error</option></select>`) +
      field("Result summary", `<textarea name="result_summary" maxlength="500" required></textarea>`) +
      field("Measured minutes", `<input name="observed_minutes" type="number" min="1" max="1440" required>`) +
      field("Heavy AI session used?", `<select name="heavy_session_used"><option value="false">No</option><option value="true">Yes</option></select>`) +
      field("Manual context transfers", `<input name="manual_context_transfers" type="number" min="0" max="100" value="0" required>`),
    onSubmit: async (data) => {
      const criterion_results = criteria.map((item) => ({ id: item.id, result: data.get(`criterion:${item.id}`) }));
      const evidence_refs = String(data.get("evidence_refs")).split("\n").map((v) => v.trim()).filter(Boolean);
      state.proposed = await client.proposeOutcome(state.card.human_ref, { base_version: state.card.version,
        plan_hash: state.card.plan.plan_hash, criterion_results, evidence_refs,
        blocker_code: data.get("blocker_code"), result_summary: data.get("result_summary"),
        observed_minutes: Number(data.get("observed_minutes")), interaction_surface: "workspace",
        heavy_session_used: data.get("heavy_session_used") === "true",
        manual_context_transfers: Number(data.get("manual_context_transfers")) });
      await refresh();
    } });
}

function acceptOutcomeForm() {
  const feedback = state.card.pending_outcome_feedback || state.proposed;
  openForm({ eyebrow: "Fresh human confirmation", title: "Accept this outcome record", submit: "Accept outcome record",
    body: `<div class="system-work-review"><strong>${esc(feedback.proposed_outcome || feedback.outcome)}</strong><p>${esc(feedback.result_summary)}</p><small>This accepts an observation. It does not execute or close work.</small></div>`,
    onSubmit: async () => { await client.acceptOutcome(state.card.human_ref,
      { base_version: state.card.version, feedback_hash: feedback.feedback_hash }); await refresh(); } });
}

const actionForms = { triage: triageForm, "prepare-plan": planForm, "accept-plan": acceptPlanForm,
  "record-outcome": outcomeForm, "accept-outcome": acceptOutcomeForm };

async function boot() {
  const session = await client.bootstrap();
  $("#systemWorkActor").textContent = `Signed in as ${session.actor?.display || session.actor?.slug || "partner"}`;
  $("#reportProblemButton").onclick = reportForm;
  $("#openWorkRequest").onsubmit = async (event) => { event.preventDefault();
    try { await refresh(new FormData(event.currentTarget).get("human_ref")); alert(""); }
    catch (error) { alert(refusal(error)); } };
  $("#systemWorkStage").onclick = (event) => {
    const open = event.target.closest("[data-open-work-request]");
    if (open) { refresh(open.dataset.openWorkRequest).catch((error) => alert(refusal(error))); return; }
    const button = event.target.closest("[data-system-action]");
    if (button) actionForms[button.dataset.systemAction]?.();
  };
  document.querySelectorAll("[data-system-cancel]").forEach((button) => { button.onclick = () => $("#systemWorkDialog").close(); });
  const requested = new URLSearchParams(location.search).get("work_request");
  if (requested) await refresh(requested);
  else { state.current = (await client.current()).items || []; render(); }
}

boot().catch((error) => alert(refusal(error)));
