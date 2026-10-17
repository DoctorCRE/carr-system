import { readMigration } from "./migration-source.testhelper.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { createCaptureHandler } from "../src/capture.js";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";

const ids = {
  device: "10000000-0000-0000-0000-000000000001",
  joe: "10000000-0000-0000-0000-000000000002",
  dell: "10000000-0000-0000-0000-000000000003",
  deal: "20000000-0000-0000-0000-000000000001",
  deal2: "20000000-0000-0000-0000-000000000002",
  party: "25000000-0000-0000-0000-000000000001",
  session: "30000000-0000-0000-0000-000000000001",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "dealroom-cookie", client_id: "dealroom-pwa" };

class CaptureFake {
  constructor() {
    this.actors = new Map([["mac-studio", ids.device], ["joe", ids.joe]]);
    this.sessions = [];
    this.candidates = [];
    this.postCallCandidates = [];
    this.postCallReports = [];
    this.activities = [];
    this.nextActions = [];
    this.postCallActions = [];
    this.events = [];
    this.toolCalls = new Map();
    this.sequence = 1;
    this.failActivity = false;
    this.queryCount = 0;
  }

  uuid(prefix) {
    return `${prefix}0000000-0000-0000-0000-${String(this.sequence++).padStart(12, "0")}`;
  }

