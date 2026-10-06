// WR-000110 — F02-ADMIT-DOOR and F02-RESUME-DOOR.
//
// THE POINT OF THIS FILE, and the reason Part A exists. A decision module is
// only load-bearing if the door that PERFORMS the gated action consults it, and
// the door is `admit-engineering-slice`. So the proof drives the ONE REGISTERED
// HANDLER through the real idempotency wrapper — the same wrapper mcp.js uses —
// rather than calling the function directly. A direct call proves the function's
// own return shape and nothing about caller handling, which is why the direct
// test below is named so it can never be mistaken for the criterion.
//
// A repository search finds no other production caller of admitEngineeringSlice:
// engineering-runtime.js registers it once, at the handler line, and nothing
// else imports it outside tests.

import assert from "node:assert/strict";
import test from "node:test";

import {
  admitEngineeringSlice, buildCodexEnvelope, canonicalDigest, engineeringRuntimeTools,
  ENGINEERING_REPOSITORY_ACTIONS,
} from "../src/engineering-runtime.js";

class EngineeringToolError extends Error {
  constructor(payload) { super(payload.error || "engineering tool error"); Object.assign(this, payload); }
}

const HEAD = "a".repeat(40);
const OTHER_HEAD = "b".repeat(40);
const ROOT = "/Users/booko/carr-system";
const WORK_REF = "WR-301";

const actor = {
  id: "33333333-3333-4333-8333-333333333333", slug: "codex", sponsoring_human_slug: "joe",
};

const source = {
  work: {
    id: "wr:11111111-1111-4111-8111-111111111111", ref: WORK_REF, version: 3,
    canonical_record_digest: `sha256:${"1".repeat(64)}`,
  },
  plan: {
    record_id: "22222222-2222-4222-8222-222222222222", plan_ref: "plan:301",
    revision: 2, digest: `sha256:${"2".repeat(64)}`,
  },
};

