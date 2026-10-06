import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { TOOLS, ToolError } from "../src/tools.js";
import { BOARD_ANSWER_WRITE_VERBS } from "../src/board-answers.js";

const joe = {
  id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth-google", client_id: "doctorcre-app",
};
const robot = { ...joe, id: "10000000-0000-0000-0000-000000000009", slug: "board-job", human: false };
const sponsoredRobot = { ...robot, slug: "codex", sponsoring_human_slug: "joe",
  native_agent_verified: true };
const dell = { ...joe, id: "10000000-0000-0000-0000-000000000003", slug: "dell", display: "Dell" };

class Fake {
  constructor(plan = {}) { this.plan = plan; this.calls = []; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push([sql, params]);
    for (const [fragment, value] of Object.entries(this.plan)) {
      if (sql.includes(fragment)) return { rows: typeof value === "function" ? value(params) : value };
    }
    return { rows: [] };
  }
}

test("board verbs expose typed versioned writes and a partner-only answer", () => {
  const writes = ["publish-board-snapshot", "ask-board-question", "revise-board-question",
    "answer-board-question", "acknowledge-board-answer", "record-board-answer-applied"];
  assert.deepEqual([...BOARD_ANSWER_WRITE_VERBS], writes);
  for (const name of writes) {
    assert.equal(TOOLS[name]?.write, true, name);
    assert.ok(TOOLS[name].inputSchema.required.includes("idempotency_key"), name);
    assert.ok(TOOLS[name].inputSchema.required.includes("base_version"), name);
  }
  assert.equal(TOOLS["answer-board-question"].humanOnly, true);
  for (const name of ["read-progress-board", "read-board-answers"])
    assert.equal(TOOLS[name]?.write, false, name);
  assert.deepEqual(TOOLS["read-board-answers"].inputSchema.required,
    ["after_cursor", "asker_ref"]);
});

test("board snapshot publication is tenant scoped and refuses stale versions", async () => {
  const fake = new Fake({ "update board_snapshot": [] });
  await assert.rejects(
    () => TOOLS["publish-board-snapshot"].handler(fake, sponsoredRobot, {
      board_id: "project", base_version: 1, snapshot: { title: "Project", tasks: {} },
      idempotency_key: "publish-1",
    }),
    error => error instanceof ToolError && error.payload.error === "board_version_conflict",
  );
  const [sql, params] = fake.calls.find(([statement]) => statement.includes("update board_snapshot"));
  assert.match(sql, /organization_tenant_id/);
  assert.match(sql, /version/);
  assert.ok(params.includes("carr-internal"));
});

test("concurrent same-key board writes replay the first result", async () => {
  let receipt = null, snapshot = null, unlock = null;
  let firstRead;
  const readStarted = new Promise(resolve => { firstRead = resolve; });
  function client() {
    let ownsLock = false;
    return {
      release() { if (ownsLock) { ownsLock = false; unlock?.(); unlock = null; } },
      async query(sql, params = []) {
        if (sql.includes("pg_advisory_xact_lock")) {
          while (unlock) await new Promise(resolve => { const previous = unlock; unlock = () => { previous(); resolve(); }; });
          ownsLock = true;
          unlock = () => {};
          return { rows: [] };
        }
        if (sql.includes("select request_hash, response")) {
          firstRead();
          return { rows: receipt ? [receipt] : [] };
        }
        if (sql.includes("insert into board_snapshot")) {
          await new Promise(resolve => setTimeout(resolve, 20));
          if (snapshot) return { rows: [] };
          snapshot = { id: "board-1", board_id: "project", version: 1 };
          return { rows: [snapshot] };
        }
        if (sql.includes("insert into tool_call")) {
          receipt = { request_hash: params[3], response: JSON.parse(params[4]) };
          return { rows: [] };
        }
        return { rows: [] };
      },
    };
  }
  const args = { idempotency_key: "same-board-key", board_id: "project",
    base_version: 0, snapshot: { title: "Project" } };
  async function run(c) {
    try { return await TOOLS["publish-board-snapshot"].handler(c, joe, args); }
    finally { c.release(); }
  }
  const first = run(client());
  await readStarted;
  const second = run(client());
  const [a, b] = await Promise.all([first, second]);
  assert.equal(a.snapshot.id, "board-1");
  assert.equal(b.replayed, true);
  assert.deepEqual(b.snapshot, a.snapshot);
});

