
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude",
  organization_tenant_id: "carr-internal" };

const ROWS = [
  { ref: "WR-AI-006", title: "RAG pipeline", state: "in_progress",
    owner_actor: "joe", executor_actor: "codex",
    acceptance_criteria: ["Golden queries identify current source in top results"],
    project_context: { completion_definition: "Extended when retrieval quality is measured" },
    blocker_code: null, blocker_detail: null,
    program_key: "carr-ai-engineering-suite-v1", program_ordinal: 1,
    claimed_at: "2026-08-20T00:00:00Z", started_at: null,
    updated_at: "2026-08-22T00:00:00Z", hours_since_change: 4 },
  { ref: "WR-91", title: "Blocked on a credential", state: "blocked",
    owner_actor: "joe", executor_actor: null,
    acceptance_criteria: [], project_context: {},
    blocker_code: "capability", blocker_detail: "needs the authority credential Joe holds",
    program_key: null, program_ordinal: null,
    claimed_at: null, started_at: null,
    updated_at: "2026-08-18T00:00:00Z", hours_since_change: 96 },
  { ref: "WR-92", title: "Second thing codex is holding", state: "claimed",
    owner_actor: "joe", executor_actor: "codex",
    acceptance_criteria: [], project_context: {},
    blocker_code: null, blocker_detail: null,
    program_key: null, program_ordinal: null,
    claimed_at: "2026-08-22T00:00:00Z", started_at: null,
    updated_at: "2026-08-22T00:00:00Z", hours_since_change: 1 },
];

class Fake {
  constructor(rows = ROWS) { this.rows = rows; this.sql = []; }
  async query(text) {
    const s = text.replace(/\s+/g, " ").trim();
    this.sql.push(s);
    if (s.includes("work-request-intake:current-item")) return { rows: this.rows };
    return { rows: [] };
  }
}

const call = async (fake = new Fake(), args = {}) =>
  TOOLS["current-work-item"].handler(fake, joe, args);

test("the verb exists, reads nothing, and takes no arguments", async () => {
  const tool = TOOLS["current-work-item"];
  assert.ok(tool, "the council asked for ONE verb that answers this");
  assert.equal(tool.write, false, "answering what is current must never mutate");
  assert.deepEqual(tool.inputSchema.properties, {},
    "a caller-chosen filter is how a queue answer gets shaped to the answer someone wanted");
  assert.equal(tool.inputSchema.additionalProperties, false);
  await assert.rejects(() => call(new Fake(), { owner: "joe" }),
    (e) => e instanceof ToolError && e.payload.error === "invalid_current_work_item_fields");
});

test("a blocked row is STILL CURRENT and is never dropped", async () => {
  const out = await call();
  const refs = out.current.map(i => i.human_ref);
  assert.ok(refs.includes("WR-91"),
    "the council was explicit that a blocked row stays current and can never be skipped");
  const blocked = out.current.find(i => i.human_ref === "WR-91");
  assert.equal(blocked.blocker.code, "capability");
  assert.match(blocked.blocker.detail, /authority credential/);
});

test("blocker is present and null when there is none, never absent", async () => {
  const out = await call();
  const clear = out.current.find(i => i.human_ref === "WR-AI-006");
  assert.ok("blocker" in clear, "an absent field and 'not blocked' read the same and are not");
  assert.equal(clear.blocker, null);
});

test("owner and executor stay separate questions", async () => {
  const out = await call();
  const item = out.current.find(i => i.human_ref === "WR-AI-006");
  assert.equal(item.owner, "joe", "who answers for it");
  assert.equal(item.executor, "codex", "who has hands on it");
});