  async query(text, params = []) {
    this.queryCount += 1;
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.includes("capture:claim-nonce"))
      return { rows: this.sessions.filter(session => session.nonce === params[0]).map(session => ({ id: session.id })) };
    if (sql.includes("capture:device-actor"))
      return { rows: this.actors.has(params[0]) ? [{ id: this.actors.get(params[0]) }] : [] };
    if (sql.includes("capture:claim-insert")) {
      this.sessions.push({ id: ids.session, nonce: params[0], device_id: params[1], actor_id: params[2],
        mode: params[3], started_at: params[4], consent_announced_at: params[5], token_hash: params[6],
        state: "recording", state_at: params[4], post_call: params[8] });
      return { rows: [] };
    }
    if (sql.includes("capture:session-auth")) {
      const session = this.sessions.find(row => row.token_hash === params[0]);
      return { rows: session ? [{ id: session.id, state: session.state, post_call: session.post_call,
        state_at: session.state_at }] : [] };
    }
    if (sql.includes("capture:status-update")) {
      const session = this.sessions.find(row => row.id === params[0]);
      session.state = params[1];
      session.state_at = params[2];
      session.detail = params[3];
      return { rows: [{ state: session.state, state_at: session.state_at }] };
    }
    if (sql.includes("capture:candidates-insert")) {
      const items = JSON.parse(params[3]);
      items.forEach((item, item_index) => {
        if (this.candidates.some(row => row.session_id === params[0] && row.idempotency_key === params[1] && row.item_index === item_index)) return;
        this.candidates.push({ id: this.uuid("4"), session_id: params[0], idempotency_key: params[1],
          batch_hash: params[2], item_index, ...structuredClone(item), status: "pending", resulting_ref: null });
      });
      return { rows: [] };
    }
    if (sql.includes("capture:candidates-prior")) {
      const row = this.candidates.find(candidate => candidate.session_id === params[0] && candidate.idempotency_key === params[1]);
      return { rows: row ? [{ batch_hash: row.batch_hash }] : [] };
    }
    if (sql.includes("capture:candidates-result"))
      return { rows: this.candidates.filter(row => row.session_id === params[0] && row.idempotency_key === params[1])
        .sort((a, b) => a.item_index - b.item_index).map(row => ({ id: row.id, item_index: row.item_index })) };
    if (sql.includes("capture:session-read")) {
      const session = this.sessions.find(row => row.id === params[0]);
      const rows = [...this.candidates, ...this.postCallCandidates].filter(row => row.session_id === params[0]);
      const meeting = rows.find(row => row.kind === "meeting_record" && row.status === "confirmed");
      return { rows: [{ state: session.state, post_call: session.post_call,
        pending: rows.filter(row => row.status === "pending").length,
        confirmed: rows.filter(row => row.status === "confirmed").length,
        skipped: rows.filter(row => row.status === "skipped").length,
        meeting_record: meeting?.resulting_ref || null,
        candidate_statuses: rows.map(row => ({ id: row.id, kind: row.kind, status: row.status,
          resulting_ref: row.resulting_ref || null,
          source: this.postCallCandidates.includes(row) ? "post_call" : "legacy" })),
        post_call_candidates: this.postCallCandidates.filter(row => row.session_id === params[0])
          .map(row => ({ id: row.id, kind: row.kind, status: row.status,
            resulting_ref: row.resulting_ref || null })) }] };
    }
    if (sql.includes("capture:post-call-finalize-check") || sql.includes("capture:post-call-report-count")) {
      const rows = [...this.candidates, ...this.postCallCandidates].filter(row => row.session_id === params[0]);
      const report = this.postCallReports.find(row => row.session_id === params[0]);
      return { rows: [{ total: rows.length, pending: rows.filter(row => row.status === "pending").length,
        report_filed: !!report, report_candidate_count: report?.candidate_count ?? null }] };
    }
    if (sql.includes("capture:post-call-context") || sql.includes("capture:post-call-legacy-context") ||
        sql.includes("capture:call-context") ||
        sql.includes("capture:tool-call-context")) {
      const requested = params[0];
      const all = [
        { deal_id: ids.deal, deal_name: "Deal Alpha", owner: "joe", operating_state: "active", participant_party_id: ids.party,
          participant_party_ref: "P-0001", participant_name: "Dr. Alpha", participant_email: "alpha@example.com",
          participant_role: "client_contact", party_id: ids.party, party_ref: "P-0001" },
        { deal_id: ids.deal2, deal_name: "Deal Beta", owner: "dell", operating_state: "active", participant_party_id: ids.party,
          participant_party_ref: "P-0001", participant_name: "Dr. Alpha", participant_email: "alpha@example.com",
          participant_role: "client_contact", party_id: ids.party, party_ref: "P-0001" },
      ];
      return { rows: Array.isArray(requested) ? all.filter(row => requested.includes(row.deal_id)) : all };
    }
    if (sql.includes("capture:post-call-candidates-prior")) {
      const row = this.postCallCandidates.find(candidate => candidate.session_id === params[0] && candidate.idempotency_key === params[1]);
      return { rows: row ? [{ batch_hash: row.batch_hash }] : [] };
    }
    if (sql.includes("capture:post-call-candidates-report-lock"))
      return { rows: this.postCallReports.filter(report => report.session_id === params[0]).map(report => ({ ...report })) };
    if (sql.includes("capture:post-call-legacy-report-lock"))
      return { rows: this.postCallReports.filter(report => report.session_id === params[0]).map(report => ({ ...report })) };
    if (sql.includes("capture:post-call-candidates-insert")) {
      const items = JSON.parse(params[3]);
      items.forEach((item, item_index) => {
        if (this.postCallCandidates.some(row => row.session_id === params[0] && row.idempotency_key === params[1] && row.item_index === item_index)) return;
        const payload = item.payload;
        this.postCallCandidates.push({ id: this.uuid("6"), session_id: params[0], idempotency_key: params[1],
          batch_hash: params[2], item_index, kind: item.kind, deal_id: payload.deal_id,
          assignee_slug: payload.assignee || null, action_description: payload.action || null,
          due_on: payload.due_on || null, recipient_party_id: payload.recipient_party_id || null,
          recipient_ref: payload.recipient_ref || null, email_subject: payload.subject || null,
          body_sha256: payload.body_sha256 || null, evidence_quote: item.evidence_quote,
          confidence: item.confidence, status: "pending", resulting_ref: null });
      });
      return { rows: [] };
    }
    if (sql.includes("capture:post-call-candidates-result"))
      return { rows: this.postCallCandidates.filter(row => row.session_id === params[0] && row.idempotency_key === params[1])
        .sort((a, b) => a.item_index - b.item_index).map(row => ({ id: row.id, item_index: row.item_index })) };
    if (sql.includes("capture:post-call-report-prior")) {
      const row = this.postCallReports.find(report => report.session_id === params[0]);
      return { rows: row ? [{ ...row }] : [] };
    }
    if (sql.includes("capture:post-call-report-insert")) {
      this.postCallReports.push({ session_id: params[0], idempotency_key: params[1], report_sha256: params[2], candidate_count: params[3] });
      return { rows: [] };
    }
    if (sql.includes("from v_capture_candidate_queue"))
      return { rows: this.candidates.filter(row => row.status === "pending")
        .sort((a, b) => b.confidence - a.confidence)
        .map(row => ({ id: row.id, session_id: row.session_id, kind: row.kind,
          payload: row.payload, evidence_quote: row.evidence_quote, confidence: row.confidence,
          deal_name: row.payload.deal || row.payload.ref || null,
          created_at: "2026-08-08 15:00:00+00" })) };
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.toolCalls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null });
      return { rows: [] };
    }
    if (sql.includes("capture:resolve-read")) {
      const row = this.candidates.find(candidate => candidate.id === params[0]);
      return { rows: row ? [{ id: row.id, kind: row.kind, payload: row.payload,
        status: row.status, resulting_ref: row.resulting_ref }] : [] };
    }
    if (sql.includes("capture:resolve-skip")) {
      const row = this.candidates.find(candidate => candidate.id === params[0]);
      Object.assign(row, { status: "skipped", resolved_by: params[1], note: params[2] });
      return { rows: [] };
    }
    if (sql.includes("capture:resolve-confirm")) {
      const row = this.candidates.find(candidate => candidate.id === params[0]);
      Object.assign(row, { status: "confirmed", resolved_by: params[1], note: params[2], resulting_ref: params[3] });
      return { rows: [] };
    }
    if (sql.includes("capture:resolve-post-call-read")) {
      const row = this.postCallCandidates.find(candidate => candidate.id === params[0]);
      return { rows: row ? [{ ...row }] : [] };
    }
    if (sql.includes("capture:resolve-post-call-skip") || sql.includes("capture:resolve-post-call-email") ||
        sql.includes("capture:resolve-post-call-confirm")) {
      const row = this.postCallCandidates.find(candidate => candidate.id === params[0]);
      Object.assign(row, { status: sql.includes("skip") ? "skipped" : "confirmed", resolved_by: params[1],
        note: params[2], resulting_ref: sql.includes("confirm") ? params[3] : null });
      return { rows: [] };
    }
    if (sql.includes("capture:resolve-post-call-assignee"))
      return { rows: params[0] === "joe" ? [{ id: ids.joe }] : params[0] === "dell" ? [{ id: "10000000-0000-0000-0000-000000000003" }] : [] };
    if (sql.includes("capture:resolve-post-call-drop-prior")) return { rows: [] };
    if (sql.includes("capture:resolve-post-call-action")) {
      const action = { id: this.uuid("7"), candidate_id: params[0], deal_id: params[1], owner_id: params[2],
        due_on: params[3], description: params[4], accepted_by: params[5], status: "open",
        updated_at: "2026-08-08T15:00:00.000Z", completed_at: null };
      this.postCallActions.push(action);
      return { rows: [{ id: action.id }] };
    }
    if (sql.startsWith("select subject_type, subject_id from v_ref_index where subject_id=$1"))
      return { rows: params[0] === ids.deal ? [{ subject_type: "deal", subject_id: ids.deal }] : [] };
    if (sql.startsWith("update next_action set status='dropped'")) {
      for (const action of this.nextActions) {
        if (action.owner_id === params[0] && action.subject_type === params[1] && action.subject_id === params[2] && action.status === "open")
          action.status = "dropped";
      }
      return { rows: [] };
    }
    if (sql.includes("capture:replace-post-call-actions")) {
      const changed = this.postCallActions.filter(action => action.deal_id === params[0] && action.owner_id === params[1] && action.status === "open");
      changed.forEach(action => { action.status = "dropped"; action.updated_at = "2026-08-08T16:00:00.000Z"; });
      return { rows: changed.map(action => ({ id: action.id, description: action.description })) };
    }
    if (sql.startsWith("insert into next_action (subject_type, subject_id, owner_id")) {
      const outreachOrder = sql.includes("owner_id, description,");
      const action = { id: this.uuid("8"), subject_type: params[0], subject_id: params[1], owner_id: params[2],
        due_on: outreachOrder ? params[4] : params[3],
        description: outreachOrder ? params[3] : params[4], status: "open" };
      this.nextActions.push(action);
      return { rows: [{ id: action.id }] };
    }
    if (sql.startsWith("update next_action set status='done'")) {
      const changed = this.nextActions.filter(action => action.owner_id === params[0] && action.subject_type === params[1] &&
        action.subject_id === params[2] && action.status === "open");
      changed.forEach(action => { action.status = "done"; });
      return { rows: changed.map(action => ({ id: action.id, description: action.description, due_on: action.due_on })) };
    }
    if (sql.includes("capture:complete-post-call-actions")) {
      const changed = this.postCallActions.filter(action => action.deal_id === params[0] && action.owner_id === params[1] && action.status === "open");
      changed.forEach(action => { action.status = "done"; action.updated_at = "2026-08-08T16:00:00.000Z"; action.completed_at = action.updated_at; });
      return { rows: changed.map(action => ({ id: action.id, description: action.description, due_on: action.due_on })) };
    }
    if (sql.includes("capture:outreach-complete-post-call-actions")) {
      const changed = this.postCallActions.filter(action => action.deal_id === params[0] && action.owner_id === params[1] && action.status === "open");
      changed.forEach(action => { action.status = "done"; action.updated_at = "2026-08-08T16:00:00.000Z"; action.completed_at = action.updated_at; });
      return { rows: changed.map(action => ({ id: action.id, description: action.description, due_on: action.due_on })) };
    }
    if (sql.includes("from v_ref_index where subject_type='deal'"))
      return { rows: [{ subject_id: ids.deal, display_name: "Deal Alpha", status: "negotiation", client_ref: "C-1" }] };
    if (sql.startsWith("insert into activity")) {
      if (this.failActivity) throw new ToolError({ error: "inner_write_failed" });
      const id = this.uuid("5");
      this.activities.push({ id, actor_id: params[1], kind: params[2], summary: params[3], detail: params[4] });
      return { rows: [{ id, occurred_at: "2026-08-08 10:00:00+00" }] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push({ actor_id: params[1], verb: params[2], subject_type: params[3], subject_id: params[4] });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

function rig(db) {
  let randomCalls = 0;
  const handler = createCaptureHandler({
    randomStringFn: () => `opaque-session-${++randomCalls}`,
    withWriter: fn => fn(db),
  });
  return { handler, get randomCalls() { return randomCalls; } };
}

function claimRequest(body, token = "device-secret") {
  return new Request("https://worker.test/capture/claim", { method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify(body) });
}

const validClaim = { nonce: "nonce-1", device_id: "mac-studio", mode: "meeting",
  started_at: "2026-08-08T15:00:00.000Z", consent: { announced_at: "2026-08-08T14:59:58.000Z" } };
const validPostCallClaim = { ...validClaim, nonce: "post-call-nonce", workflow: "post_call" };

async function claimed() {
  const db = new CaptureFake();
  const r = rig(db);
  const response = await r.handler.fetch(claimRequest(validClaim), { CAPTURE_TOKENS: '{"mac-studio":"device-secret"}' });
  return { db, r, token: (await response.json()).session_token };
}

async function post(handler, path, body) {
  return handler.fetch(new Request(`https://worker.test${path}`, { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify(body) }), {});
}

test("claim authenticates first, requires consent, and never mints on nonce replay", async () => {
  const db = new CaptureFake();
  const r = rig(db);
  const environment = { CAPTURE_TOKENS: '{"mac-studio":"device-secret"}' };
  const unauthorized = await r.handler.fetch(claimRequest({ transcript: "must not parse" }, "wrong"), environment);
  assert.equal(unauthorized.status, 401);
  assert.equal(db.queryCount, 0);
  const missing = await r.handler.fetch(claimRequest({ ...validClaim, consent: { announced_at: null } }), environment);
  assert.equal(missing.status, 400);
  const forbidden = await r.handler.fetch(claimRequest({ ...validClaim, transcript: "must never land" }), environment);
  assert.equal(forbidden.status, 400);
  assert.equal(db.sessions.length, 0);
  const first = await r.handler.fetch(claimRequest(validClaim), environment);
  assert.equal(first.status, 200);
  assert.equal(r.randomCalls, 1);
  const replay = await r.handler.fetch(claimRequest(validClaim), environment);
  assert.equal(replay.status, 409);
  assert.equal(r.randomCalls, 1);
  assert.equal(db.sessions.length, 1);
});

test("status transitions move forward, reject every backward edge, and stop at terminals", async () => {
  for (const terminal of ["done", "failed"]) {
    const { db, r, token } = await claimed();
    const path = terminal === "done" ? ["transcribing", "distilling", "done"] : ["transcribing", "failed"];
    for (const [index, state] of path.entries()) {
      const response = await post(r.handler, "/capture/status", { session_token: token, state,
        at: `2026-08-08T15:0${index + 1}:00.000Z` });
      assert.equal(response.status, 200);
      assert.equal(typeof (await response.json()).at, "string");
      if (state !== "failed") {
        for (const backward of ["recording", "transcribing", "distilling"].filter(s =>
          ["recording", "transcribing", "distilling", "done"].indexOf(s) <
          ["recording", "transcribing", "distilling", "done"].indexOf(state))) {
          const denied = await post(r.handler, "/capture/status", { session_token: token, state: backward,
            at: "2026-08-08T16:00:00.000Z" });
          assert.equal(denied.status, 409, `${state} -> ${backward}`);
        }
      }
    }
    for (const state of ["recording", "transcribing", "distilling", "done", "failed"]) {
      const after = await post(r.handler, "/capture/status", { session_token: token, state,
        at: "2026-08-08T17:00:00.000Z" });
      assert.equal(after.status, 409, `${terminal} -> ${state}`);
    }
    assert.equal(db.sessions[0].state, terminal);
  }
});

test("candidate batches are atomic at validation, idempotent, and never auto-write", async () => {
  const { db, r, token } = await claimed();
  const tooLong = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen";
  let response = await post(r.handler, "/capture/candidates", { session_token: token, idempotency_key: "batch-bad",
    items: [{ kind: "activity", payload: { ref: "Deal Alpha", summary: "Call" }, evidence_quote: "fine", confidence: 0.5 },
      { kind: "activity", payload: { ref: "Deal Alpha", summary: "Call" }, evidence_quote: tooLong, confidence: 0.5 }] });
  assert.equal(response.status, 400);
  assert.equal(db.candidates.length, 0);
  response = await post(r.handler, "/capture/candidates", { session_token: token, idempotency_key: "batch-transcript",
    items: [{ kind: "activity", payload: { ref: "Deal Alpha", transcript: "must never land" },
      evidence_quote: "fine", confidence: 0.5 }] });
  assert.equal(response.status, 400);
  assert.equal(db.candidates.length, 0);

  const batch = { session_token: token, idempotency_key: "batch-1", items: [{ kind: "activity",
    payload: { ref: "Deal Alpha", summary: "Discussed lease", kind: "call" },
    evidence_quote: "Please call me next week", confidence: 0.99 }] };
  response = await post(r.handler, "/capture/candidates", batch);
  assert.equal(response.status, 200);
  await post(r.handler, "/capture/candidates", batch);
  assert.equal(db.candidates.length, 1);
  response = await post(r.handler, "/capture/candidates", { ...batch,
    items: [{ ...batch.items[0], evidence_quote: "different evidence" }] });
  assert.equal(response.status, 409);
  assert.equal(db.candidates.length, 1);
  assert.equal(db.candidates[0].status, "pending");
  assert.equal(db.activities.length, 0);
  assert.equal(db.events.length, 0);
  const queue = await TOOLS["capture-queue"].handler(db, joe, {});
  assert.equal(queue.candidates.length, 1);
  assert.equal(queue.candidates[0].confidence, 0.99);
  assert.equal(typeof queue.candidates[0].created_at, "string");
});

test("post-call legacy lane accepts only note activities with exact active deal UUIDs", async () => {
  const db = new CaptureFake();
  const r = rig(db);
  const environment = { CAPTURE_TOKENS: '{"mac-studio":"device-secret"}' };
  const claimedResponse = await r.handler.fetch(claimRequest(validPostCallClaim), environment);
  const token = (await claimedResponse.json()).session_token;
  const candidate = (overrides = {}) => ({ kind: "activity",
    payload: { ref: ids.deal, kind: "note", summary: "Reviewed landlord follow-up" },
    evidence_quote: "Landlord requested revised timing", confidence: 0.9, ...overrides });

  let response = await post(r.handler, "/capture/candidates", { session_token: token,
    idempotency_key: "post-legacy-phase", items: [{ ...candidate(), kind: "phase_move" }] });
  assert.equal(response.status, 409);
  response = await post(r.handler, "/capture/candidates", { session_token: token,
    idempotency_key: "post-legacy-name", items: [candidate({ payload: {
      ref: "Deal Alpha", kind: "note", summary: "Reviewed landlord follow-up" } })] });
  assert.equal(response.status, 400);
  response = await post(r.handler, "/capture/candidates", { session_token: token,
    idempotency_key: "post-legacy-unknown", items: [candidate({ payload: {
      ref: "20000000-0000-0000-0000-000000000099", kind: "note", summary: "Reviewed landlord follow-up" } })] });
  assert.equal(response.status, 400);
  response = await post(r.handler, "/capture/candidates", { session_token: token,
    idempotency_key: "post-legacy-activity", items: [candidate()] });
  assert.equal(response.status, 200);
  assert.equal(db.candidates.length, 1);
  assert.equal(db.candidates[0].payload.ref, ids.deal);
  assert.equal(db.candidates[0].payload.kind, "note");
});

test("resolve accept executes once as confirmer; reject only skips; double resolve is inert", async () => {
  const { db, r, token } = await claimed();
  await post(r.handler, "/capture/candidates", { session_token: token, idempotency_key: "batch-resolve", items: [
    { kind: "activity", payload: { ref: "Deal Alpha", kind: "call", summary: "Called client" }, evidence_quote: "Call went well", confidence: 0.99 },
    { kind: "activity", payload: { ref: "Deal Alpha", kind: "call", summary: "Ignore" }, evidence_quote: "Not relevant", confidence: 0.2 },
  ] });
  const [accepted, rejected] = db.candidates;
  const result = await TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-1", candidate_id: accepted.id, accept: true });
  assert.equal(result.status, "confirmed");
  assert.equal(result.ref, db.activities[0].id);
  assert.equal(db.activities.length, 1);
  assert.equal(db.activities[0].actor_id, ids.joe);
  assert.equal(db.events[0].actor_id, ids.joe);
  assert.equal(accepted.resulting_ref, db.activities[0].id);

  const skipped = await TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-2", candidate_id: rejected.id, accept: false });
  assert.equal(skipped.status, "skipped");
  assert.equal(db.activities.length, 1);
  const again = await TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-3", candidate_id: accepted.id, accept: false });
  assert.equal(again.already, "confirmed");
  assert.equal(db.activities.length, 1);
});

test("inner failure remains pending and meeting record appears only after a real write", async () => {
  const { db, r, token } = await claimed();
  await post(r.handler, "/capture/candidates", { session_token: token, idempotency_key: "batch-meeting", items: [
    { kind: "meeting_record", payload: { ref: "Deal Alpha", summary: "Lease strategy meeting", detail: "Agreed to review options." },
      evidence_quote: "Review both lease options", confidence: 0.8 },
  ] });
  const candidate = db.candidates[0];
  const poll = () => r.handler.fetch(new Request("https://worker.test/capture/session",
    { headers: { authorization: `Bearer ${token}` } }), {});
  const initial = await (await poll()).json();
  assert.equal(initial.meeting_record, null);
  assert.deepEqual(initial.candidate_statuses, [{ id: candidate.id, kind: "meeting_record",
    status: "pending", resulting_ref: null, source: "legacy" }]);

  db.failActivity = true;
  await assert.rejects(TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-fail", candidate_id: candidate.id, accept: true }),
  error => error instanceof ToolError && error.payload.error === "inner_write_failed");
  assert.equal(candidate.status, "pending");
  assert.equal(candidate.resulting_ref, null);
  assert.equal((await (await poll()).json()).meeting_record, null);

  db.failActivity = false;
  const landed = await TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-success", candidate_id: candidate.id, accept: true });
  assert.equal(db.activities[0].kind, "meeting");
  assert.equal(landed.ref, db.activities[0].id);
  const final = await (await poll()).json();
  assert.equal(final.meeting_record, db.activities[0].id);
  assert.equal(final.candidate_statuses[0].resulting_ref, db.activities[0].id);
});

test("capture SQL casts every temporal response and the contract has no transcript field", async () => {
  const source = await import("node:fs/promises").then(fs => fs.readFile(new URL("../src/capture.js", import.meta.url), "utf8"));
  assert.match(source, /to_jsonb\(state_at\)#>>'\{\}' as state_at/g);
  const dealroom = await import("node:fs/promises").then(fs => fs.readFile(new URL("../src/dealroom.js", import.meta.url), "utf8"));
  assert.match(dealroom, /to_jsonb\(started_at\)#>>'\{\}' as started_at/);
  assert.match(dealroom, /to_jsonb\(state_at\)#>>'\{\}' as state_at/);
  const tools = await import("node:fs/promises").then(fs => fs.readFile(new URL("../src/tools.js", import.meta.url), "utf8"));
  assert.match(tools, /to_jsonb\(created_at\)#>>'\{\}' as created_at/);
  const migration = await import("node:fs/promises").then(fs => readMigration(new URL("../../migrations/0081_capture_bridge.sql", import.meta.url)));
  assert.doesNotMatch(migration, /^\s*transcript\w*\s+/im);
});

test("post-call context is authenticated, bounded, exact, and never searches by name", async () => {
  const db = new CaptureFake();
  const r = rig(db);
  const environment = { CAPTURE_TOKENS: '{"mac-studio":"device-secret"}' };
  const claimedResponse = await r.handler.fetch(claimRequest(validPostCallClaim), environment);
  const token = (await claimedResponse.json()).session_token;
  const noAuth = await r.handler.fetch(new Request(`https://worker.test/capture/call-context?deal_id=${ids.deal}`), {});
  assert.equal(noAuth.status, 401);
  const exact = await r.handler.fetch(new Request(
    `https://worker.test/capture/call-context?deal_id=${ids.deal}&deal_id=${ids.deal2}`,
    { headers: { authorization: `Bearer ${token}` } }), {});
  assert.equal(exact.status, 200);
  const context = await exact.json();
  assert.deepEqual(context.deals.map(deal => [deal.id, deal.name]), [[ids.deal, "Deal Alpha"], [ids.deal2, "Deal Beta"]]);
  assert.deepEqual(context.deals[0].participants[0], {
    party_id: ids.party, ref: "P-0001", name: "Dr. Alpha", email: "alpha@example.com", role: "client_contact",
  });
  const unscoped = await r.handler.fetch(new Request("https://worker.test/capture/call-context",
    { headers: { authorization: `Bearer ${token}` } }), {});
  assert.equal(unscoped.status, 400);
  const unknown = await r.handler.fetch(new Request(
    "https://worker.test/capture/call-context?deal_id=20000000-0000-0000-0000-000000000099",
    { headers: { authorization: `Bearer ${token}` } }), {});
  assert.equal(unknown.status, 400);
  const toolContext = await TOOLS["get-call-context"].handler(db, joe, { deal_ids: [ids.deal, ids.deal2] });
  assert.equal(toolContext.deals.length, 2);
  await assert.rejects(TOOLS["get-call-context"].handler(db, joe, { deal_ids: ["Deal Alpha"] }),
    error => error instanceof ToolError && error.payload.error === "invalid_call_context_deals");
});

test("post-call lifecycle cannot finalize after one acceptance and stores only safe metadata", async () => {
  const db = new CaptureFake();
  const r = rig(db);
  const environment = { CAPTURE_TOKENS: '{"mac-studio":"device-secret"}' };
  const claimedResponse = await r.handler.fetch(claimRequest(validPostCallClaim), environment);
  const token = (await claimedResponse.json()).session_token;
  const action = { kind: "assigned_action", deal_id: ids.deal, assignee: "dell",
    action: "Call Dr. Alpha Tuesday", due_on: "2026-08-11", evidence_quote: "Dell will call Tuesday", confidence: 0.9 };
  const draft = { kind: "email_draft", deal_id: ids.deal2, recipient_party_id: ids.party,
    recipient_ref: "P-0001", subject: "Next steps", body_sha256: "a".repeat(64),
    evidence_quote: "Please send the revised terms", confidence: 0.8 };
  const badBody = await post(r.handler, "/capture/post-call/candidates", { session_token: token,
    idempotency_key: "post-bad", items: [{ ...draft, email_body: "must stay local" }] });
  assert.equal(badBody.status, 400);
  const mismatched = await post(r.handler, "/capture/post-call/candidates", { session_token: token,
    idempotency_key: "post-mismatch", items: [{ ...draft, recipient_ref: "P-9999" }] });
  assert.equal(mismatched.status, 400);
  const batch = { session_token: token, idempotency_key: "post-batch", items: [action, draft] };
  let response = await post(r.handler, "/capture/post-call/candidates", batch);
  assert.equal(response.status, 200);
  await post(r.handler, "/capture/post-call/candidates", batch);
  assert.equal(db.postCallCandidates.length, 2);
  response = await post(r.handler, "/capture/post-call/candidates", { ...batch, items: [action] });
  assert.equal(response.status, 409);
  assert.equal(JSON.stringify(db.postCallCandidates).includes("email_body"), false);
  const first = db.postCallCandidates[0];
  const second = db.postCallCandidates[1];
  db.nextActions.push({ id: "existing-action", subject_id: ids.deal, owner_id: "10000000-0000-0000-0000-000000000003",
    description: "Unrelated Dell task", status: "open" });
  await assert.rejects(executeRegisteredTool(db, { ...joe, human: false }, "resolve-post-call-candidate",
    { idempotency_key: "machine-no", candidate_id: first.id, accept: true }),
  error => error instanceof ToolError && error.payload.error === "human_only");
  const acceptedAction = await TOOLS["resolve-post-call-candidate"].handler(db, joe,
    { idempotency_key: "accept-action", candidate_id: first.id, accept: true });
  assert.equal(acceptedAction.assignee, "dell");
  assert.equal(db.nextActions.some(action => action.id === "existing-action" && action.status === "open"), true);
  assert.equal(db.postCallActions.length, 1);
  const tooSoon = await post(r.handler, "/capture/status", { session_token: token, state: "done",
    at: "2026-08-08T16:00:00.000Z" });
  assert.equal(tooSoon.status, 409);
  const earlyReport = await post(r.handler, "/capture/post-call/report", { session_token: token,
    idempotency_key: "report-early", report_sha256: "b".repeat(64), candidate_count: 2 });
  assert.equal(earlyReport.status, 409);
  const acceptedDraft = await TOOLS["resolve-post-call-candidate"].handler(db, joe,
    { idempotency_key: "accept-draft", candidate_id: second.id, accept: true });
  assert.deepEqual({ local_only: acceptedDraft.local_only, send: acceptedDraft.send, ref: acceptedDraft.ref },
    { local_only: true, send: false, ref: null });
  const report = await post(r.handler, "/capture/post-call/report", { session_token: token,
    idempotency_key: "report-final", report_sha256: "b".repeat(64), candidate_count: 2 });
  assert.equal(report.status, 200);
  const reportAgain = await post(r.handler, "/capture/post-call/report", { session_token: token,
    idempotency_key: "report-final", report_sha256: "b".repeat(64), candidate_count: 2 });
  assert.equal((await reportAgain.json()).already, true);
  const done = await post(r.handler, "/capture/status", { session_token: token, state: "done",
    at: "2026-08-08T16:01:00.000Z" });
  assert.equal(done.status, 200);
  assert.equal(Object.keys(TOOLS).some(name => /send.*email|email.*send/iu.test(name)), false);
});

test("post-call actions complete and replace only for the calling partner", async () => {
  const db = new CaptureFake();
  db.postCallActions.push(
    { id: "joe-post-action", deal_id: ids.deal, owner_id: ids.joe, description: "Joe follow-up",
      due_on: "2026-08-11", status: "open", updated_at: "2026-08-08T15:00:00.000Z", completed_at: null },
    { id: "dell-post-action", deal_id: ids.deal, owner_id: ids.dell, description: "Dell follow-up",
      due_on: "2026-08-12", status: "open", updated_at: "2026-08-08T15:00:00.000Z", completed_at: null },
  );
  const completed = await TOOLS["complete-action"].handler(db, joe,
    { idempotency_key: "complete-joe-post", ref: ids.deal, outcome: "Called client" });
  assert.equal(completed.count, 1);
  assert.equal(completed.completed[0].source, "post_call_action");
  assert.equal(db.postCallActions.find(action => action.id === "joe-post-action").status, "done");
  assert.equal(db.postCallActions.find(action => action.id === "dell-post-action").status, "open");

  db.postCallActions.push({ id: "joe-replacement", deal_id: ids.deal, owner_id: ids.joe,
    description: "Old Joe task", due_on: "2026-08-13", status: "open",
    updated_at: "2026-08-08T15:00:00.000Z", completed_at: null });
  const replaced = await TOOLS["set-next-action"].handler(db, joe,
    { idempotency_key: "replace-joe-post", ref: ids.deal,
      description: "New Joe task", due_on: "2026-08-14" });
  assert.deepEqual(replaced.dropped_post_call_action_ids, ["joe-replacement"]);
  assert.equal(db.postCallActions.find(action => action.id === "joe-replacement").status, "dropped");
  assert.equal(db.postCallActions.find(action => action.id === "dell-post-action").status, "open");
  assert.equal(db.nextActions.at(-1).description, "New Joe task");
});

test("log-outreach completes only the caller's post-call action on the deal", async () => {
  const db = new CaptureFake();
  db.postCallActions.push(
    { id: "joe-outreach-action", deal_id: ids.deal, owner_id: ids.joe,
      description: "Joe call client", due_on: "2026-08-11", status: "open",
      updated_at: "2026-08-08T15:00:00.000Z", completed_at: null },
    { id: "dell-outreach-action", deal_id: ids.deal, owner_id: ids.dell,
      description: "Dell email landlord", due_on: "2026-08-12", status: "open",
      updated_at: "2026-08-08T15:00:00.000Z", completed_at: null },
  );

  const result = await TOOLS["log-outreach"].handler(db, joe, {
    idempotency_key: "outreach-completes-joe-post-call",
    ref: ids.deal,
    channel: "call",
    outcome: "connected",
    summary: "Confirmed the revised schedule",
    next_on: "2026-08-18",
    next_step: "Call client with final terms",
  });

  assert.deepEqual(result.completed_post_call_action_ids, ["joe-outreach-action"]);
  assert.equal(result.completed_action, "Joe call client");
  assert.deepEqual(result.completed_actions.map(action => [action.id, action.source]),
    [["joe-outreach-action", "post_call_action"]]);
  assert.equal(db.postCallActions.find(action => action.id === "joe-outreach-action").status, "done");
  assert.equal(db.postCallActions.find(action => action.id === "dell-outreach-action").status, "open");
  assert.equal(db.nextActions.at(-1).description, "Call client with final terms");
  assert.equal(db.events.at(-1).actor_id, ids.joe);
  assert.equal(db.events.at(-1).verb, "log-outreach");
});