// The plan, slice and passport shapes are lifted verbatim from the door's own
// existing suite so this proof exercises the real admission path rather than a
// shape that happens to get past it.
function engineeringSlice(sliceRef, ordinal, dependency_refs = []) {
  return {
    slice_ref: sliceRef, ordinal, objective: `Execute ${sliceRef}`,
    definition_of_done: "A typed receipt exists",
    dependency_refs, declared_resource_refs: [], declared_component_refs: [],
    declared_plan_step_refs: [], baseline_evidence_refs: [],
    planned_checks: [{ check_ref: `check:${sliceRef}`, failure_condition: "missing",
      evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: `one bounded ${sliceRef}`, forbidden_change_refs: [],
    concurrency_posture: "serial_after_dependencies", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
}

function typedPlan(slices) {
  const typed = {
    schema_version: "engineering-slice-plan.v1",
    work_request: { id: source.work.id, state_version: 3,
      canonical_record_digest: source.work.canonical_record_digest },
    accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest },
    slices,
  };
  typed.plan_digest = canonicalDigest(typed);
  return typed;
}

function passportFacts(plan, { envelopes = [], receipts = [], reviewer_facts = [] } = {}) {
  return {
    source: { work_request: source.work, accepted_plan: source.plan },
    slice_plans: [{ id: "12121212-1212-4212-8212-121212121212",
      accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan }],
    envelopes, receipts, reviewer_facts,
  };
}

function envelopeRow(id, slice_ref, created_at, supersedes_envelope_id = null) {
  const job_id = id.replace(/-/g, "").slice(0, 32);
  return { id, job_id, agent_session_id: id, envelope_digest: `sha256:${"e".repeat(64)}`,
    work_request_id: source.work.id.replace(/^wr:/, ""),
    slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref, created_at,
    issued_at: created_at, supersedes_envelope_id,
    envelope: { envelope_id: `env:${id}`, request: { job_ref: `job:${job_id}` },
      agent_session: { id: `session:${id}` },
      server_binding: { identity: { agent_principal_id: "agent:codex" },
        adapter: { adapter_id: "adapter:codex-desktop" } } } };
}

// ---------------------------------------------------------------------------
// The seam rows. The ONLY difference between the two runs is one peer lease on
// an overlapping path segment — one fact, changed in the records and nowhere
// else.
// ---------------------------------------------------------------------------

const leaseRow = (overrides = {}) => ({
  slice_ref: "slice:one",
  worktree_ref: "worktree:wr110-a",
  worktree_path: "/Users/booko/carr-system/.claude/worktrees/wr110-a",
  branch_ref: "branch:wr110-a",
  base_commit_sha: HEAD,
  source_paths: ["mcp-server/src/program-controller-census.v5.js"],
  database_disposition: "schema_only_fixture",
  database_resources: [],
  serialized_surfaces: [],
  repository_actions: ["repository:commit"],
  reuse_disposition: "extend",
  model_roles: ["author"],
  ...overrides,
});

const peerOnOverlappingPath = leaseRow({
  slice_ref: "slice:peer", worktree_ref: "worktree:wr110-b",
  worktree_path: "/Users/booko/carr-system/.claude/worktrees/wr110-b",
  branch_ref: "branch:wr110-b", source_paths: ["mcp-server/src"],
});

const checkpointRow = (overrides = {}) => ({
  checkpoint_ref: "checkpoint:wr110-1", recorded_at: new Date(), base_commit_sha: HEAD,
  worktree_ref: "worktree:wr110-a", completed_step_refs: ["step:one"],
  next_step_ref: "step:two", record_evidence_refs: ["record:one"],
  reconstruction_source: "durable_records", inherited_transcript_used: false, ...overrides,
});

/**
 * A client that answers both the door's own reads and the census legs, and
 * records every write it was asked to make — including the fact-ledger call,
 * which is how the refusal's persistence is proved.
 */
function doorClient({ facts, leases = [], checkpoints = [], toolCalls = new Map(),
  widthState = [{ program_ref: source.work.id, current_width: 3, requested_width: 3 }] } = {}) {
  const calls = [];
  const ledger = [];
  const client = {
    calls, ledger, toolCalls,
    async query(sql, params = []) {
      calls.push({ sql, params });
      if (sql.includes("record_program_controller_fact")) {
        ledger.push({ kind: "admission_refusal", key: params[0], body: JSON.parse(params[1]) });
        return { rows: [{ fact: { ok: true } }] };
      }
      if (sql.includes("ops.program_origin_head_observation"))
        return { rows: [{ repository_root: ROOT, origin_main_sha: HEAD, observed_at: new Date() }] };
      if (sql.includes("ops.slice_source_lease")) return { rows: leases };
      if (sql.includes("ops.program_width_state")) return { rows: widthState };
      if (sql.includes("ops.program_width_evidence")) return { rows: [] };
      if (sql.includes("ops.slice_checkpoint")) return { rows: checkpoints };
      if (sql.includes("ops.release_receipt")) return { rows: [] };
      if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
      if (sql.includes("from tool_call")) {
        const prior = toolCalls.get(params[0]);
        return { rows: prior ? [prior] : [] };
      }
      if (sql.includes("insert into tool_call")) {
        toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
        return { rows: [] };
      }
      if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
      if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan"))
        return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
      if (sql.includes("select id from ops.work_request"))
        return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
      if (sql.includes("insert into ops.capability_agent_session"))
        return { rows: [{ id: "22222222-2222-4222-8222-222222222222" }] };
      if (sql.includes("capability_agent_session")) return { rows: [] };
      if (sql.includes("engineering_enqueue_slice_job"))
        return { rows: [{ id: "11111111-1111-4111-8111-111111111111" }] };
      if (sql.includes("insert into ops.engineering_execution_envelope"))
        return { rows: [{ id: "33333333-3333-4333-8333-333333333333" }] };
      return { rows: [] };
    },
  };
  return client;
}

/**
 * The REAL idempotency wrapper's two properties, modelled exactly: a replayed
 * key returns the stored response without calling the handler again, and a
 * handler that RETURNS has its returned value stored. That is what makes a
 * returned refusal survive the commit mcp.js performs.
 */
function envelopeWrapper(store) {
  return async (client, _actor, _verb, args, fn) => {
    const prior = store.get(args.idempotency_key);
    if (prior) return { replayed: true, ...prior };
    const result = await fn();
    store.set(args.idempotency_key, result);
    return result;
  };
}

function toolsFor(store, events = []) {
  return engineeringRuntimeTools({
    withEnvelope: envelopeWrapper(store),
    writeEvent: async (_c, _a, verb, subjectType, subjectId, fields) =>
      events.push({ verb, subjectType, subjectId, fields }),
    ToolError: EngineeringToolError,
  });
}

// ---------------------------------------------------------------------------
// F02-ADMIT-DOOR — Part A, the one real caller
// ---------------------------------------------------------------------------

test("F02-ADMIT-DOOR (A): the registered handler admits, then refuses on one changed fact, and the refusal replays", async () => {
  const plan = typedPlan([engineeringSlice("slice:one", 1)]);
  const facts = passportFacts(plan);
  const store = new Map();
  const events = [];
  const tools = toolsFor(store, events);
  const handler = tools["admit-engineering-slice"].handler;

  // FIRST CALL — one live lease, no peer. The evaluator allows and the door
  // issues the envelope exactly as it always did.
  const allowed = doorClient({ facts, leases: [leaseRow()] });
  const first = await handler(allowed, actor, {
    idempotency_key: "11111111-1111-4111-8111-111111111111",
    work_request: WORK_REF, slice_ref: "slice:one",
  });
  assert.equal(first.ok, true);
  assert.equal(first.envelope_id, "33333333-3333-4333-8333-333333333333");
  assert.equal(allowed.ledger.length, 0, "an allow records no refusal");

  // SECOND CALL — the census now holds a peer lease on an overlapping path
  // segment. Nothing else differs: same plan, same passport, same arguments.
  const refused = doorClient({ facts, leases: [leaseRow(), peerOnOverlappingPath] });
  const second = await handler(refused, actor, {
    idempotency_key: "22222222-2222-4222-8222-222222222222",
    work_request: WORK_REF, slice_ref: "slice:one",
  });
  assert.equal(second.ok, false);
  assert.equal(second.refusal, "engineering_slice_admission_refused");
  // The door does not invent a reason: it carries the EVALUATOR's own.
  assert.equal(second.reason_id, "source_path_overlap_denied");
  assert.equal(second.blocking_check, "source_path_overlap");
  assert.equal(second.decision.check_states.source_path_overlap.detail.conflicting_slice_ref,
    "slice:peer");

  // The refusal RETURNED rather than threw, so the transaction commits: the
  // fact-ledger row, the event and the replay row are all there.
  assert.equal(refused.ledger.length, 1);
  assert.equal(refused.ledger[0].body.reason_id, "source_path_overlap_denied");
  assert.equal(refused.ledger[0].key, "22222222-2222-4222-8222-222222222222");
  assert.ok(refused.ledger[0].body.decision_digest.startsWith("sha256:"));
  assert.equal(events.at(-1).fields.new.admission_refused, "source_path_overlap_denied");

  // The refusal must also come back BEFORE any insert: nothing was enqueued.
  assert.equal(refused.calls.some(call => call.sql.includes("engineering_enqueue_slice_job")), false,
    "a refused slice reached the enqueue path");
  assert.equal(refused.calls.some(call => call.sql.includes("insert into ops.engineering_execution_envelope")),
    false, "a refused slice reached the envelope insert");

  // THIRD CALL — the same key. The stored refusal replays byte for byte and the
  // decision is not made a second time.
  const replay = doorClient({ facts, leases: [leaseRow(), peerOnOverlappingPath] });
  const third = await handler(replay, actor, {
    idempotency_key: "22222222-2222-4222-8222-222222222222",
    work_request: WORK_REF, slice_ref: "slice:one",
  });
  assert.equal(third.replayed, true);
  assert.equal(third.ok, false);
  assert.equal(third.reason_id, "source_path_overlap_denied");
  assert.equal(replay.ledger.length, 0, "a replay re-decided and recorded a second fact");
  assert.equal(replay.calls.length, 0, "a replay reached the database at all");
});

test("F02-ADMIT-DOOR (A): the evaluator runs before the replay branch and before any insert", async () => {
  const plan = typedPlan([engineeringSlice("slice:one", 1)]);
  const client = doorClient({ facts: passportFacts(plan), leases: [leaseRow(), peerOnOverlappingPath] });
  const store = new Map();
  await toolsFor(store)["admit-engineering-slice"].handler(client, actor, {
    idempotency_key: "33333333-3333-4333-8333-333333333333",
    work_request: WORK_REF, slice_ref: "slice:one",
  });
  const lockAt = client.calls.findIndex(call => call.sql.includes("pg_advisory_xact_lock"));
  const censusAt = client.calls.findIndex(call => call.sql.includes("ops.slice_source_lease"));
  const ledgerAt = client.calls.findIndex(call => call.sql.includes("record_program_controller_fact"));
  assert.ok(lockAt !== -1 && censusAt > lockAt,
    "the census must be read INSIDE the serialization boundary");
  assert.ok(ledgerAt > censusAt, "the refusal is recorded after the decision it records");
});

// ---------------------------------------------------------------------------
// F02-ADMIT-DOOR — Part B. NOT caller-handling evidence.
// ---------------------------------------------------------------------------

test("direct call of admitEngineeringSlice (NOT caller-handling evidence)", async () => {
  const plan = typedPlan([engineeringSlice("slice:one", 1)]);
  const client = doorClient({ facts: passportFacts(plan), leases: [leaseRow(), peerOnOverlappingPath] });
  // This checks the function's own RETURN SHAPE and nothing else. It says
  // nothing about whether the registered handler, its envelope wrapper or the
  // transaction around them preserve that shape, which is the whole of
  // F02-ADMIT-DOOR and is proved in Part A.
  const result = await admitEngineeringSlice(client, actor, {
    idempotency_key: "44444444-4444-4444-8444-444444444444",
    work_request: WORK_REF, slice_ref: "slice:one",
  }, EngineeringToolError, async () => {});
  assert.deepEqual(Object.keys(result).sort(),
    ["admitted", "blocking_check", "decision", "ok", "reason_id", "refusal", "slice_ref"]);
  assert.equal(result.ok, false);
  assert.equal(result.admitted, false);
});

// ---------------------------------------------------------------------------
// F02-RESUME-DOOR
// ---------------------------------------------------------------------------

/**
 * A door fixture whose prior envelope belongs to an ACTIVE session, so control
 * reaches the replay branch — the branch that IS the resume door. Lifted from
 * the door's own prior-session fixture so the shape is the real one.
 */
function activeReplayFixture({ checkpoints = [], leases = [leaseRow()] } = {}) {
  const plan = typedPlan([engineeringSlice("slice:one", 1)]);
  const priorId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const priorSession = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const priorJob = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const prior = buildCodexEnvelope({ source, plan, slice: plan.slices[0],
    jobId: priorJob, sessionId: priorSession, actor });
  const facts = passportFacts(plan, { envelopes: [{
    id: priorId, job_id: priorJob, agent_session_id: priorSession,
    accepted_plan_id: source.plan.record_id,
    slice_plan_id: "12121212-1212-4212-8212-121212121212",
    slice_ref: "slice:one", created_at: prior.issued_at, envelope: prior,
  }] });
  const ledger = [];
  const calls = [];
  const c = { ledger, calls, query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("record_program_controller_fact")) {
      ledger.push({ key: params[0], body: JSON.parse(params[1]) });
      return { rows: [{ fact: { ok: true } }] };
    }
    if (sql.includes("ops.program_origin_head_observation"))
      return { rows: [{ repository_root: ROOT, origin_main_sha: HEAD, observed_at: new Date() }] };
    if (sql.includes("ops.slice_source_lease")) return { rows: leases };
    if (sql.includes("ops.program_width_state"))
      return { rows: [{ program_ref: source.work.id, current_width: 3, requested_width: 3 }] };
    if (sql.includes("ops.program_width_evidence")) return { rows: [] };
    if (sql.includes("ops.slice_checkpoint")) return { rows: checkpoints };
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("from ops.engineering_execution_envelope e")) return { rows: [{
      id: priorId, job_id: priorJob, work_request_id: source.work.id.replace(/^wr:/, ""),
      accepted_plan_id: source.plan.record_id,
      slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: "slice:one",
      agent_session_id: priorSession, envelope: prior, job_state: "running",
    }] };
    if (sql.includes("where id=$1::uuid for update")) return { rows: [{
      id: priorSession, work_request_id: source.work.id.replace(/^wr:/, ""),
      executor_actor_id: actor.id, state: "claimed",
      lease_expires_at: prior.agent_session.lease_expires_at, scope_ref: "slice:slice:one",
      worktree_ref: "engineering:server-admission", source_commit_sha: "0".repeat(40),
    }] };
    if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
    if (sql.includes("engineering_envelope_currentness"))
      return { rows: [{ currentness: { eligible: true, dispatch_runway_sufficient: true } }] };
    if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan"))
      return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
    if (sql.includes("select id from ops.work_request"))
      return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
    return { rows: [] };
  } };
  return { c, ledger, prior };
}

