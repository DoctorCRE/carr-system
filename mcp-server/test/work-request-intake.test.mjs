import test from "node:test";
import assert from "node:assert/strict";

import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";

const ACTOR = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "test" };
const KEY = "10000000-0000-0000-0000-000000000099";
const REQUEST = { idempotency_key: KEY, situation: "collector evidence is missing", title: "Show collector evidence state",
  desired_outcome: "Operators can distinguish missing evidence.", acceptance_criteria: [{ id: "AC-1", text: "Missing evidence is visible." }] };

async function rejected(fn) {
  try { await fn(); assert.fail("expected ToolError"); }
  catch (error) { assert.ok(error instanceof ToolError); return error.payload; }
}

class IntakeFake {
  constructor() { this.calls = []; this.toolCalls = new Map(); this.noHits = false; this.personalFirst = false; this.noShared = false; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response")) {
      const row = this.toolCalls.get(params[0]);
      return { rows: row ? [row] : [] };
    }
    // Sponsor resolution moved behind a definer function in migration 0223, so
    // the read path never selects from the actor table it cannot fully read.
    if (sql.includes("retrieval_visibility_actor_id"))
      return { rows: [{ id: ACTOR.id }] };
    if (sql.includes("search_doctrine_situations") && this.noHits) return { rows: [] };
    if (sql.includes("search_doctrine_situations")) return { rows: [
      ...(this.personalFirst ? [{ section_id: "30000000-0000-0000-0000-000000000099", doc_slug: "personal", section_key: "top",
        final_score: 1, provenance: { policy_id: "lexical-dominant-v1" } }] : []),
      { section_id: "30000000-0000-0000-0000-000000000001",
        doc_slug: "control-room", section_key: "evidence", final_score: 0.95,
        provenance: { policy_id: "lexical-dominant-v1" } },
    ] };
    if (sql.includes("work-request-intake:highest-shared-source")) {
      if (this.noShared) return { rows: [] };
      return { rows: [{ section_id: "30000000-0000-0000-0000-000000000001",
        current_revision_id: "40000000-0000-0000-0000-000000000001" }] };
    }
    if (sql.includes("capture_sourced_work_request")) return { rows: [{
      id: "20000000-0000-0000-0000-000000000001", ref: "WR-0001", state: "captured", version: 1,
      doctrine_source_label: "control-room#evidence", replayed: false,
      source_provenance: { policy_id: "lexical-dominant-v1" },
    }] };
    if (sql.includes("acting-identity")) return { rows: [] };
    if (sql.includes("work_request_card")) return { rows: [{
      ref: "WR-0001", state: "captured", version: 1, title: REQUEST.title,
      desired_outcome: REQUEST.desired_outcome, acceptance_criteria: REQUEST.acceptance_criteria,
      source_current: true, doctrine_source_label: "control-room#evidence",
      doctrine_section_id: "30000000-0000-0000-0000-000000000001",
      doctrine_revision_id: "40000000-0000-0000-0000-000000000001",
    }] };
    if (sql.includes("work-request-intake:current")) return { rows: [{
      ref: "WR-0002", title: "Review source freshness", state: "triaged",
      source_label: "control-room#evidence", source_freshness: "current",
      next_human_action: "Prepare or review bounded plan",
    }] };
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null });
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }
}

test("report-problem is registered as a narrow additive write and work-request-card is a read", () => {
  assert.equal(TOOLS["report-problem"].write, true);
  assert.equal(TOOLS["report-problem"].humanOnly, undefined);
  assert.equal(TOOLS["work-request-card"].write, false);
  assert.deepEqual(TOOLS["report-problem"].inputSchema.required,
    ["idempotency_key", "situation", "title", "desired_outcome", "acceptance_criteria"]);
  assert.equal(TOOLS["report-problem"].inputSchema.additionalProperties, false);
  assert.equal(TOOLS["work-request-card"].inputSchema.additionalProperties, false);
  assert.match(TOOLS["work-request-card"].inputSchema.properties.work_request.pattern, /^\^WR-/);
  assert.equal(TOOLS["current-work-requests"].write, false);
  assert.deepEqual(TOOLS["current-work-requests"].inputSchema, { type: "object", additionalProperties: false, properties: {} });
});

