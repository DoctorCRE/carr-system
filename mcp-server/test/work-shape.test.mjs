import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { TOOLS } from "../src/tools.js";
import { capabilityProgramTools } from "../src/capability-program.js";
import { implementationShapeError, shapeDecisionError, shapeDispositionError, workShapeTools } from "../src/work-shape.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const MIGRATION = path.join(REPO, "migrations/0132_work_shape_revision.sql");
const SOURCED_SHAPE_MIGRATION = path.join(REPO, "migrations/0306_sourced_work_shape_disposition.sql");
const FORWARD_CORRECTION_MIGRATION = path.join(REPO, "migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql");

const SOURCED_LINEAGE = Object.freeze({
  status: "corrected",
  effective: { receipt_kind: "correction", receipt_id: "77777777-7777-4777-8777-777777777777", disposition: "required", fixed_surface_ref: null, rationale: "Heavy work needs a Shape.", decided_by_actor_id: "11111111-1111-4111-8111-111111111111", decided_at: "2026-09-08T00:00:00Z", base_version: 3, result_version: 4 },
  original: { receipt_id: "66666666-6666-4666-8666-666666666666", disposition: "not_required", fixed_surface_ref: "safe:fixed", rationale: "Mistaken.", decided_by_actor_id: "11111111-1111-4111-8111-111111111111", decided_at: "2026-09-07T00:00:00Z", base_version: 2, result_version: 3 },
  correction: { receipt_id: "77777777-7777-4777-8777-777777777777", original_receipt_id: "66666666-6666-4666-8666-666666666666", disposition: "required", fixed_surface_ref: null, rationale: "Heavy work needs a Shape.", decided_by_actor_id: "11111111-1111-4111-8111-111111111111", decided_at: "2026-09-08T00:00:00Z", base_version: 3, result_version: 4 },
  backs_current_version: true,
});

// A recording fake for the sourced/unsourced disposition branches. `classify`
// is the classifier wrapper's row list (undefined = the wrapper must not be
// called); `setter` is the sourced setter's returned row.
function dispositionFake(work, { classify, setter, lineage } = {}) {
  const calls = [];
  const db = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("from ops.work_request") && sql.includes("for update")) return { rows: [work] };
    if (sql.includes("classify_sourced_work_request_build")) {
      if (classify === undefined) throw new Error("classifier wrapper must not be consulted on this branch");
      return { rows: classify };
    }
    if (sql.includes("set_sourced_work_request_shape_disposition")) {
      if (!setter) throw new Error("sourced setter must not be reached on this branch");
      return { rows: [setter(params)] };
    }
    if (sql.includes("read_sourced_work_request_shape_disposition_lineage")) {
      if (lineage === undefined) throw new Error("sourced lineage projection must not be read on this branch");
      return { rows: [{ lineage }] };
    }
    if (sql.includes("update ops.work_request set shape_disposition")) {
      if (work.capture_idempotency_key) throw new Error("a sourced request must never take the direct update");
      return { rows: [{ ...work, version: Number(work.version) + 1, shape_disposition: params[1], shape_fixed_surface_ref: params[2], shape_rationale: params[3], shape_decided_by_actor_id: params[4], shape_decided_at: "now" }] };
    }
    throw new Error(`unexpected query: ${sql}`);
  }};
  const events = [];
  const tools = workShapeTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async (...args) => events.push(args), ToolError });
  return { db, calls, events, tools };
}
const SOURCED_WORK = Object.freeze({
  id: "22222222-2222-4222-8222-222222222222", ref: "WR-000063", title: "Heavy sourced request", state: "triaged", version: 3,
  capture_idempotency_key: "33333333-3333-4333-8333-333333333333", shape_disposition: null, shape_fixed_surface_ref: null, shape_rationale: null,
});
const settled = params => ({ id: SOURCED_WORK.id, ref: SOURCED_WORK.ref, title: SOURCED_WORK.title, state: "triaged", version: Number(params[1]) + 1,
  shape_disposition: params[2], shape_fixed_surface_ref: params[3], shape_rationale: params[4], shape_decided_by_actor_id: params[5], shape_decided_at: "2026-09-08T00:00:00Z", replayed: false });

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const actor = { id: "11111111-1111-4111-8111-111111111111", slug: "codex", human: false };