test("board answers and receipts refuse a different sponsor within the same tenant", async () => {
  const answerId = "20000000-0000-0000-0000-000000000001";
  const answer = { id: answerId, asker_ref: "orchestrator:project", answer_text: "Proceed" };
  const fake = new Fake({
    "from board_answer a where": params => params.includes("joe") ? [answer] : [],
    "update board_answer a set received_at": params => params.includes("joe") ? [answer] : [],
    "update board_answer a set applied_at": params => params.includes("joe") ? [answer] : [],
  });
  const args = { after_cursor: 0, asker_ref: "orchestrator:project" };
  const own = await TOOLS["read-board-answers"].handler(fake, joe, args);
  assert.equal(own.answers.length, 1);
  const other = await TOOLS["read-board-answers"].handler(fake, dell, args);
  assert.deepEqual(other.answers, []);
  const read = fake.calls.find(([sql]) => sql.includes("from board_answer a where"));
  assert.match(read[0], /sponsoring_human_slug/);
  assert.deepEqual(read[1].slice(0, 3), ["carr-internal", "joe", "orchestrator:project"]);
  await assert.rejects(() => TOOLS["acknowledge-board-answer"].handler(fake, dell,
    { idempotency_key: "ack-cross-sponsor", answer_id: answerId,
      asker_ref: "orchestrator:project", base_version: 1 }),
  error => error instanceof ToolError && error.payload.error === "board_version_conflict");
  await assert.rejects(() => TOOLS["record-board-answer-applied"].handler(fake, dell,
    { idempotency_key: "apply-cross-sponsor", answer_id: answerId,
      base_version: 2, effect_ref: "pr:1400" }),
  error => error instanceof ToolError && error.payload.error === "board_version_conflict");
  for (const statement of ["update board_answer a set received_at",
    "update board_answer a set applied_at"]) {
    const [sql, params] = fake.calls.find(([query]) => query.includes(statement));
    assert.match(sql, /a\.sponsoring_human_slug=\$4/);
    assert.equal(params[3], "dell");
  }
});

test("answer uses the authenticated partner, never a caller supplied answered_by", async () => {
  const answer = { id: "answer-1", question_id: "q1", question_revision: 1,
    answer_text: "Proceed", answered_by: "joe", version: 1, status: "Sent" };
  const fake = new Fake({
    "select request_hash, response": [],
    "from board_question": [{ question_id: "q1", revision: 1, choices: [], allow_free_text: true,
      default_answer: null, asker_ref: "orchestrator:project" }],
    "insert into board_answer": [answer],
    "insert into event": [{ id: "event-1" }],
  });
  const result = await TOOLS["answer-board-question"].handler(fake, joe, {
    idempotency_key: "answer-1", board_id: "project", question_id: "q1",
    base_version: 1, answer_text: "Proceed",
  });
  assert.equal(result.answer.answered_by, "joe");
  const [, params] = fake.calls.find(([sql]) => sql.includes("insert into board_answer"));
  assert.ok(params.includes(joe.id));
  assert.ok(params.includes("joe"));
  assert.equal(TOOLS["answer-board-question"].inputSchema.properties.answered_by, undefined);
});

test("a machine cannot answer even when it has a verified sponsor", async () => {
  const fake = new Fake();
  await assert.rejects(
    () => TOOLS["answer-board-question"].handler(fake,
      { ...robot, native_agent_verified: true, sponsoring_human_slug: "joe" }, {
        idempotency_key: "answer-machine", board_id: "project", question_id: "q1",
        base_version: 1, answer_text: "Proceed",
      }),
    error => error instanceof ToolError && error.payload.error === "board_answer_requires_human_partner",
  );
  assert.deepEqual(fake.calls, []);
});