test("current-work-requests derives tenant server-side and returns only the typed bounded projection", async () => {
  const db = new IntakeFake();
  const result = await executeRegisteredTool(db, { ...ACTOR }, "current-work-requests", {});
  assert.deepEqual(result, { ok: true, items: [{ human_ref: "WR-0002", title: "Review source freshness", state: "triaged", source: { label: "control-room#evidence", freshness: "current" }, next_human_action: "Prepare or review bounded plan" }] });
  const read = db.calls.find(call => call.sql.includes("work-request-intake:current"));
  assert.deepEqual(read.params, ["carr-internal"]);
  const rejectedArgs = await rejected(() => executeRegisteredTool(new IntakeFake(), { ...ACTOR }, "current-work-requests", { state: "ready" }));
  assert.ok(["invalid_current_work_requests_fields", "unregistered_operation_fields"].includes(rejectedArgs.error));
});

test("report-problem retrieves deterministically then captures exactly the top current visible source", async () => {
  const db = new IntakeFake();
  const result = await executeRegisteredTool(db, { ...ACTOR }, "report-problem", structuredClone(REQUEST));
  assert.equal(result.human_ref, "WR-0001");
  assert.equal(result.state, "captured");
  assert.equal(result.version, 1);
  const retrieval = db.calls.find(call => call.sql.includes("search_doctrine_situations"));
  const capture = db.calls.find(call => call.sql.includes("capture_sourced_work_request"));
  assert.ok(retrieval);
  assert.ok(capture);
  assert.ok(db.calls.indexOf(retrieval) < db.calls.indexOf(capture));
  assert.equal(capture.params.includes("30000000-0000-0000-0000-000000000001"), true);
  assert.equal(capture.params.includes("40000000-0000-0000-0000-000000000001"), true);
  assert.equal(capture.params.includes(ACTOR.id), false, "actor attribution stays in the server audit envelope, not a spoofable SQL parameter");
  assert.equal(capture.params.includes("doctrine:control-room#evidence"), true, "origin is retrieval-derived");
  assert.equal(capture.params.includes(REQUEST.situation), false, "raw situation is never persisted");
  assert.match(capture.sql, /\$7::uuid/);
  const event = db.calls.find(call => call.sql.startsWith("insert into event"));
  assert.equal(JSON.parse(event.params[7]).source_ref, "doctrine:control-room#evidence");
});

test("report-problem refuses no-hit and bounded or injection-shaped input before any capture", async () => {
  const noHit = new IntakeFake();
  noHit.noHits = true;
  const noHitOut = await rejected(() => executeRegisteredTool(noHit, { ...ACTOR }, "report-problem", structuredClone(REQUEST)));
  assert.equal(noHitOut.error, "current_situation_source_not_found");
  assert.equal(noHit.calls.some(call => call.sql.includes("capture_sourced_work_request")), false);
  assert.equal(noHit.calls.some(call => call.sql.startsWith("insert into event")), false);

  for (const args of [
    { ...REQUEST, idempotency_key: "not-a-uuid" },
    { ...REQUEST, title: "x".repeat(201) },
    { ...REQUEST, acceptance_criteria: [{ id: "not-valid", text: "a" }] },
    { ...REQUEST, acceptance_criteria: [{ id: "AC-1", text: "a" }, { id: "AC-1", text: "b" }] },
  ]) {
    const db = new IntakeFake();
    const out = await rejected(() => executeRegisteredTool(db, { ...ACTOR }, "report-problem", args));
    assert.ok(["invalid_report_problem", "invalid_acceptance_criteria"].includes(out.error));
    assert.equal(db.calls.length, 0);
  }

  const injected = new IntakeFake();
  const result = await executeRegisteredTool(injected, { ...ACTOR }, "report-problem", { ...REQUEST, situation: "x'); drop table ops.work_request; --" });
  assert.equal(result.human_ref, "WR-0001");
  const retrieval = injected.calls.find(call => call.sql.includes("search_doctrine_situations"));
  assert.equal(retrieval.params[0], "x'); drop table ops.work_request; --");
  assert.equal(injected.calls.some(call => call.sql.includes("drop table")), false);
});