function validShape(overrides = {}) {
  return {
    trinity: {
      workflow_trigger: "A bounded engineering request reaches build readiness",
      output_user: "The accountable requester and the implementing agent",
      runtime: "Cloudflare Worker plus Postgres",
    },
    hidden_assumption: "The work is best expressed as a durable request-linked record rather than a dashboard.",
    repo_searches: ["durable agent approval", "work request decision provenance"],
    maintained_repos: [
      { url: "https://github.com/openai/openai-agents-python", maintenance_evidence: "recent releases and commits" },
      { url: "https://github.com/cloudflare/agents", maintenance_evidence: "recent commits" },
      { url: "https://github.com/langchain-ai/langgraph", maintenance_evidence: "active releases" },
      { url: "https://github.com/microsoft/agent-framework", maintenance_evidence: "active development" },
      { url: "https://github.com/dlt-hub/dlt", maintenance_evidence: "active releases" },
    ],
    archetypes: [
      { key: "workspace", label: "Workspace-first UI", core_assumption: "Work starts when a person opens a screen", scores: { trinity_fit: 2, useful_v1_effort: 2, extension_effort: 4 } },
      { key: "lane", label: "Governed work lane", core_assumption: "Work starts as a bounded request that must survive offline actors", scores: { trinity_fit: 5, useful_v1_effort: 3, extension_effort: 2 } },
      { key: "scheduled", label: "Scheduled ingestion lane", core_assumption: "Most useful work arrives as recurring external deltas", scores: { trinity_fit: 3, useful_v1_effort: 3, extension_effort: 3 } },
    ],
    chosen_key: "lane",
    mind_changing_fact: "Choose scheduled ingestion first if measured external deltas dominate bounded requests.",
    builder_brief: {
      chosen_shape: "A governed work lane linked to the canonical Work Request",
      repo_url: "https://github.com/openai/openai-agents-python",
      trinity: {
        workflow_trigger: "A bounded engineering request reaches build readiness",
        output_user: "The accountable requester and the implementing agent",
        runtime: "Cloudflare Worker plus Postgres",
      },
      must_have_integrations: ["ops.work_request", "event", "tool_call"],
      v1_non_goals: ["generic workflow engine", "browser execution", "autonomous approval"],
      text: "Build a governed work lane around the canonical Work Request. Trigger it when a bounded request reaches build readiness and its implementation surface is still open. The accountable requester consumes the outcome while an attributed agent may implement it. Run the lane in the existing Cloudflare Worker and Postgres record layer. Persist the trinity, evidence-backed alternatives, chosen shape, and the fact that would reverse the choice. Integrate with ops.work_request, event, and tool_call so revisions are attributable and stale writes conflict. Use the OpenAI Agents approval and resume pattern only as a behavioral reference. Keep version one narrow: no generic workflow engine, no browser execution, no autonomous approval, no copied business payload, and no new model-specific authority.",
    },
    source_url: "https://x.com/nurijanian/status/2088524098549018944",
    ...overrides,
  };
}

test("a complete, evidence-backed shape decision passes", () => {
  assert.equal(shapeDecisionError(validShape()), null);
});

test("shape validation refuses fake variety, thin recon, invalid scores, and loose briefs", () => {
  const base = validShape();
  assert.equal(shapeDecisionError({ ...base, repo_searches: ["one"] }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, maintained_repos: base.maintained_repos.slice(0, 4) }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, maintained_repos: base.maintained_repos.map(repo => ({ ...repo, url: base.maintained_repos[0].url })) }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, archetypes: [base.archetypes[0], base.archetypes[0], base.archetypes[2]] }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, archetypes: base.archetypes.map((x, i) => i ? x : { ...x, scores: { ...x.scores, trinity_fit: 6 } }) }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, chosen_key: "missing" }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, builder_brief: { ...base.builder_brief, repo_url: "https://github.com/example/not-researched" } }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, builder_brief: { ...base.builder_brief, trinity: { ...base.trinity, runtime: "Browser memory" } } }).error, "work_shape_invalid");
  assert.equal(shapeDecisionError({ ...base, builder_brief: { ...base.builder_brief, text: "too short" } }).error, "work_shape_invalid");
});

test("the registry exposes a model-agnostic append and read surface", () => {
  assert.equal(TOOLS["read-work-shape"].write, false);
  assert.equal(TOOLS["write-work-shape"].write, true);
  assert.equal(TOOLS["set-work-shape-disposition"].write, true);
  assert.equal(TOOLS["read-work-shape"].fullOnly, true);
  assert.notEqual(TOOLS["write-work-shape"].humanOnly, true);
  assert.notEqual(TOOLS["set-work-shape-disposition"].humanOnly, true);
  assert.equal(TOOLS["write-work-shape"].inputSchema.required.includes("base_version"), true);
  assert.equal(TOOLS["write-work-shape"].inputSchema.required.includes("work_request_base_version"), true);
  assert.equal(TOOLS["write-work-shape"].inputSchema.required.includes("idempotency_key"), true);
  assert.equal(TOOLS["write-work-shape"].inputSchema.additionalProperties, false);
  const routing = fs.readFileSync(path.join(REPO, "mcp-server/src/mcp.js"), "utf8");
  assert.match(routing, /if \(tool\.fullOnly\) return false;[\s\S]*if \(!tool\.write\) return true;/,
    "fullOnly operational reads must be refused before the general read-profile allowance");
});