test("Received and Applied require exact versions and record the actor and effect", async () => {
  const answerId = "20000000-0000-0000-0000-000000000001";
  const received = { id: answerId, version: 2, status: "Received" };
  const applied = { id: answerId, version: 3, status: "Applied", effect_ref: "pr:1400" };
  const fake = new Fake({
    "select request_hash, response": [],
    "update board_answer a set received_at": [received],
    "update board_answer a set applied_at": [applied],
  });
  const ack = await TOOLS["acknowledge-board-answer"].handler(fake, sponsoredRobot, {
    idempotency_key: "ack-1", answer_id: answerId,
    asker_ref: "orchestrator:project", base_version: 1,
  });
  assert.equal(ack.answer.status, "Received");
  const [ackSql, ackParams] = fake.calls.find(([sql]) => sql.includes("update board_answer a set received_at"));
  assert.match(ackSql, /received_for_ref/);
  assert.match(ackSql, /version=\$6/);
  assert.deepEqual(ackParams, [sponsoredRobot.id, "orchestrator:project", "carr-internal", "joe", answerId, 1]);
  const result = await TOOLS["record-board-answer-applied"].handler(fake, sponsoredRobot, {
    idempotency_key: "applied-1", answer_id: answerId, base_version: 2,
    effect_ref: "pr:1400",
  });
  assert.equal(result.answer.status, "Applied");
  const [applySql, applyParams] = fake.calls.find(([sql]) => sql.includes("update board_answer a set applied_at"));
  assert.match(applySql, /received_at is not null/);
  assert.deepEqual(applyParams, [sponsoredRobot.id, "pr:1400", "carr-internal", "joe", answerId, 2]);
});

test("the board read leaves unanswered questions without a status", async () => {
  const fake = new Fake({
    "from board_snapshot": [{ board_id: "project", version: 1, snapshot_json: { title: "Project" } }],
    "from board_question q": [{ question_id: "q1", answer_id: null, status: null }],
  });
  const result = await TOOLS["read-progress-board"].handler(fake, joe, { board_id: "project" });
  assert.equal(result.questions[0].status, null);
  const [sql] = fake.calls.find(([statement]) => statement.includes("from board_question q"));
  assert.match(sql, /case when a\.id is null then null/);
  assert.match(sql, /q\.current=true/);
});

test("the published board and its current questions use the authenticated sponsor", async () => {
  const fake = new Fake({
    "from board_snapshot": params => params.includes("joe") ? [{ board_id: "project" }] : [],
    "from board_question q": params => params.includes("joe") ? [{ question_id: "q1" }] : [],
  });
  const own = await TOOLS["read-progress-board"].handler(fake, joe, { board_id: "project" });
  const other = await TOOLS["read-progress-board"].handler(fake, dell, { board_id: "project" });
  assert.equal(own.questions.length, 1);
  assert.equal(other.snapshot, null);
  assert.deepEqual(other.questions, []);
  for (const [sql, params] of fake.calls.filter(([query]) =>
    query.includes("from board_snapshot") || query.includes("from board_question q"))) {
    assert.match(sql, /sponsoring_human_slug=\$2/);
    assert.ok(params[1] === "joe" || params[1] === "dell");
  }
});

test("read cursor and asker reference are both in the tenant scoped query", async () => {
  const fake = new Fake({ "from board_answer": [] });
  const result = await TOOLS["read-board-answers"].handler(fake, sponsoredRobot,
    { after_cursor: 12, asker_ref: "orchestrator:project" });
  assert.deepEqual(result.answers, []);
  const [sql, params] = fake.calls.find(([statement]) => statement.includes("from board_answer"));
  assert.match(sql, /organization_tenant_id/);
  assert.match(sql, /asker_ref/);
  assert.match(sql, /cursor > /);
  assert.deepEqual(params.slice(0, 4), ["carr-internal", "joe", "orchestrator:project", 12]);
});

test("typed board records install reference monitors and question uniqueness", () => {
  const schema = readFileSync(new URL("../../migrations/0740_board_answers.sql", import.meta.url), "utf8");
  for (const table of ["board_snapshot", "board_question", "board_answer"])
    assert.match(schema, new RegExp(`create table public\\.${table}`));
  for (const table of ["board_snapshot", "board_question", "board_answer"]) {
    assert.match(schema, new RegExp(`on public\\.${table} for each row execute function ops\\.scac_reference_monitor_guard\\(\\)`));
    assert.match(schema, new RegExp(`on public\\.${table} for each statement execute function ops\\.scac_reference_monitor_guard\\(\\)`));
  }
  assert.match(schema, /unique \(organization_tenant_id,sponsoring_human_slug,board_id,question_id,question_revision\)/i);
});
