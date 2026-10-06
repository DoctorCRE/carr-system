import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { TOOLS, executeRegisteredTool, ToolError as RegistryToolError } from "../src/tools.js";
import {
  COMPLETION_KINDS,
  capabilityProgramTools,
  completionEvidenceError,
  nextProjectState,
} from "../src/capability-program.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const MIGRATION = path.join(REPO, "migrations/0125_ai_capability_program.sql");
const REPRIORITIZATION = path.join(REPO, "migrations/0129_reprioritize_rag_benchmark.sql");
const PROGRAM = "carr-ai-engineering-suite-v1";
const APPROVED_TITLES = [
  "LLM evaluation harness", "Structured-output parser", "Function-calling router",
  "Guardrails system", "AI gateway", "RAG pipeline", "Agent loop / ReAct",
  "Data-curation and deduplication pipeline", "Synthetic-data generator",
  "Knowledge-graph builder", "Semantic router", "Prompt caching",
  "Code-interpreter sandbox", "Text-to-SQL", "Graph RAG", "Vector database / HNSW",
  "Embedding model", "Adversarial-attack generator", "Whisper-style ASR",
  "Text-to-speech pipeline", "Small language model", "Inference server",
  "Quantization library", "Feature store", "Recommendation system", "Vector database driver",
  "Reasoner / Chain-of-Thought implementation", "Interpretability / SAE tooling",
  "LoRA trainer", "PEFT library", "Model-distillation pipeline", "DPO loss",
  "RLHF / PPO pipeline", "Model merger", "KV-cache paging", "Speculative decoding",
  "Tokenizer", "Transformer", "Vision Transformer", "Multimodal projector / CLIP",
  "Diffusion model", "Audio Spectrogram Transformer", "Logit processor",
  "State Space Model / Mamba", "Mixture-of-Experts routing layer",
  "Distributed training / FSDP / tensor parallelism", "Autograd engine",
  "Matrix multiplication kernel", "Softmax optimization", "FlashAttention CUDA kernel",
  "Neural Architecture Search",
];