test("write-work-shape appends one revision and refuses a stale base version", async () => {
  const work = { id: "22222222-2222-4222-8222-222222222222", ref: "WR-TEST-1", title: "Shape test", state: "ready", version: 7, shape_disposition: "required", shape_rationale: "Requirements leave multiple viable surfaces." };
  const existing = { id: "33333333-3333-4333-8333-333333333333", work_request_id: work.id, work_request_version: 7, version: 2, ...validShape() };
  const inserts = [];
  const db = { query: async (sql, params = []) => {
    if (sql.includes("from ops.work_request") && sql.includes("for update")) return { rows: [work] };
    if (sql.includes("from ops.work_shape_revision") && sql.includes("limit 1")) return { rows: [existing] };
    if (sql.includes("insert into ops.work_shape_revision")) {
      inserts.push(params);
      return { rows: [{ ...existing, id: "44444444-4444-4444-8444-444444444444", version: 3 }] };
    }
    throw new Error(`unexpected query: ${sql}`);
  }};
  const events = [];
  const tools = workShapeTools({
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    writeEvent: async (...args) => events.push(args),
    ToolError,
  });

  await assert.rejects(
    tools["write-work-shape"].handler(db, actor, { idempotency_key: "stale", work_request: work.ref, base_version: 1, work_request_base_version: 7, ...validShape() }),
    error => error instanceof ToolError && error.payload.error === "version_conflict" && error.payload.current_version === 2,
  );
  assert.equal(inserts.length, 0);

  await assert.rejects(
    tools["write-work-shape"].handler(db, actor, { idempotency_key: "stale-work", work_request: work.ref, base_version: 2, work_request_base_version: 6, ...validShape() }),
    error => error instanceof ToolError && error.payload.error === "work_request_version_conflict" && error.payload.current_version === 7,
  );
  const result = await tools["write-work-shape"].handler(db, actor, { idempotency_key: "fresh", work_request: work.ref, base_version: 2, work_request_base_version: 7, ...validShape() });
  assert.equal(result.shape.version, 3);
  assert.equal(result.shape.work_request_version, 7);
  assert.equal(inserts.length, 1);
  assert.equal(events.length, 1);
  assert.equal(events[0][2], "write-work-shape");
  assert.equal(events[0][3], "ops_work_request");
});

test("shape disposition is explicit and not_required must cite the fixed surface", () => {
  assert.equal(shapeDispositionError({ disposition: "required", rationale: "The surface is open." }), null);
  assert.equal(shapeDispositionError({ disposition: "not_required", fixed_surface_ref: "mcp-server/src/tools.js#verb", rationale: "The request explicitly extends this verb." }), null);
  assert.equal(shapeDispositionError({ disposition: "not_required", rationale: "Already fixed." }).error, "work_shape_disposition_invalid");
  assert.equal(shapeDispositionError({ disposition: "required", fixed_surface_ref: "somewhere", rationale: "Open." }).error, "work_shape_disposition_invalid");
  assert.equal(shapeDispositionError({ rationale: "Unknown." }).error, "work_shape_disposition_invalid");
});

test("set-work-shape-disposition uses Work Request optimistic locking and freezes after claim", async () => {
  const base = { id: "22222222-2222-4222-8222-222222222222", ref: "WR-TEST-2", title: "Disposition test", state: "triaged", version: 3, shape_disposition: null };
  const updates = [];
  const db = { query: async (sql, params = []) => {
    if (sql.includes("from ops.work_request") && sql.includes("for update")) return { rows: [base] };
    if (sql.includes("update ops.work_request set shape_disposition")) {
      updates.push(params);
      return { rows: [{ ...base, version: 4, shape_disposition: "not_required", shape_fixed_surface_ref: params[2], shape_rationale: params[3], shape_decided_by_actor_id: actor.id, shape_decided_at: "now" }] };
    }
    throw new Error(`unexpected query: ${sql}`);
  }};
  const events = [];
  const tools = workShapeTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async (...args) => events.push(args), ToolError });
  await assert.rejects(
    tools["set-work-shape-disposition"].handler(db, actor, { idempotency_key: "stale", work_request: base.ref, base_version: 2, disposition: "required", rationale: "Open surface." }),
    error => error instanceof ToolError && error.payload.error === "version_conflict" && error.payload.current_version === 3,
  );
  const result = await tools["set-work-shape-disposition"].handler(db, actor, { idempotency_key: "fresh", work_request: base.ref, base_version: 3, disposition: "not_required", fixed_surface_ref: "mcp-server/src/tools.js#verb", rationale: "This request explicitly extends the existing verb." });
  assert.equal(result.work_request.version, 4);
  assert.equal(result.work_request.shape_disposition, "not_required");
  assert.equal(updates.length, 1);
  assert.equal(events[0][2], "set-work-shape-disposition");

  const frozenDb = { query: async sql => {
    if (sql.includes("from ops.work_request") && sql.includes("for update")) return { rows: [{ ...base, state: "claimed" }] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  await assert.rejects(
    tools["set-work-shape-disposition"].handler(frozenDb, actor, { idempotency_key: "frozen", work_request: base.ref, base_version: 3, disposition: "required", rationale: "Too late." }),
    error => error instanceof ToolError && error.payload.error === "work_shape_disposition_frozen",
  );
});

test("a sourced triaged disposition uses the exact receipt-backed database transition", async () => {
  const base = {
    id: "22222222-2222-4222-8222-222222222222", ref: "WR-000007", title: "Sourced disposition",
    state: "triaged", version: 2, capture_idempotency_key: "33333333-3333-4333-8333-333333333333",
    shape_disposition: null, shape_fixed_surface_ref: null, shape_rationale: null,
  };
  const calls = [];
  const db = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("from ops.work_request") && sql.includes("for update")) return { rows: [base] };
    if (sql.includes("set_sourced_work_request_shape_disposition")) return { rows: [{
      ...base, version: 3, shape_disposition: "required", shape_fixed_surface_ref: null,
      shape_rationale: "The implementation surface remains open.", shape_decided_by_actor_id: actor.id,
      shape_decided_at: "2026-08-25T00:00:00Z", replayed: false,
    }] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const events = [];
  const tools = workShapeTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async (...args) => events.push(args), ToolError });
  const result = await tools["set-work-shape-disposition"].handler(db, actor, {
    idempotency_key: "44444444-4444-4444-8444-444444444444", work_request: base.ref, base_version: 2,
    disposition: "required", rationale: "The implementation surface remains open.",
  });
  assert.equal(result.work_request.version, 3);
  assert.equal(result.work_request.shape_disposition, "required");
  const transition = calls.find(call => call.sql.includes("set_sourced_work_request_shape_disposition"));
  assert.deepEqual(transition.params, [base.ref, 2, "required", null, "The implementation surface remains open.", actor.id, "44444444-4444-4444-8444-444444444444"]);
  assert.equal(calls.some(call => call.sql.includes("update ops.work_request set shape_disposition")), false);
  assert.equal(events[0][2], "set-work-shape-disposition");
});