test("report-problem same-key replay returns the stored result without retrieval or capture", async () => {
  const db = new IntakeFake();
  const first = await executeRegisteredTool(db, { ...ACTOR }, "report-problem", structuredClone(REQUEST));
  const before = db.calls.length;
  const replay = await executeRegisteredTool(db, { ...ACTOR }, "report-problem", structuredClone(REQUEST));
  assert.equal(replay.replayed, true);
  assert.equal(replay.human_ref, first.human_ref);
  const later = db.calls.slice(before);
  assert.equal(later.some(call => call.sql.includes("search_doctrine_situations") || call.sql.includes("capture_sourced_work_request")), false);
});

test("report-problem replay key is bound to the authenticated actor", async () => {
  const db = new IntakeFake();
  await executeRegisteredTool(db, { ...ACTOR }, "report-problem", structuredClone(REQUEST));
  const other = { ...ACTOR, id: "10000000-0000-0000-0000-000000000003", slug: "dell" };
  const out = await rejected(() => executeRegisteredTool(db, other, "report-problem", structuredClone(REQUEST)));
  assert.equal(out.error, "key_reuse");
  assert.equal(db.calls.filter(call => call.sql.includes("capture_sourced_work_request")).length, 1);
});

test("mutation replay key is bound to operation and server-derived principal context", async () => {
  const db = new IntakeFake();
  const firstActor = { ...ACTOR, client_id: "client:verified-a", authorization_class: "interactive" };
  await executeRegisteredTool(db, firstActor, "report-problem", structuredClone(REQUEST));

  for (const actor of [
    { ...firstActor, client_id: "client:verified-b" },
    { ...firstActor, via: "different-auth-adapter" },
    { ...firstActor, authorization_class: "different-class" },
    { ...firstActor, human: false, sponsoring_human_slug: "joe" },
  ]) {
    const out = await rejected(() => executeRegisteredTool(db, actor, "report-problem", structuredClone(REQUEST)));
    assert.equal(out.error, "key_reuse");
  }

  const crossVerb = await rejected(() => executeRegisteredTool(db, firstActor, "review-and-triage", {
    idempotency_key: KEY, human_ref: "WR-1", base_version: 1, classification: "operational",
  }));
  assert.equal(crossVerb.error, "key_reuse");
  assert.equal(db.calls.filter(call => call.sql.includes("capture_sourced_work_request")).length, 1);
  assert.equal(db.calls.some(call => call.sql.includes("triage_sourced_work_request")), false);
});

test("report-problem skips a higher-ranked personal hit and refuses when no shared source remains", async () => {
  const shared = new IntakeFake();
  shared.personalFirst = true;
  await executeRegisteredTool(shared, { ...ACTOR }, "report-problem", structuredClone(REQUEST));
  const sourceQuery = shared.calls.find(call => call.sql.includes("highest-shared-source"));
  assert.deepEqual(sourceQuery.params[0], ["30000000-0000-0000-0000-000000000099", "30000000-0000-0000-0000-000000000001"]);
  const capture = shared.calls.find(call => call.sql.includes("capture_sourced_work_request"));
  assert.equal(capture.params.includes("doctrine:control-room#evidence"), true);
  assert.equal(capture.params.includes("doctrine:personal#top"), false);

  const none = new IntakeFake();
  none.noShared = true;
  const out = await rejected(() => executeRegisteredTool(none, { ...ACTOR }, "report-problem", structuredClone(REQUEST)));
  assert.equal(out.error, "current_situation_source_not_found");
  assert.equal(none.calls.some(call => call.sql.includes("capture_sourced_work_request") || call.sql.startsWith("insert into event")), false);
});