test("the done predicate prefers acceptance criteria, falls back, and is never invented", async () => {
  const out = await call();
  const withCriteria = out.current.find(i => i.human_ref === "WR-AI-006");
  assert.deepEqual(withCriteria.done_predicate,
    ["Golden queries identify current source in top results"],
    "acceptance predicates win over the prose completion definition");

  const fake = new Fake([{ ...ROWS[1], acceptance_criteria: [],
    project_context: { completion_definition: "Extended when the gap is enforced" } }]);
  const fell = await call(fake);
  assert.deepEqual(fell.current[0].done_predicate, ["Extended when the gap is enforced"]);

  const bare = new Fake([{ ...ROWS[1], acceptance_criteria: [], project_context: {} }]);
  const none = await call(bare);
  assert.equal(none.current[0].done_predicate, null,
    "a row with no stated predicate reports null rather than a plausible sentence");
});

test("the work-in-progress limit is reported, and over-commitment is named", async () => {
  const out = await call();
  assert.equal(out.wip.limit_system_wide, 2);
  assert.equal(out.wip.limit_per_executor, 1);
  assert.equal(out.wip.in_flight, 2);
  assert.deepEqual(out.wip.executors_over_limit, [{ executor: "codex", in_flight: 2 }]);
  assert.match(out.wip.note, /enforced in the claim path/,
    "reporting a limit and enforcing it are different jobs and must not be confused");
});

test("per-executor counting resolves the responsible party exactly as the trigger does", async () => {
  const base = { acceptance_criteria: [], project_context: {},
    blocker_code: null, blocker_detail: null,
    program_key: null, program_ordinal: null,
    claimed_at: null, started_at: null, updated_at: new Date().toISOString(),
    hours_since_change: 1 };

  const unowned = new Fake([
    { ...base, ref: "WR-N1", title: "nobody a", state: "claimed",
      owner_actor: null, executor_actor: null },
    { ...base, ref: "WR-N2", title: "nobody b", state: "in_progress",
      owner_actor: null, executor_actor: null },
  ]);
  const out = await call(unowned);
  assert.deepEqual(out.wip.executors_over_limit, [],
    "two unowned rows are not one executor over their limit — the trigger refuses neither");
  assert.equal(out.wip.in_flight, 2,
    "they still consume the system-wide limit and must stay in that count");
  assert.equal(out.wip.in_flight_unattributed, 2,
    "and they are named rather than silently dropped, or the number stops meaning anything");

  const ownerOnly = new Fake([
    { ...base, ref: "WR-O1", title: "owner a", state: "claimed",
      owner_actor: "dell", executor_actor: null },
    { ...base, ref: "WR-O2", title: "owner b", state: "in_progress",
      owner_actor: "dell", executor_actor: null },
  ]);
  const fell = await call(ownerOnly);
  assert.deepEqual(fell.wip.executors_over_limit, [{ executor: "dell", in_flight: 2 }],
    "owner is the fallback on both sides; dropping it would under-report a real breach");
  assert.equal(fell.wip.in_flight_unattributed, 0);
});

test("an item nobody has touched for two days is surfaced", async () => {
  const out = await call();
  const stale = out.unchanged_over_48h.map(s => s.human_ref);
  assert.deepEqual(stale, ["WR-91"],
    "the council forces a 48-hour-idle item to blocked, split or closed; surfacing it is this read's half");
});

test("an empty queue says so rather than looking like a broken read", async () => {
  const out = await call(new Fake([]));
  assert.equal(out.count, 0);
  assert.deepEqual(out.current, []);
  assert.match(out.say, /nothing is held/);
  assert.match(out.say, /ready work/,
    "silence about ready work is how an empty answer gets read as an empty system");
});

test("ready and captured rows are not treated as current", async () => {
  const fake = new Fake();
  await call(fake);
  const q = fake.sql.find(s => s.includes("work-request-intake:current-item"));
  assert.ok(q.includes("'claimed','in_progress','verification','needs_joe','blocked'"),
    "the queue is not the current item; only held work is");
  assert.ok(!q.includes("'ready'") && !q.includes("'captured'"));
});

test("the read is tenant-scoped by the server, never by the caller", async () => {
  const fake = new Fake();
  await call(fake);
  const q = fake.sql.find(s => s.includes("work-request-intake:current-item"));
  assert.ok(q.includes("organization_tenant_id"),
    "a caller-supplied tenant is the shape this registry refuses everywhere else");
});