test("a sourced initial not_required is preflighted: heavy refuses without mutation, standard proceeds, zero rows defers to the setter", async () => {
  const args = { idempotency_key: "44444444-4444-4444-8444-444444444444", work_request: SOURCED_WORK.ref, base_version: 3, disposition: "not_required", fixed_surface_ref: "safe:fixed", rationale: "The surface is fixed." };
  const heavy = dispositionFake(SOURCED_WORK, { classify: [{ work_request_id: SOURCED_WORK.id, ref: SOURCED_WORK.ref, tier: "heavy", reasons: ["signal:new_capability"], shape_disposition: null, shape_ready: false }] });
  await assert.rejects(
    heavy.tools["set-work-shape-disposition"].handler(heavy.db, actor, args),
    error => error instanceof ToolError && error.payload.error === "heavy_build_shape_required" && error.payload.classification_reasons[0] === "signal:new_capability",
  );
  assert.equal(heavy.calls.some(call => call.sql.includes("set_sourced_work_request_shape_disposition")), false);
  assert.equal(heavy.calls.some(call => call.sql.includes("update ops.work_request")), false);
  assert.equal(heavy.events.length, 0);
  const classify = heavy.calls.find(call => call.sql.includes("classify_sourced_work_request_build"));
  assert.deepEqual(classify.params, [SOURCED_WORK.ref, 3, "", "[]", "{}"]);

  const standard = dispositionFake(SOURCED_WORK, { classify: [{ tier: "standard", reasons: [] }], setter: settled });
  const result = await standard.tools["set-work-shape-disposition"].handler(standard.db, actor, args);
  assert.equal(result.work_request.shape_disposition, "not_required");
  assert.equal(result.work_request.version, 4);
  const transition = standard.calls.find(call => call.sql.includes("set_sourced_work_request_shape_disposition"));
  assert.deepEqual(transition.params, [SOURCED_WORK.ref, 3, "not_required", "safe:fixed", "The surface is fixed.", actor.id, args.idempotency_key]);
  assert.equal(standard.events.length, 1);

  // A zero-row wrapper answer is not "standard": the sourced setter's own
  // classifier call decides, so the call still goes to the setter.
  const deferred = dispositionFake(SOURCED_WORK, { classify: [], setter: settled });
  await deferred.tools["set-work-shape-disposition"].handler(deferred.db, actor, args);
  assert.equal(deferred.calls.filter(call => call.sql.includes("set_sourced_work_request_shape_disposition")).length, 1);
});

test("a sourced initial required and a not_required to required correction never consult the classifier", async () => {
  const initial = dispositionFake(SOURCED_WORK, { setter: settled });
  const shaped = await initial.tools["set-work-shape-disposition"].handler(initial.db, actor, { idempotency_key: "55555555-5555-4555-8555-555555555555", work_request: SOURCED_WORK.ref, base_version: 3, disposition: "required", rationale: "The surface is open." });
  assert.equal(shaped.work_request.shape_disposition, "required");
  assert.equal(initial.calls.some(call => call.sql.includes("classify_sourced_work_request_build")), false);

  const mistaken = { ...SOURCED_WORK, version: 4, shape_disposition: "not_required", shape_fixed_surface_ref: "safe:fixed", shape_rationale: "Mistaken." };
  const correction = dispositionFake(mistaken, { setter: settled });
  const corrected = await correction.tools["set-work-shape-disposition"].handler(correction.db, actor, { idempotency_key: "77777777-7777-4777-8777-777777777777", work_request: mistaken.ref, base_version: 4, disposition: "required", rationale: "Heavy work needs a Shape." });
  assert.equal(corrected.work_request.shape_disposition, "required");
  assert.equal(corrected.work_request.version, 5);
  assert.equal(correction.calls.some(call => call.sql.includes("classify_sourced_work_request_build")), false);
  const transition = correction.calls.find(call => call.sql.includes("set_sourced_work_request_shape_disposition"));
  assert.deepEqual(transition.params, [mistaken.ref, 4, "required", null, "Heavy work needs a Shape.", actor.id, "77777777-7777-4777-8777-777777777777"]);
  assert.equal(correction.events[0][2], "set-work-shape-disposition");
  assert.deepEqual(correction.events[0][5].old, { disposition: "not_required", fixed_surface_ref: "safe:fixed", rationale: "Mistaken." });
  assert.deepEqual(correction.events[0][5].new, { disposition: "required", fixed_surface_ref: null, rationale: "Heavy work needs a Shape." });
});