test("work-request-card marks stale source evidence without creating an action", async () => {
  const db = new IntakeFake();
  const original = db.query.bind(db);
  db.query = async (sql, params) => {
    const result = await original(sql, params);
    if (String(sql).includes("work_request_card")) result.rows[0].source_current = false;
    return result;
  };
  const result = await executeRegisteredTool(db, { ...ACTOR }, "work-request-card", { work_request: "WR-0001" });
  assert.equal(result.source.freshness, "stale");
  assert.deepEqual(result.actions, []);
  assert.deepEqual(result.next_human_action, { label: "Review and triage", effect: "none" });
});

test("report-problem refuses caller source, identity, and state fields before any database call", async () => {
  for (const extra of [{ source_id: "secret" }, { state: "triaged" }, { actor: "other" }, { tenant: "other" }]) {
    const db = new IntakeFake();
    const out = await rejected(() => executeRegisteredTool(db, { ...ACTOR }, "report-problem", { ...REQUEST, ...extra }));
    assert.ok(["invalid_report_problem_fields", "caller_authority_field_forbidden", "unregistered_operation_fields"].includes(out.error));
    assert.equal(db.calls.length, 0);
  }
});

test("work-request-card returns a safe captured projection with no executable action", async () => {
  const db = new IntakeFake();
  const result = await executeRegisteredTool(db, { ...ACTOR }, "work-request-card", { work_request: "WR-0001" });
  assert.equal(result.human_ref, "WR-0001");
  assert.equal(result.state, "captured");
  assert.equal(result.triage, null);
  assert.equal(result.source.freshness, "current");
  assert.equal(result.source.provenance.doctrine_revision_id, "40000000-0000-0000-0000-000000000001");
  assert.deepEqual(result.next_human_action, { label: "Review and triage", effect: "none" });
  assert.deepEqual(result.actions, []);
  const read = db.calls.find(call => call.sql.includes("work_request_card"));
  assert.equal(read.params.includes(ACTOR.id), false);
  assert.equal(read.params.includes("carr-internal"), true);
});

test("work-request-card keeps a triaged request queued and returns only durable triage readback", async () => {
  const db = new IntakeFake();
  const original = db.query.bind(db);
  db.query = async (sql, params) => {
    const result = await original(sql, params);
    if (String(sql).includes("work_request_card")) Object.assign(result.rows[0], {
      state: "triaged", triage_classification: "operational", triaged_by_actor_slug: "joe", triaged_at: "2026-08-16T00:00:00Z",
    });
    return result;
  };
  const result = await executeRegisteredTool(db, { ...ACTOR }, "work-request-card", { work_request: "WR-0001" });
  assert.equal(result.projection_state, "queued");
  assert.deepEqual(result.triage, { classification: "operational", human_actor_slug: "joe", triaged_at: "2026-08-16T00:00:00Z" });
  assert.deepEqual(result.next_human_action, { label: "Prepare scope and acceptance", effect: "none" });
  assert.deepEqual(result.actions, []);
});

test("work-request-card retains triage readback when a ready request remains queued", async () => {
  const db = new IntakeFake(); const original = db.query.bind(db);
  db.query = async (sql, params) => { if (String(sql).includes("pending_sourced_work_request_outcome_feedback")) return { rows: [] }; const result = await original(sql, params); if (String(sql).includes("work_request_card")) Object.assign(result.rows[0], {
    state: "ready", triage_classification: "operational", triaged_by_actor_slug: "joe", triaged_at: "2026-08-16T00:00:00Z",
    plan_ref: "PLAN-000001", plan_hash: "sha256:" + "a".repeat(64),
    scope_summary: "Inspect evidence and record a bounded result",
    runbook_ref: "doctrine:runbook#safe-plan", accepted_by_actor_slug: "joe",
    accepted_at: "2026-08-16T01:00:00Z"
  }); return result; };
  const card = await executeRegisteredTool(db, { ...ACTOR }, "work-request-card", { work_request: "WR-0001" });
  assert.equal(card.projection_state, "queued"); assert.equal(card.triage.classification, "operational");
  assert.equal(card.plan.plan_ref, "PLAN-000001");
  assert.equal(card.plan.scope_summary, "Inspect evidence and record a bounded result");
  assert.equal(card.plan.accepted_by_actor_slug, "joe");
  assert.equal(card.plan.accepted_at, "2026-08-16T01:00:00Z");
  assert.deepEqual(card.next_human_action, { label: "Plan accepted", effect: "none" }); assert.deepEqual(card.actions, []);
});