function seededRows() {
  const sql = fs.readFileSync(MIGRATION, "utf8");
  const rows = [...sql.matchAll(/\(\s*(\d+)\s*,\s*'carr-ai-engineering-suite-v1'\s*,\s*'(WR-AI-\d+)'\s*,\s*'([^']+)'\s*,\s*'(build|extend|adopt|decline)'\s*,[\s\S]*?'(\{[^']+\})'::jsonb,\s*'joe',\s*'joe'\)/g)]
    .map(([, sequence, ref, title, disposition, context]) => ({
      sequence: Number(sequence), ref, title, disposition, context: JSON.parse(context),
    }));
  return rows;
}

test("the capability portfolio is seeded once in the approved usefulness order", () => {
  const rows = seededRows();

  assert.equal(rows.length, 51);
  assert.deepEqual(rows.map(row => row.sequence), Array.from({ length: 51 }, (_, i) => i + 1));
  assert.deepEqual(rows.map(row => row.title), APPROVED_TITLES);
  for (const row of rows) {
    assert.deepEqual(Object.keys(row.context).sort(), [
      "completion_definition", "data_risk", "effort", "evidence", "first_deliverable",
      "non_goals", "prerequisites", "rollback_exit", "scope",
    ]);
    assert.equal(typeof row.context.scope, "string", `${row.ref}: scope`);
    assert.equal(typeof row.context.first_deliverable, "string", `${row.ref}: first deliverable`);
    assert.equal(typeof row.context.rollback_exit, "string", `${row.ref}: rollback/exit`);
    assert.equal(typeof row.context.data_risk, "string", `${row.ref}: data/risk`);
    assert.equal(typeof row.context.effort, "string", `${row.ref}: effort`);
    assert.equal(typeof row.context.completion_definition, "string", `${row.ref}: completion definition`);
    assert.ok(Array.isArray(row.context.non_goals), `${row.ref}: non-goals`);
    assert.ok(Array.isArray(row.context.prerequisites), `${row.ref}: prerequisites`);
    assert.ok(Array.isArray(row.context.evidence), `${row.ref}: evidence`);
    assert.ok(row.context.scope.trim() && row.context.first_deliverable.trim() &&
      row.context.rollback_exit.trim() && row.context.completion_definition.trim(), `${row.ref}: no placeholder context`);
  }
});

test("Joe's retrieval decision moves RAG first without renaming or skipping another project", () => {
  const sql = fs.readFileSync(REPRIORITIZATION, "utf8");
  const expectedRefs = [
    "WR-AI-006", ...Array.from({ length: 5 }, (_, i) => `WR-AI-${String(i + 1).padStart(3, "0")}`),
    ...Array.from({ length: 45 }, (_, i) => `WR-AI-${String(i + 7).padStart(3, "0")}`),
  ];
  const proof = sql.match(/expected text\[\] := array\[([\s\S]*?)\];/);
  assert.ok(proof, "migration must carry its exact effective-order proof");
  assert.deepEqual([...proof[1].matchAll(/'(WR-AI-\d+)'/g)].map(match => match[1]), expectedRefs);
  assert.match(sql, /non_ready <> 0 or sessions <> 0/,
    "an in-flight or changed program must refuse reprioritization");
  assert.match(sql, /lock table ops\.capability_agent_session in share row exclusive mode/,
    "the zero-session proof must be atomic against a concurrent session insert");
  assert.match(sql, /disable trigger capability_program_identity_guard_before_update/);
  assert.match(sql, /enable trigger capability_program_identity_guard_before_update/);
  assert.match(sql, /WR-AI-006[\s\S]*sole queue head/i);
});

test("completion evidence is conditional on what completion means", () => {
  assert.deepEqual(COMPLETION_KINDS, ["built", "extended", "adopted", "declined"]);

  assert.equal(completionEvidenceError("built", {
    artifact_ref: "pr:201",
    acceptance_test_refs: ["ci:run:501"],
    independent_verifier_ref: "finding:review-9",
  }), null);

  assert.equal(completionEvidenceError("extended", {
    artifact_ref: "commit:abc",
    acceptance_test_refs: ["test:contract"],
    independent_verifier_ref: "review:fresh-context",
  }), null);

  assert.equal(completionEvidenceError("adopted", {
    artifact_ref: "manifest:runtime-v1",
    acceptance_test_refs: ["rehearsal:12"],
    independent_verifier_ref: "review:13",
    rollback_ref: "runbook:remove-runtime",
    decision_ref: "decision:adopt-runtime",
  }), null);

  assert.equal(completionEvidenceError("declined", {
    decision_ref: "decision:decline-transformer",
    independent_verifier_ref: "review:cost-case",
  }), null);

  assert.equal(completionEvidenceError("built", {
    artifact_ref: "pr:201",
    acceptance_test_refs: [],
    independent_verifier_ref: "review:9",
  }).error, "completion_evidence_incomplete");

  assert.equal(completionEvidenceError("adopted", {
    artifact_ref: "manifest:runtime-v1",
    acceptance_test_refs: ["rehearsal:12"],
    independent_verifier_ref: "review:13",
  }).missing.includes("rollback_ref"), true);

  assert.equal(completionEvidenceError("declined", {
    decision_ref: "decision:decline-transformer",
  }).missing.includes("independent_verifier_ref"), true);
});

test("advancement activates exactly one successor and never skips a queued row", () => {
  assert.deepEqual(nextProjectState(1, [
    { sequence: 2, state: "ready" },
    { sequence: 3, state: "ready" },
  ]), { completeProgram: false, nextSequence: 2 });

  assert.deepEqual(nextProjectState(50, [
    { sequence: 51, state: "ready" },
  ]), { completeProgram: false, nextSequence: 51 });

  assert.deepEqual(nextProjectState(51, []), {
    completeProgram: true,
    nextSequence: null,
  });

  assert.throws(() => nextProjectState(1, [
    { sequence: 3, state: "ready" },
  ]), /queue_gap/);
  assert.throws(() => nextProjectState(1, [
    { sequence: 2, state: "in_progress" },
  ]), /successor_not_ready/);
});

test("the registry exposes a read context and only human-governed lifecycle writes", () => {
  assert.equal(Boolean(TOOLS["capability-program"]?.write), false);
  assert.equal(Boolean(TOOLS["start-capability-project"]?.write), true);
  assert.equal(Boolean(TOOLS["complete-capability-project"]?.write), true);
  assert.equal(Boolean(TOOLS["begin-capability-project"]?.write), true);
  assert.equal(Boolean(TOOLS["attest-capability-project"]?.write), true);
  // humanOnly LABEL RETIRED (WR-000019 slice S1, 2026-08-27): it was already a
  // dead label — executeRegisteredTool stopped reading it 2026-08-26 by Joe's
  // ruling, decision dc57f62d — and this slice removes the stale declaration
  // from capability-program.js. These are still write:true, human-governed
  // lifecycle transitions; only the unread flag is gone.
  assert.equal(TOOLS["complete-capability-project"].humanOnly, undefined);
  assert.equal(TOOLS["start-capability-project"].humanOnly, undefined);
  assert.equal(TOOLS["begin-capability-project"].humanOnly, undefined);
  assert.equal(TOOLS["attest-capability-project"].humanOnly, undefined);

  const completion = TOOLS["complete-capability-project"].inputSchema;
  assert.equal(completion.additionalProperties, false);
  assert.equal(completion.required.includes("base_version"), true);
  assert.equal(completion.required.includes("completion_evidence"), true);
  assert.deepEqual(completion.properties.completion_kind.enum, COMPLETION_KINDS);
  assert.equal(TOOLS["prepare-capability-project"].humanOnly, undefined);
  assert.equal(TOOLS["capability-program"].inputSchema.properties.program_key.const, PROGRAM,
    "the public reader must not select an arbitrary program");
  for (const name of ["start-capability-project", "begin-capability-project", "prepare-capability-project", "attest-capability-project", "complete-capability-project"])
    assert.equal(TOOLS[name].inputSchema.properties.program_key.const, PROGRAM,
      `${name} must reject cross-program writes`);
});

test("capability-program exposes only the current open session identity", async () => {
  const current = {
    id: "wr-current", ref: "WR-AI-006", program_ordinal: 1, version: 3, state: "in_progress",
    disposition: "extend", title: "RAG pipeline", project_context: {},
  };
  const later = {
    ...current, id: "wr-later", ref: "WR-AI-007", program_ordinal: 2, state: "ready",
  };
  const session = {
    id: "11111111-1111-4111-8111-111111111111", state: "in_progress",
    candidate_evidence: { artifact_ref: "must-not-leak" }, candidate_fingerprint: "a".repeat(32),
  };
  const db = { query: async (sql, params = []) => {
    if (sql.includes("from ops.work_request")) return { rows: [current, later] };
    if (sql.includes("from ops.capability_agent_session")) {
      assert.deepEqual(params, [current.id], "session lookup binds only the current Work Request");
      assert.match(sql, /state not in \('completed','cancelled'\)/i,
        "terminal and historical sessions are excluded in SQL");
      return { rows: [session] };
    }
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });

  const result = await tools["capability-program"].handler(db, actor, { program_key: PROGRAM, sequence: 2, include_all: true });

  assert.deepEqual(result.capability_session, { id: session.id, state: session.state });
  assert.deepEqual(result.current.ref, "WR-AI-006");
  assert.deepEqual(result.requested.ref, "WR-AI-007");
  assert.equal("candidate_evidence" in result.capability_session, false);
  assert.equal("candidate_fingerprint" in result.capability_session, false);
  assert.equal("capability_session" in result.projects[0], false);
  assert.equal("capability_session" in result.projects[1], false);
});

test("capability-program returns no session when the current Work Request has none", async () => {
  const current = {
    id: "wr-current", ref: "WR-AI-006", program_ordinal: 1, version: 1, state: "ready",
    disposition: "extend", title: "RAG pipeline", project_context: {},
  };
  const db = { query: async (sql, params = []) => {
    if (sql.includes("from ops.work_request")) return { rows: [current] };
    if (sql.includes("from ops.capability_agent_session")) {
      assert.deepEqual(params, [current.id]);
      return { rows: [] };
    }
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });

  const result = await tools["capability-program"].handler(db, actor, { program_key: PROGRAM });

  assert.equal(result.capability_session, null);
});

test("a proposed decline is never offered as the next build item, and never counted as done", async () => {
  // THE LIVE SHAPE THIS PINS. 29 of the program's 51 rows carry disposition
  // 'decline' while sitting in state 'ready'. Nothing records that any of those
  // declines was DECIDED — shape disposition, decider, decision timestamp,
  // triage classification and triaging actor are null on all 29 — and each row's
  // own completion definition writes the decline as a condition rather than an
  // outcome. So they can be neither built nor closed by this read.
  const closed = { id: "a", ref: "WR-AI-006", program_ordinal: 1, version: 1,
    state: "confirmed_closed", disposition: "extend", title: "RAG pipeline", project_context: {} };
  const decline = { id: "b", ref: "WR-AI-014", program_ordinal: 2, version: 1,
    state: "ready", disposition: "decline", title: "Text-to-SQL", project_context: {} };
  const buildable = { id: "c", ref: "WR-AI-001", program_ordinal: 3, version: 1,
    state: "ready", disposition: "extend", title: "LLM evaluation harness", project_context: {} };

  const db = { query: async (sql) => {
    if (sql.includes("from ops.work_request")) return { rows: [closed, decline, buildable] };
    if (sql.includes("from ops.capability_agent_session")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const result = await tools["capability-program"].handler(db, actor, { program_key: PROGRAM });

  assert.equal(result.current.ref, "WR-AI-001",
    "the decline sits earlier in the sequence and must be stepped over, not handed to a session to build");
  assert.equal(result.total, 3, "total still counts every row in the program");
  assert.equal(result.buildable_total, 2,
    "buildable excludes the proposed decline; counting it is why 1-of-51 read as stalled");
  assert.equal(result.proposed_declines_awaiting_a_decision, 1);
  assert.deepEqual(result.proposed_decline_refs, ["WR-AI-014"],
    "named, not silently dropped — a skipped row nobody can see is how work disappears");
});

test("the read names what the CLOSE path will demand next, not only what to build", async () => {
  // THE DIVERGENCE THIS PINS, which the fixture above already contained and
  // nothing asserted on. `current` skips proposed declines so a session is never
  // handed one to build — deliberate and right. The close path does not skip: it
  // takes the first row that is not confirmed_closed in program order and refuses
  // anything else with out_of_order_project.
  //
  // Here the decline sits at ordinal 2 and the buildable row at ordinal 3. So a
  // session reads WR-AI-001, builds it, calls complete on its sequence, and is
  // refused by a verb naming a sequence it was never shown. On the live program
  // that moment is twelve closes away: the first proposed decline is at ordinal
  // 14 and the queue is at ordinal 2.
  //
  // Both answers ship, because they answer different questions.
  const closed = { id: "a", ref: "WR-AI-006", program_ordinal: 1, version: 1,
    state: "confirmed_closed", disposition: "extend", title: "RAG pipeline", project_context: {} };
  const decline = { id: "b", ref: "WR-AI-014", program_ordinal: 2, version: 1,
    state: "ready", disposition: "decline", title: "Text-to-SQL", project_context: {} };
  const buildable = { id: "c", ref: "WR-AI-001", program_ordinal: 3, version: 1,
    state: "ready", disposition: "extend", title: "LLM evaluation harness", project_context: {} };

  const db = { query: async (sql) => {
    if (sql.includes("from ops.work_request")) return { rows: [closed, decline, buildable] };
    if (sql.includes("from ops.capability_agent_session")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const result = await tools["capability-program"].handler(db, actor, { program_key: PROGRAM });

  assert.equal(result.current.ref, "WR-AI-001",
    "unchanged: the buildable row is still what a session is told to build");
  assert.equal(result.next_to_close_ref, "WR-AI-014",
    "the close path takes the decline first, and the read must say so");
  assert.equal(result.next_to_close_sequence, 2,
    "and name the sequence the close verb will actually accept");
  assert.equal(result.close_sequence_blocked_by_proposed_decline, true);
  assert.match(result.close_sequence_note, /out of order/,
    "the note must say why completing the buildable row will be refused");
});

test("no close-sequence warning when the two definitions agree", async () => {
  // The other half. A warning that fires when nothing is wrong is noise, and
  // this read is consumed by sessions deciding what to do next.
  const closed = { id: "a", ref: "WR-AI-006", program_ordinal: 1, version: 1,
    state: "confirmed_closed", disposition: "extend", title: "RAG pipeline", project_context: {} };
  const buildable = { id: "c", ref: "WR-AI-001", program_ordinal: 2, version: 1,
    state: "ready", disposition: "extend", title: "LLM evaluation harness", project_context: {} };
  const laterDecline = { id: "b", ref: "WR-AI-014", program_ordinal: 3, version: 1,
    state: "ready", disposition: "decline", title: "Text-to-SQL", project_context: {} };

  const db = { query: async (sql) => {
    if (sql.includes("from ops.work_request")) return { rows: [closed, buildable, laterDecline] };
    if (sql.includes("from ops.capability_agent_session")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const result = await tools["capability-program"].handler(db, actor, { program_key: PROGRAM });

  assert.equal(result.current.ref, "WR-AI-001");
  assert.equal(result.next_to_close_ref, "WR-AI-001", "same row — both definitions agree");
  assert.equal(result.close_sequence_blocked_by_proposed_decline, false);
  assert.equal(result.close_sequence_note, undefined, "no note when there is nothing to warn about");
});

test("a program with only proposed declines left is NOT complete", async () => {
  // The dangerous half of stepping over them. If the read skipped declines and
  // called the program done, 29 undecided rows would vanish behind a green
  // headline — the false-green shape this system keeps finding.
  const closed = { id: "a", ref: "WR-AI-006", program_ordinal: 1, version: 1,
    state: "confirmed_closed", disposition: "extend", title: "RAG pipeline", project_context: {} };
  const decline = { id: "b", ref: "WR-AI-014", program_ordinal: 2, version: 1,
    state: "ready", disposition: "decline", title: "Text-to-SQL", project_context: {} };
  const db = { query: async (sql) => {
    if (sql.includes("from ops.work_request")) return { rows: [closed, decline] };
    if (sql.includes("from ops.capability_agent_session")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const result = await tools["capability-program"].handler(db, actor, { program_key: PROGRAM });

  assert.equal(result.current, null, "there is nothing left to build");
  assert.equal(result.program_complete, false,
    "but one decline is still undecided, so the program is not done");
  assert.equal(result.proposed_declines_awaiting_a_decision, 1);
});

test("a decline that WAS decided stops being a proposed decline", async () => {
  // The rule is about undecided rows, not about the word decline. A row moved to
  // a terminal state is settled and must leave the awaiting count, or the number
  // could never fall to zero and the program could never complete.
  const settled = { id: "a", ref: "WR-AI-014", program_ordinal: 1, version: 1,
    state: "confirmed_closed", disposition: "decline", title: "Text-to-SQL", project_context: {} };
  const db = { query: async (sql) => {
    if (sql.includes("from ops.work_request")) return { rows: [settled] };
    if (sql.includes("from ops.capability_agent_session")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const result = await tools["capability-program"].handler(db, actor, { program_key: PROGRAM });

  assert.equal(result.proposed_declines_awaiting_a_decision, 0);
  assert.equal(result.buildable_total, 1, "a settled row counts again");
  assert.equal(result.program_complete, true);
});

test("a settled decline may be closed out of order; build work may not", async () => {
  // WHY THIS EXISTS. On 2026-08-22 Joe ruled on twelve declines in one sitting
  // and not one could be recorded: the earliest sat at sequence 24, behind an
  // unbuilt evaluation harness at sequence 2. Build ordering is about WORK, and
  // a decline is never built — holding it in the lane is what makes the queue
  // read fifty deep when twenty-one items are real.
  const head = { id: "h", ref: "WR-AI-001", program_ordinal: 2, version: 3,
    state: "ready", disposition: "extend", title: "LLM evaluation harness", project_context: {} };
  const decline = { id: "d", ref: "WR-AI-024", program_ordinal: 24, version: 5,
    state: "ready", disposition: "decline", title: "Feature store", project_context: {} };
  const buildLater = { id: "b", ref: "WR-AI-007", program_ordinal: 7, version: 2,
    state: "ready", disposition: "extend", title: "Something buildable", project_context: {} };

  const dbFor = rows => ({ query: async (sql, params = []) => {
    if (/order by w.program_ordinal limit 1/.test(sql)) return { rows: [head] };
    if (/program_ordinal=\$2/.test(sql)) {
      const wanted = Number(params[1]);
      return { rows: rows.filter(r => Number(r.program_ordinal) === wanted) };
    }
    if (sql.includes("from ops.capability_agent_session")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }});

  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });

  // A DECLINE OUT OF ORDER IS REACHABLE. It must fail for a reason that is NOT
  // out_of_order_project — reaching it is the whole point.
  let err = null;
  try {
    await tools["start-capability-project"].handler(dbFor([decline]), actor,
      { idempotency_key: "00000000-0000-4000-8000-000000000001", sequence: 24, base_version: 5, executor_actor: "claude", program_key: PROGRAM });
  } catch (e) { err = e; }
  assert.notEqual(err?.payload?.error, "out_of_order_project",
    "a row already dispositioned decline must not be refused for being out of sequence");

  // BUILD WORK OUT OF ORDER IS STILL REFUSED. This is the half that must not
  // move: the exemption tests the row's disposition, never the caller's wish.
  err = null;
  try {
    await tools["start-capability-project"].handler(dbFor([buildLater]), actor,
      { idempotency_key: "00000000-0000-4000-8000-000000000002", sequence: 7, base_version: 2, executor_actor: "claude", program_key: PROGRAM });
  } catch (e) { err = e; }
  assert.equal(err?.payload?.error, "out_of_order_project",
    "buildable work stays strictly ordered");

  // A STALE READ IS STILL REFUSED even on the decline path, or the exemption
  // would also be a way past the version guard.
  err = null;
  try {
    await tools["start-capability-project"].handler(dbFor([decline]), actor,
      { idempotency_key: "00000000-0000-4000-8000-000000000003", sequence: 24, base_version: 1, executor_actor: "claude", program_key: PROGRAM });
  } catch (e) { err = e; }
  assert.equal(err?.payload?.error, "version_conflict",
    "the decline exemption must not smuggle a stale write past the version check");
});

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const actor = { id: "human-owner", human: true, slug: "joe" };
const row = (overrides = {}) => ({
  id: "wr-1", ref: "WR-AI-001", program_ordinal: 1, version: 7, state: "verification",
  disposition: "build", title: "LLM evaluation harness", project_context: {
    active_session_ref: "session:owned-1",
    candidate_evidence: { artifact_ref: "commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", acceptance_test_refs: ["ci:100"] },
  }, ...overrides,
});

function programToolsFor(rowValue) {
  const writes = [];
  const db = { query: async (sql, params = []) => {
    if (sql.includes("select w.* from ops.work_request")) return { rows: [rowValue] };
    if (sql.includes("program_ordinal>$2")) return { rows: [{ ...rowValue, id: "wr-2", ref: "WR-AI-002", program_ordinal: 2, state: "ready" }] };
    if (sql.includes("update ops.work_request")) { writes.push({ sql, params }); return { rows: [{ ...rowValue, state: "confirmed_closed" }] }; }
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({
    withEnvelope: async (_c, _a, _verb, _args, fn) => fn(),
    writeEvent: async () => {}, ToolError,
  });
  return { db, tools, writes };
}

test("completion refuses fake, same-actor, mismatched, or unbound verification evidence", async () => {
  const { db, tools, writes } = programToolsFor(row());
  const complete = tools["complete-capability-project"].handler;
  const base = {
    idempotency_key: "test-complete-1", program_key: PROGRAM, sequence: 1, base_version: 7,
    completion_kind: "built", completion_evidence: {
      artifact_ref: "commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", acceptance_test_refs: ["ci:100"],
      independent_verifier_ref: "finding:independent-pass",
    },
  };
  for (const completion_evidence of [
    { ...base.completion_evidence, independent_verifier_ref: "totally-made-up" },
    { ...base.completion_evidence, independent_verifier_ref: "finding:human-owner" },
    { ...base.completion_evidence, artifact_ref: "commit:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" },
    { ...base.completion_evidence, acceptance_test_refs: ["ci:999"] },
  ]) await assert.rejects(
    complete(db, actor, { ...base, completion_evidence }),
    error => error instanceof ToolError && /verification|candidate|evidence/.test(error.payload.error),
  );
  assert.equal(writes.length, 0, "a rejected completion must not close or advance the queue");
});

test("persisted candidate plus an independent pass closes exactly one project and exposes its successor", async () => {
  const sessionId = "11111111-1111-4111-8111-111111111111";
  const executorId = "22222222-2222-4222-8222-222222222222";
  const verifierId = "33333333-3333-4333-8333-333333333333";
  const passId = "44444444-4444-4444-8444-444444444444";
  const fingerprint = "a".repeat(32);
  const current = row();
  const session = {
    id: sessionId, work_request_id: current.id, state: "verification", candidate_kind: "built",
    candidate_fingerprint: fingerprint, executor_actor_id: executorId,
    candidate_evidence: { artifact_ref: "commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", candidate_commit_sha: "a".repeat(40), acceptance_test_refs: ["ci:100"] },
  };
  const writes = [];
  const db = { query: async (sql, params = []) => {
    if (sql.includes("select w.* from ops.work_request")) return { rows: [current] };
    if (sql.includes("from ops.capability_agent_session")) return { rows: [session] };
    if (sql.includes("from ops.capability_verification")) return { rows: [{
      id: passId, verifier_actor_id: verifierId, verification_evidence_ref: "test:ci-100",
      source_ref: "independent review", attested_at: "2026-08-15T00:00:00.000Z", candidate_fingerprint: fingerprint,
    }] };
    if (sql.includes("update ops.work_request")) { writes.push({ kind: "close", params }); return { rows: [{ ...current, state: "confirmed_closed" }] }; }
    if (sql.includes("update ops.capability_agent_session")) { writes.push({ kind: "session", params }); return { rows: [] }; }
    if (sql.includes("program_ordinal>$2")) return { rows: [{ ...current, id: "wr-2", ref: "WR-AI-002", program_ordinal: 2, state: "ready" }] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const result = await tools["complete-capability-project"].handler(db, { ...actor, id: verifierId }, {
    idempotency_key: "happy-path-1", program_key: PROGRAM, sequence: 1, base_version: 7,
    capability_agent_session_id: sessionId, completion_kind: "built", completion_evidence: { candidate_fingerprint: fingerprint },
  });
  assert.equal(result.completed_project.state, "confirmed_closed");
  assert.equal(result.next_project.ref, "WR-AI-002");
  assert.deepEqual(writes.map(w => w.kind), ["close", "session"]);

  for (const bad of [
    { actor: { ...actor, id: executorId }, evidence: { candidate_fingerprint: fingerprint }, label: "self actor" },
    { actor: { ...actor, id: verifierId }, evidence: { candidate_fingerprint: "b".repeat(32) }, label: "candidate mismatch" },
  ]) await assert.rejects(
    tools["complete-capability-project"].handler(db, bad.actor, {
      idempotency_key: `reject-${bad.label}`, program_key: PROGRAM, sequence: 1, base_version: 7,
      capability_agent_session_id: sessionId, completion_kind: "built", completion_evidence: bad.evidence,
    }), error => error instanceof ToolError && /self|fingerprint/.test(error.payload.error), bad.label,
  );

  const noPass = { query: async (sql, params) => {
    if (sql.includes("from ops.capability_verification")) return { rows: [] };
    return db.query(sql, params);
  }};
  await assert.rejects(
    tools["complete-capability-project"].handler(noPass, { ...actor, id: verifierId }, {
      idempotency_key: "reject-no-pass", program_key: PROGRAM, sequence: 1, base_version: 7,
      capability_agent_session_id: sessionId, completion_kind: "built", completion_evidence: { candidate_fingerprint: fingerprint },
    }), error => error instanceof ToolError && error.payload.error === "independent_capability_pass_required",
  );
});

test("lifecycle binds a persisted session and does not falsely collapse ready through claimed", async () => {
  const source = fs.readFileSync(path.join(REPO, "mcp-server/src/capability-program.js"), "utf8");
  assert.match(source, /insert into ops\.capability_agent_session/i,
    "start must create/validate a persisted session, not accept an arbitrary string");
  assert.match(source, /state='claimed'/i,
    "ready→claimed must be a real durable transition");
  assert.match(source, /active_session_id|agent_session_id/i,
    "prepare and complete must bind to the same persisted session");
});

test("the scheduled builder definition cannot certify, merge, deploy, or communicate", () => {
  const prompt = fs.readFileSync(
    path.join(REPO, "ops/scheduled-tasks/ai-capability-builder.SKILL.md"), "utf8");
  for (const boundary of [
    "NEVER mark a project complete",
    "NEVER merge",
    "NEVER deploy",
    "NEVER communicate externally",
    "one current project",
    "reminder_only_pending_runner_identity",
    "must not edit\\s+files",
    "must not[\\s\\S]*delegate implementation",
  ]) assert.match(prompt, new RegExp(boundary, "i"));
});

test("the dispatcher lets a non-human actor reach every capability lifecycle handler", async () => {
  // INVERTED, not deleted (Joe's ruling 2026-08-26, decision dc57f62d). These
  // five used to be stopped at the dispatcher before their handler ran. The
  // point now is the opposite one, and it is still worth pinning: the actor
  // must REACH the handler. The fake client throws the moment it is touched, so
  // "handler must not run" arriving is proof the dispatcher passed the call
  // through rather than refusing it on authority.
  const nonHuman = { id: "scheduled-builder", human: false, slug: "scheduled-builder" };
  for (const name of ["start-capability-project", "begin-capability-project", "prepare-capability-project", "attest-capability-project", "complete-capability-project", "cancel-capability-session"]) {
    await assert.rejects(
      executeRegisteredTool({ query: async () => { throw new Error("handler must not run"); } }, nonHuman, name, {}),
      error => !(error instanceof RegistryToolError && error.payload?.error === "human_only"),
      `${name} must no longer be stopped on authority`);
  }
});


// ── cancel-capability-session ────────────────────────────────────────────────
// The verb that unsticks a row whose session died mid-flight. These pin the
// three ways a cancel could quietly become something worse than the problem it
// solves: undoing a settled close, erasing evidence, and running without a
// stated reason.

function cancelFixture(overrides = {}) {
  const row = { id: "wr", ref: "WR-AI-038", program_ordinal: 38, version: 5,
    state: "verification", disposition: "decline", title: "Transformer",
    project_context: {}, shape_disposition: "not_required",
    executor_actor: "joe-local", ...overrides };
  const head = { id: "head", ref: "WR-AI-001", program_ordinal: 2, version: 3,
    state: "ready", disposition: "extend", title: "LLM evaluation harness", project_context: {} };
  const session = { id: "sess", work_request_id: "wr", state: "verification",
    candidate_kind: "declined", candidate_fingerprint: "abc", executor_actor_id: "ex" };
  const writes = [];
  const db = { query: async (sql, params = []) => {
    if (/order by w.program_ordinal limit 1/.test(sql)) return { rows: [head] };
    if (/program_ordinal=\$2/.test(sql)) return { rows: Number(params[1]) === 38 ? [row] : [] };
    if (/from ops.capability_agent_session/.test(sql)) return { rows: overrides.__noSession ? [] : [session] };
    if (/update ops.capability_agent_session/.test(sql)) { writes.push("session-cancelled"); return { rows: [{ ...session, state: "cancelled" }] }; }
    if (/update ops.work_request/.test(sql)) { writes.push("row-ready"); return { rows: [{ ...row, state: "ready", executor_actor: null, version: 6 }] }; }
    if (/delete/i.test(sql)) { writes.push("DELETE"); return { rows: [] }; }
    throw new Error(`unexpected query: ${sql}`);
  }};
  return { db, writes, row, session };
}

function cancelTools(events = []) {
  return capabilityProgramTools({
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    writeEvent: async (_c, _a, verb, _t, _id, payload) => { events.push({ verb, payload }); },
    ToolError,
  });
}

test("a cancel returns the row to ready, cancels the session, and deletes nothing", async () => {
  const { db, writes } = cancelFixture();
  const events = [];
  const result = await cancelTools(events)["cancel-capability-session"].handler(db, actor, {
    idempotency_key: "00000000-0000-4000-8000-00000000000a", sequence: 38, base_version: 5,
    reason: "Joe parked the whole suite on 2026-08-26; this candidate must not be completed.",
    program_key: PROGRAM,
  });

  assert.equal(result.ok, true);
  assert.equal(result.project.state, "ready", "the row must be workable again");
  assert.equal(result.cancelled_session.state, "cancelled");
  assert.equal(result.cancelled_session.previous_state, "verification",
    "the report says what was actually abandoned, not just that something was");
  assert.equal(result.cancelled_session.candidate_fingerprint, "abc",
    "the frozen candidate is REPORTED, not erased — what happened did happen");
  assert.ok(writes.includes("session-cancelled") && writes.includes("row-ready"));
  assert.ok(!writes.includes("DELETE"), "a cancel must never delete evidence");
  assert.equal(events[0].payload.new.reason.length > 0, true, "the reason reaches the timeline");
});

test("a cancel can NEVER reopen a settled close", async () => {
  // requireProject only ever returns rows that are not confirmed_closed, so a
  // closed row is simply not found out of order. This is the assertion that
  // stops cancel becoming a back door around the immutability trigger.
  const { db } = cancelFixture({ __noSession: false });
  const closedDb = { query: async (sql, params = []) => {
    if (/order by w.program_ordinal limit 1/.test(sql)) return { rows: [{ id: "head", ref: "WR-AI-001", program_ordinal: 2, version: 3, state: "ready", disposition: "extend", project_context: {} }] };
    if (/program_ordinal=\$2/.test(sql)) return { rows: [] };  // closed rows are excluded by the query itself
    return db.query(sql, params);
  }};
  await assert.rejects(
    cancelTools()["cancel-capability-session"].handler(closedDb, actor, {
      idempotency_key: "00000000-0000-4000-8000-00000000000b", sequence: 38, base_version: 6,
      reason: "trying to undo a close", program_key: PROGRAM }),
    error => error.payload.error === "out_of_order_project");
});

test("a cancel refuses without a reason, and refuses a row that is not in flight", async () => {
  const blank = cancelFixture();
  await assert.rejects(
    cancelTools()["cancel-capability-session"].handler(blank.db, actor, {
      idempotency_key: "00000000-0000-4000-8000-00000000000c", sequence: 38, base_version: 5,
      reason: "   ", program_key: PROGRAM }),
    error => error.payload.error === "capability_cancel_reason_required",
    "a side exit with no reason is the thing the canonical machine forbids");

  const idle = cancelFixture({ state: "ready" });
  await assert.rejects(
    cancelTools()["cancel-capability-session"].handler(idle.db, actor, {
      idempotency_key: "00000000-0000-4000-8000-00000000000d", sequence: 38, base_version: 5,
      reason: "nothing is running", program_key: PROGRAM }),
    error => error.payload.error === "invalid_state_transition");
});

test("a cancel refuses a row with no shape disposition rather than letting the database refuse it", async () => {
  // ops.work_request_shape_gate rejects any row entering ready without one, and
  // freezes the shape columns after claim, so this verb cannot set one itself.
  // Catching it here is the difference between a usable message and a raw
  // trigger exception surfacing as an internal error.
  const { db } = cancelFixture({ shape_disposition: null });
  await assert.rejects(
    cancelTools()["cancel-capability-session"].handler(db, actor, {
      idempotency_key: "00000000-0000-4000-8000-00000000000e", sequence: 38, base_version: 5,
      reason: "row is stuck", program_key: PROGRAM }),
    error => error.payload.error === "work_shape_disposition_required");
});

test("a cancel refuses a session id that is not the row's open session", async () => {
  const { db } = cancelFixture();
  await assert.rejects(
    cancelTools()["cancel-capability-session"].handler(db, actor, {
      idempotency_key: "00000000-0000-4000-8000-00000000000f", sequence: 38, base_version: 5,
      reason: "wrong session", capability_agent_session_id: "not-the-open-one", program_key: PROGRAM }),
    error => error.payload.error === "capability_agent_session_not_for_current_project");
});