test("unsourced initial required and not_required keep the direct update and touch no sourced seam", async () => {
  const unsourced = { id: "22222222-2222-4222-8222-222222222222", ref: "WR-TEST-9", title: "Unsourced", state: "triaged", version: 3, capture_idempotency_key: null, shape_disposition: null, shape_fixed_surface_ref: null, shape_rationale: null };
  for (const args of [
    { idempotency_key: "u1", work_request: unsourced.ref, base_version: 3, disposition: "required", rationale: "Open surface." },
    { idempotency_key: "u2", work_request: unsourced.ref, base_version: 3, disposition: "not_required", fixed_surface_ref: "mcp-server/src/tools.js#verb", rationale: "Fixed surface." },
  ]) {
    const fake = dispositionFake(unsourced);
    const result = await fake.tools["set-work-shape-disposition"].handler(fake.db, actor, args);
    assert.equal(result.work_request.version, 4);
    assert.equal(result.work_request.shape_disposition, args.disposition);
    assert.equal(fake.calls.filter(call => call.sql.includes("update ops.work_request set shape_disposition")).length, 1);
    assert.equal(fake.calls.some(call => /classify_sourced|set_sourced|read_sourced|_receipt/.test(call.sql)), false);
    assert.equal(fake.events.length, 1);
    assert.equal(fake.events[0][2], "set-work-shape-disposition");
  }
});