// ---- Jev rerank trial seam (CARR_JEV_RERANK_MODE, default off) ----

const SECOND = { section_id: "30000000-0000-0000-0000-000000000002", doc_slug: "neon-database-sop",
  section_key: "06-credentials-and-incidents", content_class: "sop",
  title: "6. Credentials and incidents", snippet: "rotation is four calls, not one", final_score: 0.4,
  provenance: { policy_id: "lexical-dominant-v1" } };

function rerankFake({ personalFirst = false } = {}) {
  const db = new IntakeFake();
  db.personalFirst = personalFirst;
  const original = db.query.bind(db);
  db.query = async (text, params = []) => {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.includes("search_doctrine_situations")) {
      const result = await original(text, params);
      result.rows.push(SECOND);
      return result;
    }
    if (sql.includes("work-request-intake:shared-shortlist")) {
      db.calls.push({ sql, params });
      // The database, not the caller, decides eligibility: the personal hit
      // never comes back from this query.
      return { rows: [
        { section_id: "30000000-0000-0000-0000-000000000001", current_revision_id: "40000000-0000-0000-0000-000000000001" },
        { section_id: SECOND.section_id, current_revision_id: "40000000-0000-0000-0000-000000000002" },
      ] };
    }
    return original(text, params);
  };
  return db;
}

function jevPrefers(title, { requests = [] } = {}) {
  return async request => {
    requests.push(structuredClone(request));
    const answers = Object.fromEntries(Object.entries(request.questions).map(([k, q]) =>
      [k, { type: "noul", noul: q.instructions.includes(`"${title}`) ? 0.92 : 0.1 }]));
    return { model: "jev-test", answers, usage: { input_tokens: 300, output_tokens: 2 } };
  };
}

test("report-problem with the rerank flag off is byte-for-byte the deterministic path", async () => {
  for (const jevRerank of [undefined, { posture: { enabled: false, mode: "off" }, ask: jevPrefers(SECOND.title) },
    { posture: { enabled: false, mode: "off", posture: "misconfigured" }, ask: jevPrefers(SECOND.title) }]) {
    const db = rerankFake();
    if (jevRerank) db.jevRerank = jevRerank;
    const result = await executeRegisteredTool(db, { ...ACTOR }, "report-problem", structuredClone(REQUEST));
    assert.equal(result.source_selection, undefined);
    assert.equal(db.calls.some(call => call.sql.includes("shared-shortlist")), false);
    const capture = db.calls.find(call => call.sql.includes("capture_sourced_work_request"));
    assert.equal(capture.params[4], "30000000-0000-0000-0000-000000000001");
    assert.equal(capture.params[5], "40000000-0000-0000-0000-000000000001");
  }
});

test("report-problem never calls optional Jev from its write envelope", async () => {
  const db = rerankFake();
  let asked = 0;
  db.jevRerank = { posture: { enabled: true, mode: "noul" }, ask: async () => { asked += 1; throw new Error("outside call"); } };
  const result = await executeRegisteredTool(db, { ...ACTOR }, "report-problem", structuredClone(REQUEST));
  assert.equal(asked, 0);
  assert.equal(result.source_selection, undefined);
  const capture = db.calls.find(call => call.sql.includes("capture_sourced_work_request"));
  assert.equal(capture.params[4], "30000000-0000-0000-0000-000000000001");
});
