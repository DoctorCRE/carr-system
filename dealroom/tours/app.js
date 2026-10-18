(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const state = { csrf: "", tours: [], tour: null, rawShareToken: "", shareGrantId: "", shareStatus: "missing", shareGrants: [], projectionId: "", projectionDraftId: "", candidateDigest: "", renderJobId: "", pdfQcRunDigest: "", cheatDirty: false, cheatDraftTourId: "", feedbackToken: 0, tourGeneration: 0 };
  const uuid = () => crypto.randomUUID();
  const status = (message) => { $("#status").textContent = message; };
  const text = (value, fallback = "") => typeof value === "string" && value ? value : fallback;
  const digest = (value) => /^sha256:[0-9a-f]{64}$/i.test(value);
  const id = (value) => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const base64url = (bytes) => btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  async function sha256(value) { const bytes = new TextEncoder().encode(value); const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)); return `sha256:${[...hash].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`; }
  function newShareToken() { const bytes = new Uint8Array(32); crypto.getRandomValues(bytes); return base64url(bytes); }
  async function request(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    let payload = null; try { payload = await response.json(); } catch { /* only sanitized server errors are shown */ }
    if (!response.ok) throw new Error(payload?.error || "request_failed");
    if (typeof payload?.csrf_token === "string") state.csrf = payload.csrf_token;
    return payload?.data || {};
  }
  function post(path, body) { return request(path, { method: "POST", headers: { "content-type": "application/json", "x-carr-csrf": state.csrf }, body: JSON.stringify(body) }); }
  function renderLibrary() {
    const list = $("#tour-list"); list.replaceChildren();
    for (const tour of state.tours) { const button = document.createElement("button"); button.type = "button"; button.className = "tour-button"; button.textContent = `${text(tour.name, "Untitled tour")} · ${text(tour.status, "draft")}`; button.addEventListener("click", () => void loadTour(tour.id)); const item = document.createElement("li"); item.append(button); list.append(item); }
    if (!state.tours.length) list.textContent = "No tours are available.";
    list.setAttribute("aria-busy", "false");
  }
  function stops() { return Array.isArray(state.tour?.stops) ? state.tour.stops : []; }
  function renderShareGrants() { const list = $("#share-grants"); list.replaceChildren(); for (const grant of state.shareGrants) { if (!id(grant?.share_grant_id)) continue; const row = document.createElement("li"); const summary = document.createElement("span"); summary.textContent = `${text(grant.status, "unknown")} · projection ${text(grant.projection_id, "unknown")} · expires ${text(grant.expires_at, "unknown")}`; row.append(summary); if (grant.status === "active") { const button = document.createElement("button"); button.type = "button"; button.textContent = "Revoke"; button.dataset.shareGrantId = grant.share_grant_id; row.append(button); } list.append(row); } if (!list.children.length) list.textContent = "No active or rotatable confidential links."; }
  function renderTour() {
    const tour = state.tour; if (!tour) return;
    $("#empty-state").hidden = true; $("#tour-panel").hidden = false;
    $("#tour-name").textContent = text(tour.name, "Untitled tour"); $("#tour-state").textContent = text(tour.status, "Draft");
    $("#tour-meta").textContent = [tour.client_name, tour.market, tour.updated_at].filter(Boolean).join(" · ");
    $("#route-version").textContent = text(tour.route_version_label, tour.route_version_id ? "Current version" : "No route version");
    $("#save-route").hidden = tour.route_version_state !== "accepted";
    $("#reorder-route").hidden = tour.route_version_state !== "draft";
    $("#accept-route").hidden = tour.route_version_state !== "draft";
    state.projectionId = text(tour.projection_id); state.projectionDraftId = text(tour.projection_draft_id); state.shareGrantId = text(tour.share_grant_id); state.shareStatus = text(tour.share_status, "missing"); state.shareGrants = Array.isArray(tour.share_grants) ? tour.share_grants : [];
    const activeShareCount = state.shareGrants.filter((grant) => grant?.status === "active").length;
    $("#projection-state").textContent = state.projectionId ? "Approved" : tour.projection_status === "draft" ? "Draft · approval required" : "Not generated"; $("#share-state").textContent = activeShareCount ? `${activeShareCount} active` : state.shareStatus === "expired" ? "Expired · rotate" : "Not issued"; renderShareGrants();
    $("#projection-note").textContent = state.projectionId ? "The approved projection is ready for a deliberately scoped, expiring share." : tour.projection_status === "draft" ? "A projection draft exists but cannot be shared until a human authority seals it." : "A projection is required before an external link can be issued.";
    if (!state.cheatDirty || state.cheatDraftTourId !== tour.id) {
      $("#cheat-content").value = typeof tour.cheat_sheet?.content === "string" ? tour.cheat_sheet.content : JSON.stringify(tour.cheat_sheet?.content || {}, null, 2);
      state.cheatDirty = false; state.cheatDraftTourId = tour.id;
    }
    $("#sheet-state").textContent = state.cheatDirty ? "Unsaved changes" : text(tour.cheat_sheet?.revision_label, "Not saved");
    const list = $("#route-stops"); list.replaceChildren();
    for (const stop of stops()) { const row = document.createElement("li"); row.className = "stop"; row.dataset.stopId = text(stop.id); const label = document.createElement("span"); label.textContent = text(stop.label, text(stop.name, "Tour stop")); const controls = document.createElement("span"); for (const [word, delta] of [["Up", -1], ["Down", 1]]) { const button = document.createElement("button"); button.type = "button"; button.textContent = word; button.addEventListener("click", () => moveStop(stop.id, delta)); controls.append(button); } row.append(label, controls); list.append(row); }
    if (!stops().length) list.textContent = "No stops in this route cart.";
    state.renderJobId = text(tour.pdf_render_job_id); state.pdfQcRunDigest = text(tour.pdf_qc_run_digest);
    $("#pdf-state").textContent = text(tour.pdf_status, "Not rendered").replaceAll("_", " ");
    const reviewable = tour.pdf_status === "review_ready" && id(state.renderJobId);
    $("#preview-pdf").hidden = !reviewable;
    $("#review-pdf").hidden = !reviewable;
    if (reviewable) $("#preview-pdf").href = `/api/tours/pdf/preview?render_job_id=${encodeURIComponent(state.renderJobId)}`;
    const downloadable = tour.pdf_status === "available" && id(state.renderJobId);
    $("#download-pdf").hidden = !downloadable;
    if (downloadable) $("#download-pdf").href = `/api/tours/pdf/download?render_job_id=${encodeURIComponent(state.renderJobId)}`;
  }
  async function loadLibrary() { status("Loading tours…"); const data = await request("/api/tours/library"); state.tours = Array.isArray(data.tours) ? data.tours.filter((tour) => id(tour?.id)) : []; renderLibrary(); status("Tour library ready."); }
  async function loadProjectionPreview() { const preview = $("#projection-preview"); state.candidateDigest = ""; preview.hidden = true; preview.textContent = ""; if (!id(state.projectionDraftId)) return; const data = await request(`/api/tours/projection/candidates?projection_id=${encodeURIComponent(state.projectionDraftId)}`); state.candidateDigest = text(data.candidate_digest); const rows = Array.isArray(data.preview) ? data.preview : []; preview.textContent = rows.map(row => { const facts = row?.facts && typeof row.facts === "object" ? row.facts : {}; return `${text(row.route_label, `Stop ${row.route_sequence || ""}`)} · ${text(facts["display.name"], "Unnamed property")}\n${text(facts["display.address"], "Address unavailable")}\n${Object.keys(facts).sort().join(", ")}`; }).join("\n\n"); preview.hidden = false; }
  async function loadTour(tourId) { status("Loading tour…"); const generation = ++state.tourGeneration; state.feedbackToken += 1; feedbackMessage("Loading client feedback…"); if (state.tour?.id !== tourId) { state.cheatDirty = false; state.cheatDraftTourId = tourId; } const detail = await request(`/api/tours/detail?tour_id=${encodeURIComponent(tourId)}`); if (generation !== state.tourGeneration) return; state.tour = detail; renderTour(); const feedbackLoad = loadFeedback(); await loadProjectionPreview(); await feedbackLoad; if (generation === state.tourGeneration) status("Tour ready."); }
  function moveStop(stopId, delta) { const list = stops(); const index = list.findIndex((stop) => stop.id === stopId); const destination = index + delta; if (index < 0 || destination < 0 || destination >= list.length) return; [list[index], list[destination]] = [list[destination], list[index]]; renderTour(); }
  async function saveRoute(reorder = false) { if (!state.tour) return; const stopIds = stops().filter((stop) => stop.stop_state === "active").map((stop) => stop.id).filter(id); const path = reorder ? "/api/tours/route-reorder" : "/api/tours/route-version"; const payload = reorder ? { tour_id: state.tour.id, route_version_id: state.tour.route_version_id, expected_route_version: Number(state.tour.route_version || 0), stop_ids: stopIds, idempotency_key: uuid() } : { tour_id: state.tour.id, expected_route_version: Number(state.tour.route_version || 0), stop_ids: stopIds, idempotency_key: uuid() }; await post(path, payload); await loadTour(state.tour.id); status("Route version saved."); }
  async function saveSheet() { if (!state.tour) return; let content; try { content = JSON.parse($("#cheat-content").value || "{}"); } catch { content = { notes: $("#cheat-content").value }; } await post("/api/tours/cheat-sheet/autosave", { tour_id: state.tour.id, content, expected_revision_number: Number(state.tour.cheat_sheet?.revision_number || 0), idempotency_key: uuid() }); state.cheatDirty = false; await loadTour(state.tour.id); status("Internal cheat sheet saved."); }
  // Broker view of client feedback for the approved projection. Text only:
  // client comments are untrusted, so they are never parsed as markup.
  // feedbackToken names the one read whose result may still be shown: any newer
  // selection or refresh retires older reads, and a result is also checked
  // against the projection it was requested for.
  function feedbackMessage(message) { const list = $("#feedback-list"); list.replaceChildren(); list.textContent = message; }
  function renderFeedback(feedback) {
    const list = $("#feedback-list"); list.replaceChildren();
    const items = Array.isArray(feedback?.items) ? feedback.items : [];
    for (const item of items) {
      const row = document.createElement("li"); row.className = "feedback-item";
      const head = document.createElement("div"); head.className = "feedback-head";
      const label = document.createElement("strong"); label.textContent = text(item.route_label, "Stop");
      const answer = document.createElement("span"); answer.className = "feedback-answer";
      answer.textContent = item.shortlisted === true ? "Shortlisted" : item.shortlisted === false ? "Not shortlisted" : "No answer";
      head.append(label, answer); row.append(head);
      const comments = document.createElement("div"); comments.className = "feedback-comments";
      for (const comment of Array.isArray(item.comments) ? item.comments : []) {
        const line = document.createElement("p"); line.className = "feedback-comment";
        const when = document.createElement("time"); when.textContent = text(comment.created_at);
        const body = document.createElement("span"); body.textContent = text(comment.comment);
        line.append(when, body); comments.append(line);
      }
      row.append(comments); list.append(row);
    }
    if (!items.length) list.textContent = "No client feedback yet.";
  }
  async function loadFeedback() {
    const token = ++state.feedbackToken; const projectionId = state.projectionId;
    if (!id(projectionId)) { feedbackMessage("Approve a projection to collect client feedback."); return; }
    feedbackMessage("Loading client feedback…");
    let data;
    try { data = await request(`/api/tours/feedback?projection_id=${encodeURIComponent(projectionId)}`); }
    catch { if (token === state.feedbackToken && projectionId === state.projectionId) feedbackMessage("Client feedback could not be loaded. Refresh to try again."); return; }
    if (token !== state.feedbackToken || projectionId !== state.projectionId) return;
    if (data.feedback?.projection_id !== projectionId) { feedbackMessage("Client feedback could not be loaded. Refresh to try again."); return; }
    renderFeedback(data.feedback);
  }
  async function issueShare(rotate = false) { if (!state.projectionId) throw new Error("projection_required"); const raw = newShareToken(); const tokenDigest = await sha256(raw); const scopes = [...document.querySelectorAll('input[name="scope"]:checked')].map((box) => box.value); const expires = new Date($("#share-expiry").value).toISOString(); const receipt = $("#receipt-digest").value.trim(); if (!digest(receipt) || !scopes.length || !Number.isFinite(Date.parse(expires))) throw new Error("share_details_invalid"); const payload = { projection_id: state.projectionId, token_digest: tokenDigest, permission_scopes: scopes, expires_at: expires, receipt_digest: receipt, idempotency_key: uuid() }; const data = await post(rotate ? "/api/tours/share/rotate" : "/api/tours/share/issue", rotate ? { share_grant_id: state.shareGrantId, ...payload } : payload); state.rawShareToken = raw; state.shareGrantId = text(data.share_grant_id, state.shareGrantId); const url = `https://reports.doctorcre.com/share#token=${raw}`; $("#share-url").value = url; $("#share-link").hidden = false; $("#share-state").textContent = "Active"; status("Confidential link generated. Copy it now."); }
  async function revokeShare(grantId) { if (!id(grantId)) return; const receipt = $("#receipt-digest").value.trim(); if (!digest(receipt)) throw new Error("receipt_digest_required"); await post("/api/tours/share/revoke", { share_grant_id: grantId, reason: "Internal operator revoked link", revoked_at: new Date().toISOString(), receipt_digest: receipt, idempotency_key: uuid() }); state.rawShareToken = ""; $("#share-link").hidden = true; await loadTour(state.tour.id); status("Share link revoked."); }
  async function action(work) { try { await work(); } catch { status("That change could not be saved."); } }
  $("#refresh-feedback").addEventListener("click", () => void loadFeedback());
  $("#refresh").addEventListener("click", () => void action(loadLibrary)); $("#save-route").addEventListener("click", () => void action(() => saveRoute(false))); $("#reorder-route").addEventListener("click", () => void action(() => saveRoute(true)));
  $("#accept-route").addEventListener("click", () => void action(async () => { if (!state.tour?.route_version_id) return; const prior = Number(state.tour.accepted_route_version || 0); await post("/api/tours/route-accept", { route_version_id: state.tour.route_version_id, expected_prior_route_version: prior, acceptance_digest: await sha256(`${state.tour.route_version_id}:${prior}`), idempotency_key: uuid() }); await loadTour(state.tour.id); status("Route version accepted."); }));
  $("#cheat-content").addEventListener("input", () => { state.cheatDirty = true; state.cheatDraftTourId = state.tour?.id || ""; $("#sheet-state").textContent = "Unsaved changes"; });
  $("#save-sheet").addEventListener("click", () => void action(saveSheet)); $("#restore-sheet").addEventListener("click", () => void action(async () => { const revision = state.tour?.cheat_sheet?.restore_revision_id; if (!state.tour || !id(revision)) return; await post("/api/tours/cheat-sheet/restore", { tour_id: state.tour.id, restore_revision_id: revision, expected_revision_number: Number(state.tour.cheat_sheet?.revision_number || 0), idempotency_key: uuid() }); state.cheatDirty = false; await loadTour(state.tour.id); }));
  $("#generate-projection").addEventListener("click", () => void action(async () => { if (!state.tour?.route_version_id || state.tour.route_version_state !== "accepted") return; await post("/api/tours/projection", { tour_id: state.tour.id, route_version_id: state.tour.route_version_id, as_of: new Date().toISOString(), idempotency_key: uuid() }); await loadTour(state.tour.id); status("Client projection draft created. Human approval is required before sharing."); }));
  $("#seal-projection").addEventListener("click", () => void action(async () => { const receipt = $("#receipt-digest").value.trim(); if (!id(state.projectionDraftId) || !digest(state.candidateDigest) || !digest(receipt)) throw new Error("projection_review_required"); await post("/api/tours/projection/seal", { projection_id: state.projectionDraftId, candidate_digest: state.candidateDigest, receipt_digest: receipt, idempotency_key: uuid() }); await loadTour(state.tour.id); status("Reviewed facts-only projection approved. It can now be shared or rendered."); }));
  $("#share-form").addEventListener("submit", (event) => { event.preventDefault(); void action(() => issueShare(id(state.shareGrantId))); }); $("#rotate-share").addEventListener("click", () => void action(() => issueShare(true)));
  $("#revoke-share").addEventListener("click", () => void action(() => revokeShare(state.shareGrantId)));
  $("#share-grants").addEventListener("click", (event) => { const grantId = event.target?.dataset?.shareGrantId; if (id(grantId)) void action(() => revokeShare(grantId)); });
  $("#copy-share").addEventListener("click", () => void action(async () => { await navigator.clipboard.writeText($("#share-url").value); status("Confidential link copied."); }));
  $("#render-pdf").addEventListener("click", () => void action(async () => { if (!id(state.projectionId)) throw new Error("projection_required"); const data = await post("/api/tours/pdf/render", { projection_id: state.projectionId, idempotency_key: uuid() }); state.renderJobId = text(data.render_job_id); state.pdfQcRunDigest = text(data.qc_run_digest); await loadTour(state.tour.id); status("PDF rendered and QC checked. Human review is required before download."); }));
  $("#review-pdf").addEventListener("click", () => void action(async () => { if (!id(state.renderJobId) || !digest(state.pdfQcRunDigest)) throw new Error("pdf_review_required"); const reviewedAt = new Date().toISOString(); await post("/api/tours/pdf/review", { render_job_id: state.renderJobId, qc_run_digest: state.pdfQcRunDigest, decision: "accept", reviewed_at: reviewedAt, review_receipt_digest: await sha256(`tour-pdf-human-review:${state.renderJobId}:${state.pdfQcRunDigest}:${reviewedAt}`), reason: "Internal operator visually reviewed the deterministic property pages", idempotency_key: uuid() }); await loadTour(state.tour.id); status("PDF review receipt recorded. Internal download is available."); }));
  $("#share-expiry").value = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 16); void action(loadLibrary);
})();