test("read-work-shape exposes sourced lineage through the narrow projection and null for unsourced", async () => {
  const sourced = { ...SOURCED_WORK, version: 4, shape_disposition: "required" };
  const calls = [];
  const db = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("from ops.work_request where")) return { rows: [sourced] };
    if (sql.includes("from ops.work_shape_revision")) return { rows: [] };
    if (sql.includes("read_sourced_work_request_shape_disposition_lineage")) return { rows: [{ lineage: SOURCED_LINEAGE }] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = workShapeTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const result = await tools["read-work-shape"].handler(db, actor, { work_request: sourced.ref });
  assert.deepEqual(result.disposition_lineage, SOURCED_LINEAGE);
  assert.equal(result.work_request.shape_disposition, "required");
  assert.equal(result.current, null);
  assert.match(calls[0].sql, /capture_idempotency_key/);
  assert.deepEqual(calls.find(call => call.sql.includes("read_sourced_work_request_shape_disposition_lineage")).params, [sourced.id]);
  assert.equal(calls.some(call => /_receipt/.test(call.sql)), false, "direct receipt-table reads stay denied");

  const unsourcedCalls = [];
  const unsourcedDb = { query: async (sql, params = []) => {
    unsourcedCalls.push(sql);
    if (sql.includes("from ops.work_request where")) return { rows: [{ ...sourced, capture_idempotency_key: null }] };
    if (sql.includes("from ops.work_shape_revision")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const plain = await tools["read-work-shape"].handler(unsourcedDb, actor, { work_request: sourced.ref });
  assert.equal(plain.disposition_lineage, null);
  assert.equal(plain.work_request.shape_disposition, "required");
  assert.equal(unsourcedCalls.some(sql => sql.includes("read_sourced")), false);
});

test("write-work-shape requires the effective receipt-backed required disposition for sourced requests only", async () => {
  const sourced = { ...SOURCED_WORK, version: 4, shape_disposition: "required", shape_rationale: "Heavy work needs a Shape." };
  const build = lineage => {
    const calls = [];
    const db = { query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (sql.includes("from ops.work_request") && sql.includes("for update")) return { rows: [sourced] };
      if (sql.includes("read_sourced_work_request_shape_disposition_lineage")) return { rows: [{ lineage }] };
      if (sql.includes("from ops.work_shape_revision") && sql.includes("limit 1")) return { rows: [] };
      if (sql.includes("insert into ops.work_shape_revision")) return { rows: [{ id: "44444444-4444-4444-8444-444444444444", work_request_id: sourced.id, work_request_version: 4, version: 1, ...validShape() }] };
      throw new Error(`unexpected query: ${sql}`);
    }};
    const tools = workShapeTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
    return { db, calls, tools };
  };
  const args = { idempotency_key: "s1", work_request: sourced.ref, base_version: 0, work_request_base_version: 4, ...validShape() };
  const backed = build(SOURCED_LINEAGE);
  const written = await backed.tools["write-work-shape"].handler(backed.db, actor, args);
  assert.equal(written.shape.version, 1);
  assert.match(backed.calls[0].sql, /capture_idempotency_key/);

  const stale = build({ ...SOURCED_LINEAGE, backs_current_version: false });
  await assert.rejects(
    stale.tools["write-work-shape"].handler(stale.db, actor, args),
    error => error instanceof ToolError && error.payload.error === "work_shape_not_required" && error.payload.disposition_lineage.backs_current_version === false,
  );
  assert.equal(stale.calls.some(call => call.sql.includes("insert into ops.work_shape_revision")), false);

  const unbacked = build({ status: "none", effective: null, original: null, correction: null, backs_current_version: false });
  await assert.rejects(unbacked.tools["write-work-shape"].handler(unbacked.db, actor, args),
    error => error instanceof ToolError && error.payload.error === "work_shape_not_required");
});

test("migration 0492 admits one linked forward correction behind the exact sourced setter", () => {
  const sql = fs.readFileSync(FORWARD_CORRECTION_MIGRATION, "utf8");
  assert.doesNotMatch(sql, /^\s*(begin|commit)\s*;\s*$/im, "0339+ migrations use the runner's single transaction");
  assert.match(sql, /create table ops\.sourced_work_request_shape_disposition_correction_receipt \(\n  id uuid primary key default gen_random_uuid\(\),\n  work_request_id uuid not null unique references ops\.work_request\(id\),\n  idempotency_key uuid not null unique,\n  original_receipt_id uuid not null unique references ops\.sourced_work_request_shape_disposition_receipt\(id\)/);
  assert.match(sql, /result_version integer not null check \(result_version = base_version \+ 1\)/);
  assert.match(sql, /disposition text not null check \(disposition = 'required'\)/);
  assert.match(sql, /before update or delete on ops\.sourced_work_request_shape_disposition_correction_receipt\nfor each row execute function ops\.sourced_work_shape_receipts_are_immutable\(\)/);
  assert.equal((sql.match(/create or replace function ops\.set_sourced_work_request_shape_disposition\(/g) || []).length, 1);
  assert.match(sql, /create or replace function ops\.set_sourced_work_request_shape_disposition\(\n  p_work_request text,\n  p_base_version integer,\n  p_disposition text,\n  p_fixed_surface_ref text,\n  p_rationale text,\n  p_decided_by_actor_id uuid,\n  p_idempotency_key uuid\n\)/);
  assert.match(sql, /grant execute on function ops\.set_sourced_work_request_shape_disposition\(text,integer,text,text,text,uuid,uuid\)\n  to carr_writer;/);
  assert.match(sql, /revoke all on function ops\.set_sourced_work_request_shape_disposition\(text,integer,text,text,text,uuid,uuid\)\n  from public,carr_reader,carr_jobs,carr_authority;/);
  assert.match(sql, /create or replace function ops\.effective_sourced_work_request_shape_disposition\(p_work_request ops\.work_request\)/);
  assert.match(sql, /revoke all on table ops\.sourced_work_request_shape_disposition_correction_receipt\n  from public,carr_reader,carr_writer,carr_jobs,carr_authority;/);
  assert.match(sql, /grant execute on function ops\.read_sourced_work_request_shape_disposition_lineage\(uuid\) to carr_reader,carr_writer;/);
  const setter = sql.slice(sql.indexOf("create or replace function ops.set_sourced_work_request_shape_disposition("), sql.indexOf("CREATE OR REPLACE FUNCTION ops.sourced_work_request_is_immutable()"));
  const lock = setter.indexOf("pg_advisory_xact_lock(hashtextextended('program6-sourced-shape-disposition:' || p_idempotency_key, 0))");
  const originalLookup = setter.indexOf("from ops.sourced_work_request_shape_disposition_receipt r\n   where r.idempotency_key = p_idempotency_key");
  const correctionLookup = setter.indexOf("from ops.sourced_work_request_shape_disposition_correction_receipt c\n   where c.idempotency_key = p_idempotency_key");
  const rowLock = setter.indexOf("where x.ref = p_work_request\n   for update");
  const correctionInsert = setter.indexOf("insert into ops.sourced_work_request_shape_disposition_correction_receipt");
  const classifier = setter.indexOf("ops.heavy_build_classification(w.id, '', '[]'::jsonb, '{}'::jsonb)");
  assert.ok(lock >= 0 && lock < originalLookup && originalLookup < correctionLookup && correctionLookup < rowLock && rowLock < correctionInsert);
  assert.equal((setter.match(/ops\.heavy_build_classification\(/g) || []).length, 1);
  assert.ok(classifier > correctionInsert, "the classifier refusal belongs to the initial not_required branch only");
  assert.match(setter, /if p_disposition = 'not_required' then\n      classification := ops\.heavy_build_classification/);
  assert.match(setter, /or w\.program_key is not null or w\.program_ordinal is not null then/);
  assert.match(setter, /is distinct from \(null::text,null::text,null::text,null::uuid,null::timestamptz\)\n       or exists \(select 1 from ops\.work_shape_revision sr where sr\.work_request_id = w\.id\) then/);
  assert.match(setter, /or exists \(select 1 from ops\.work_shape_revision sr where sr\.work_request_id = w\.id\)\n       or exists \(select 1 from ops\.sourced_work_request_shape_disposition_correction_receipt c/);
  // Consumers read the effective lineage, never the bare columns.
  assert.match(sql, /select 1 from ops\.effective_sourced_work_request_shape_disposition\(new\) e\n        where e\.base_version = old\.version and e\.result_version = new\.version/);
  assert.equal((sql.match(/ops\.effective_sourced_work_request_shape_disposition\(w\) e where e\.disposition='required'\)/g) || []).length, 2);
  assert.equal((sql.match(/ops\.effective_sourced_work_request_shape_disposition\(w\) e where e\.disposition='not_required'\)/g) || []).length, 2);
  assert.equal((sql.match(/select 1 from ops\.sourced_work_request_shape_disposition_lineage\(w\.id\) e/g) || []).length, 2);
  assert.match(sql, /before insert on ops\.work_shape_revision\nfor each row execute function ops\.sourced_work_shape_revision_requires_effective_required\(\)/);
  assert.match(sql, /if not found or w\.capture_idempotency_key is null\n     or not exists \(select 1 from ops\.sourced_work_request_shape_disposition_receipt r\n                     where r\.work_request_id = w\.id\) then\n    return new;/);
  assert.match(sql, /or not exists \(select 1 from ops\.effective_sourced_work_request_shape_disposition\(w\) e\n                     where e\.disposition = 'required'\) then\n    raise exception 'a sourced Work Shape revision requires the exact current receipt-backed required disposition'/);
});

test("both shape writes serialize identical idempotency keys before replay lookup", () => {
  const source = fs.readFileSync(path.join(REPO, "mcp-server/src/tools.js"), "utf8");
  const body = source.slice(source.indexOf("async function withEnvelope"), source.indexOf("async function writeEvent"));
  const lock = body.indexOf("pg_advisory_xact_lock");
  const replayRead = body.indexOf("select request_hash, response");
  assert.ok(lock >= 0 && lock < replayRead, "the same-key transaction lock must precede replay lookup");
  assert.match(body, /verb === "write-work-shape" \|\| verb === "set-work-shape-disposition"/);
});

test("an unclassified capability project cannot be claimed", async () => {
  const current = {
    id: "22222222-2222-4222-8222-222222222222", ref: "WR-AI-001", title: "Unclassified project",
    program_key: "carr-ai-engineering-suite-v1", program_ordinal: 1, state: "ready", version: 4,
    shape_disposition: null, project_context: {}, acceptance_criteria: [],
  };
  assert.equal(implementationShapeError(current, null).error, "work_shape_disposition_required");
  const db = { query: async sql => {
    if (sql.includes("select w.* from ops.work_request")) return { rows: [current] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  await assert.rejects(
    tools["start-capability-project"].handler(db, { ...actor, human: true }, {
      idempotency_key: "claim-unclassified", program_key: "carr-ai-engineering-suite-v1",
      sequence: 1, base_version: 4, executor_actor: "codex",
      source_commit_sha: "a".repeat(40), worktree_ref: "worktree:test",
    }),
    error => error instanceof ToolError && error.payload.error === "work_shape_disposition_required",
  );
});

test("an explicitly shape-required capability project cannot be claimed without a decision", async () => {
  const current = {
    id: "22222222-2222-4222-8222-222222222222", ref: "WR-AI-001", title: "Open-shape project",
    program_key: "carr-ai-engineering-suite-v1", program_ordinal: 1, state: "ready", version: 4,
    shape_disposition: "required", shape_rationale: "The surface remains open.", project_context: {}, acceptance_criteria: [],
  };
  const db = { query: async sql => {
    if (sql.includes("select w.* from ops.work_request")) return { rows: [current] };
    if (sql.includes("from ops.work_shape_revision")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  await assert.rejects(
    tools["start-capability-project"].handler(db, { ...actor, human: true }, {
      idempotency_key: "claim-without-shape", program_key: "carr-ai-engineering-suite-v1",
      sequence: 1, base_version: 4, executor_actor: "codex",
      source_commit_sha: "a".repeat(40), worktree_ref: "worktree:test",
    }),
    error => error instanceof ToolError && error.payload.error === "work_shape_required",
  );
});

test("a shape decision bound to an older Work Request version cannot satisfy the claim gate", async () => {
  const current = {
    id: "22222222-2222-4222-8222-222222222222", ref: "WR-AI-001", title: "Changed project",
    program_key: "carr-ai-engineering-suite-v1", program_ordinal: 1, state: "ready", version: 5,
    shape_disposition: "required", shape_rationale: "The surface remains open.", project_context: {}, acceptance_criteria: [],
  };
  const db = { query: async sql => {
    if (sql.includes("select w.* from ops.work_request")) return { rows: [current] };
    if (sql.includes("from ops.work_shape_revision")) return { rows: [{ work_request_version: 4 }] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  await assert.rejects(
    tools["start-capability-project"].handler(db, { ...actor, human: true }, {
      idempotency_key: "claim-with-stale-shape", program_key: "carr-ai-engineering-suite-v1",
      sequence: 1, base_version: 5, executor_actor: "codex",
      source_commit_sha: "a".repeat(40), worktree_ref: "worktree:test",
    }),
    error => error instanceof ToolError && error.payload.error === "work_shape_required" && error.payload.reason === "stale_after_work_request_change",
  );
});

test("a not_required disposition is valid only with an explicit fixed surface and rationale", () => {
  const work = { ref: "WR-FIXED", version: 8, shape_disposition: "not_required", shape_fixed_surface_ref: "mcp-server/src/tools.js#existing-verb", shape_rationale: "The request is a bounded extension of this verb." };
  assert.equal(implementationShapeError(work, null), null);
  assert.equal(implementationShapeError({ ...work, shape_fixed_surface_ref: null }, null).error, "work_shape_disposition_required");
});

test("migration makes disposition mandatory at implementation entry and revisions append-only", () => {
  const sql = fs.readFileSync(MIGRATION, "utf8");
  assert.doesNotMatch(sql, /shape_required/i);
  assert.match(sql, /add column if not exists shape_disposition text/i);
  assert.match(sql, /shape_disposition is null or shape_disposition in \('required','not_required'\)/i);
  assert.match(sql, /shape_disposition = 'required'[\s\S]+shape_rationale is not null[\s\S]+btrim\(shape_rationale\) <> ''/i);
  assert.match(sql, /shape_disposition = 'not_required'[\s\S]+shape_fixed_surface_ref is not null[\s\S]+btrim\(shape_fixed_surface_ref\) <> ''[\s\S]+shape_rationale is not null[\s\S]+btrim\(shape_rationale\) <> ''/i);
  assert.match(sql, /create table if not exists ops\.work_shape_revision/i);
  assert.match(sql, /work_request_version\s+integer not null/i);
  assert.match(sql, /unique\s*\(work_request_id, version\)/i);
  assert.match(sql, /before update or delete on ops\.work_shape_revision/i);
  assert.match(sql, /raise exception[^;]+append-only/is);
  assert.match(sql, /create or replace view ops\.v_work_shape_current/i);
  assert.match(sql, /grant select on ops\.work_shape_revision to carr_reader/i);
  assert.match(sql, /grant insert on ops\.work_shape_revision to carr_writer/i);
  assert.doesNotMatch(sql, /grant[^;]+update[^;]+work_shape_revision/i);
  assert.doesNotMatch(sql, /grant[^;]+delete[^;]+work_shape_revision/i);
  assert.match(sql, /create trigger work_request_shape_gate[\s\S]+before insert or update on ops\.work_request/i);
  assert.match(sql, /if tg_op = 'INSERT'[\s\S]+new\.state in \('claimed','in_progress','verification','awaiting_release','released','confirmed_closed'\)[\s\S]+cannot enter implementation directly/i);
  assert.match(sql, /new\.state = 'ready'[\s\S]+new\.shape_disposition = 'required'[\s\S]+needs a captured or triaged Work Request before ready/i);
  assert.match(sql, /new\.state = 'ready' and old\.state is distinct from 'ready'[\s\S]+new\.shape_disposition = 'required'[\s\S]+shape_work_request_version <> old\.version/i);
  assert.match(sql, /new\.state in \('claimed','in_progress','verification','awaiting_release','released','confirmed_closed'\)[\s\S]+old\.state not in \('claimed','in_progress','verification','awaiting_release','released','confirmed_closed'\)/i);
  assert.match(sql, /shape disposition must be recorded before the implementation transition/i);
  assert.match(sql, /shape_work_request_version <> old\.version/i);
  assert.match(sql, /existing ready rows[\s\S]+remain undecided and refuse at claim/i);
  assert.doesNotMatch(sql, /update ops\.work_request[\s\S]{0,800}program_key='carr-ai-engineering-suite-v1'/i);
});

test("sourced shape disposition is append-only, base-versioned, and cannot be overwritten by ready-plan acceptance", () => {
  const sql = fs.readFileSync(SOURCED_SHAPE_MIGRATION, "utf8");
  assert.match(sql, /create table if not exists ops\.sourced_work_request_shape_disposition_receipt/i);
  assert.match(sql, /idempotency_key uuid not null unique/i);
  assert.match(sql, /base_version integer not null/i);
  assert.match(sql, /result_version integer not null/i);
  assert.match(sql, /decided_by_actor_id uuid not null/i);
  assert.match(sql, /before update or delete on ops\.sourced_work_request_shape_disposition_receipt/i);
  assert.match(sql, /set_sourced_work_request_shape_disposition\([\s\S]+p_decided_by_actor_id uuid[\s\S]+p_idempotency_key uuid/i);
  assert.match(sql, /old\.state = 'triaged'[\s\S]+new\.state = 'triaged'[\s\S]+sourced_work_request_shape_disposition_receipt/i);
  assert.match(sql, /sourced_work_request_plan_shape_binding_receipt/i);
  assert.match(sql, /elsif w\.shape_disposition = 'required'[\s\S]+work_request_version[\s\S]+= w\.version/i);
  assert.match(sql, /applied_disposition := w\.shape_disposition/i);
  assert.doesNotMatch(sql, /set state='ready'[\s\S]{0,600}shape_disposition\s*=\s*'not_required'/i);
  assert.match(sql, /grant execute on function ops\.set_sourced_work_request_shape_disposition[\s\S]+to carr_writer/i);
  assert.doesNotMatch(sql, /grant execute on function ops\.set_sourced_work_request_shape_disposition[\s\S]+to carr_authority/i);
});