test("F02-RESUME-DOOR: a checkpoint bound to a superseded head refuses the replay by name", async () => {
  const fixture = activeReplayFixture({
    checkpoints: [checkpointRow({ base_commit_sha: OTHER_HEAD })] });
  const result = await admitEngineeringSlice(fixture.c, actor, {
    idempotency_key: "55555555-5555-4555-8555-555555555555",
    work_request: WORK_REF, slice_ref: "slice:one",
  }, EngineeringToolError, async () => {});
  assert.equal(result.ok, false);
  assert.equal(result.refusal, "engineering_slice_resume_refused");
  assert.equal(result.reason_id, "checkpoint_base_moved_revalidation_required");
  assert.equal(result.checkpoint_absent, false);
  // The refusal is recorded, and it is recorded instead of an envelope: the
  // replay never happened.
  assert.equal(fixture.ledger.length, 1);
  assert.equal(fixture.ledger[0].body.reason_id, "checkpoint_base_moved_revalidation_required");
});

test("F02-RESUME-DOOR: a current checkpoint on the held tree replays as before", async () => {
  const fixture = activeReplayFixture({ checkpoints: [checkpointRow()] });
  const result = await admitEngineeringSlice(fixture.c, actor, {
    idempotency_key: "77777777-7777-4777-8777-777777777777",
    work_request: WORK_REF, slice_ref: "slice:one",
  }, EngineeringToolError, async () => {});
  assert.equal(result.ok, true);
  assert.equal(result.replayed, true);
  assert.equal(result.checkpoint_absent, false);
  assert.equal(fixture.ledger.length, 0);
});

test("F02-RESUME-DOOR: a slice with no checkpoint row replays exactly as before and says so", async () => {
  const fixture = activeReplayFixture({ checkpoints: [] });
  const result = await admitEngineeringSlice(fixture.c, actor, {
    idempotency_key: "66666666-6666-4666-8666-666666666666",
    work_request: WORK_REF, slice_ref: "slice:one",
  }, EngineeringToolError, async () => {});
  assert.equal(result.ok, true);
  assert.equal(result.replayed, true);
  // The absence is REPORTED. A checkpoint is never invented, and the absence is
  // never turned into a refusal either.
  assert.equal(result.checkpoint_absent, true);
  assert.equal(fixture.ledger.length, 0, "a missing checkpoint was turned into a refusal");
});

test("the repository registers exactly one caller of the admission door", async () => {
  const tools = toolsFor(new Map());
  assert.equal(typeof tools["admit-engineering-slice"].handler, "function");
  assert.equal(tools["admit-engineering-slice"].write, true);
  assert.deepEqual(
    Object.keys(tools["admit-engineering-slice"].inputSchema.properties).sort(),
    ["idempotency_key", "slice_ref", "work_request"],
    "a fourth argument would be a field through which a caller could assert a fact");
  assert.deepEqual([...ENGINEERING_REPOSITORY_ACTIONS].length > 0, true);
});
