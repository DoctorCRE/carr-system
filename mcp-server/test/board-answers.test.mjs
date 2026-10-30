// Tests board answer writes, publication, and reads.
import { readMigration } from "./migration-source.testhelper.mjs";
import { test } from "node:test";
import assert from "node:assert/strict";

import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { BOARD_ANSWER_WRITE_VERBS } from "../src/board-answers.js";
import { mcpInventory } from "../../ops/scac-mutation-inventory.mjs";
import { derivedIdempotencyKey } from "../src/derived-key.ts";

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
    "answer-board-question", "acknowledge-board-answer"];
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
      idempotency_key: "00000571-0000-4000-8000-000000000027",
    }),
    error => error instanceof ToolError && error.payload.error === "board_version_conflict",
  );
  const [sql, params] = fake.calls.find(([statement]) => statement.includes("update board_snapshot"));
  assert.match(sql, /organization_tenant_id/);
  assert.match(sql, /version/);
  assert.ok(params.includes("carr-internal"));
});

test("board reads overlap independent queries and batch links at every board size", async () => {
  for (const count of [1, 1000]) {
    const tasks = Object.fromEntries(Array.from({ length: count }, (_, i) =>
      [`task-${i}`, { title: `Task ${i}`, work_request: `WR-${i}` }]));
    let releaseSnapshot;
    const snapshotReady = new Promise(resolve => { releaseSnapshot = resolve; });
    const fake = new Fake({
      "from board_snapshot": [{ board_id: "project", version: "7", snapshot_json: { tasks } }],
      "from board_question q": [{ question_id: "q1", revision: "1", status: "Sent", answer_version: "1" }],
      "from ops.work_request": [{ ref: "WR-0", title: "Readable request" }],
    });
    const query = fake.query.bind(fake);
    fake.query = async (sql, params) => {
      if (sql.includes("from board_snapshot")) await snapshotReady;
      return query(sql, params);
    };
    const reading = TOOLS["read-progress-board"].handler(fake, joe, { board_id: "project" });
    try {
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(fake.calls.length, 1, "questions must start before the snapshot returns");
      assert.match(fake.calls[0][0], /from board_question q/);
    } finally {
      releaseSnapshot();
      await reading;
    }
    const result = await reading;
    assert.equal(fake.calls.length, 3, "two board queries and one batched link query");
    for (const [, params] of fake.calls) assert.deepEqual(params.slice(0, 2), ["carr-internal", "joe"]);
    const links = fake.calls.find(([sql]) => sql.includes("from ops.work_request"));
    assert.equal(links[1][2].length, count);
    assert.equal(result.snapshot.version, 7);
    assert.equal(Object.keys(result.snapshot.snapshot_json.tasks).length, count);
    assert.equal(result.snapshot.snapshot_json.tasks['task-0'].linkage.references[0].title, "Readable request");
    assert.deepEqual(result.questions, [{ question_id: "q1", revision: 1, status: "Sent", answer_version: 1 }]);
  }
});

test("board publication bounds database payload work and replies with only the board version", async () => {
  for (const base_version of [0, 7]) {
    const snapshot = { schema: "carr-progress-board.v4", tasks: Object.fromEntries(
      Array.from({ length: 1000 }, (_, i) => [`task-${i}`, { title: `Task ${i}` }])) };
    const metadata = { id: "board-1", organization_tenant_id: "carr-internal",
      sponsoring_human_slug: "joe", board_id: "project", version: String(base_version + 1), updated_at: "2026-10-01T00:00:00Z" };
    const fake = new Fake({
      [base_version ? "update board_snapshot" : "insert into board_snapshot"]: [metadata],
    });
    const args = { board_id: "project", base_version, snapshot, idempotency_key: derivedIdempotencyKey("test", `publish-${base_version}`) };
    const result = await TOOLS["publish-board-snapshot"].handler(fake, sponsoredRobot, args);
    const [sql, params] = fake.calls.find(([sql]) => /insert into board_snapshot|update board_snapshot/.test(sql));
    assert.doesNotMatch(sql.split(/returning/i)[1], /snapshot_json/, "RETURNING must have size independent of the board");
    assert.equal(fake.calls.length, 5, "one lock, replay read, snapshot write, event and receipt");
    const publication = fake.calls.filter(([sql]) => /insert into event/.test(sql));
    assert.equal(publication.length, 1, "each new publication records one history entry");
    const event = publication[0][1];
    assert.equal(event[1], sponsoredRobot.id);
    assert.deepEqual(event.slice(2, 5), ["publish-board-snapshot", "board_snapshot", metadata.id]);
    assert.deepEqual(JSON.parse(event[7]), { board_id: "project", version: metadata.version });
    assert.deepEqual(JSON.parse(params[base_version ? 0 : 3]), snapshot);
    assert.deepEqual(result, { ok: true, board_id: "project", version: base_version + 1 },
      "the reply names the board and its new version, never the snapshot");
    const receipt = fake.calls.find(([sql]) => sql.includes("insert into tool_call"));
    assert.deepEqual(JSON.parse(receipt[1][4]), { ok: true, snapshot: { ...metadata, version: base_version + 1 } });
    assert.ok(receipt[1][4].length < 512, "receipt storage must stay bounded as the board grows");
    const replay = new Fake({ "select request_hash, response": [{
      request_hash: receipt[1][3], response: JSON.parse(receipt[1][4]),
    }] });
    const retried = await TOOLS["publish-board-snapshot"].handler(replay, sponsoredRobot, args);
    assert.deepEqual(retried, { replayed: true, ...result });
    assert.equal(replay.calls.length, 2, "replay only locks and reads the bounded receipt");
    const legacyReplay = new Fake({ "select request_hash, response": [{
      request_hash: receipt[1][3], response: { ok: true, snapshot: { ...metadata, snapshot_json: snapshot } },
    }] });
    assert.deepEqual(await TOOLS["publish-board-snapshot"].handler(legacyReplay, sponsoredRobot, args),
      { replayed: true, ...result }, "a full receipt from before the bounded reply replays the same reply");
    await assert.rejects(() => TOOLS["publish-board-snapshot"].handler(replay, sponsoredRobot,
      { ...args, snapshot: { ...snapshot, title: "Different publication" } }),
    error => error.payload?.error === "key_reuse");
  }
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
  const args = { idempotency_key: "00000571-0000-4000-8000-000000000028", board_id: "project",
    base_version: 0, snapshot: { title: "Project" } };
  async function run(c) {
    try { return await TOOLS["publish-board-snapshot"].handler(c, joe, args); }
    finally { c.release(); }
  }
  const first = run(client());
  await readStarted;
  const second = run(client());
  const [a, b] = await Promise.all([first, second]);
  assert.deepEqual(a, { ok: true, board_id: "project", version: 1 });
  assert.deepEqual(b, { replayed: true, ...a });
});

test("board answers and acknowledgments refuse a different sponsor within the same tenant", async () => {
  const answerId = "20000000-0000-0000-0000-000000000001";
  const answer = { id: answerId, asker_ref: "orchestrator:project", answer_text: "Proceed" };
  const fake = new Fake({
    "from board_answer a where": params => params.includes("joe") ? [answer] : [],
    "update board_answer a set received_at": params => params.includes("joe") ? [answer] : [],
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
    { idempotency_key: "00000571-0000-4000-8000-000000000029", answer_id: answerId,
      asker_ref: "orchestrator:project", base_version: 1 }),
  error => error instanceof ToolError && error.payload.error === "board_version_conflict");
  const [sql, params] = fake.calls.find(([query]) => query.includes("update board_answer a set received_at"));
  assert.match(sql, /a\.sponsoring_human_slug=\$4/);
  assert.equal(params[3], "dell");
});

test("acknowledgments require an exact version and record the actor", async () => {
  const answerId = "20000000-0000-0000-0000-000000000001";
  const received = { id: answerId, version: 2, status: "Received" };
  const fake = new Fake({
    "select request_hash, response": [],
    "update board_answer a set received_at": [received],
  });
  const ack = await TOOLS["acknowledge-board-answer"].handler(fake, sponsoredRobot, {
    idempotency_key: "00000571-0000-4000-8000-000000000030", answer_id: answerId,
    asker_ref: "orchestrator:project", base_version: 1,
  });
  assert.equal(ack.answer.status, "Received");
  const [sql, params] = fake.calls.find(([query]) => query.includes("update board_answer a set received_at"));
  assert.match(sql, /received_for_ref/);
  assert.match(sql, /version=\$6/);
  assert.deepEqual(params, [sponsoredRobot.id, "orchestrator:project", "carr-internal", "joe", answerId, 1]);
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
    idempotency_key: "00000571-0000-4000-8000-000000000031", board_id: "project", question_id: "q1",
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
        idempotency_key: "00000571-0000-4000-8000-000000000032", board_id: "project", question_id: "q1",
        base_version: 1, answer_text: "Proceed",
      }),
    error => error instanceof ToolError && error.payload.error === "board_answer_requires_human_partner",
  );
  assert.deepEqual(fake.calls, []);
});

const answerArgs = {
  idempotency_key: "00000571-0000-4000-8000-000000000033", board_id: "project", question_id: "q1",
  base_version: 1, answer_text: "Proceed",
};

for (const [label, actor] of [
  ["Joe-sponsored agent", sponsoredRobot],
  ["Dell-sponsored agent", { ...sponsoredRobot, sponsoring_human_slug: "dell" }],
  ["unattended runner", { ...sponsoredRobot, slug: "joe-local", unattended: true, via: "unattended-token" }],
  ["reviewer", { ...robot, review: true }],
  ["unsponsored machine", robot],
]) {
  test(`board answer dispatch refuses ${label} before database access`, async () => {
    const fake = new Fake();
    await assert.rejects(() => executeRegisteredTool(fake, actor, "answer-board-question", { ...answerArgs }),
      error => error instanceof ToolError && error.payload.error === "human_only_verb_requires_verified_partner");
    assert.deepEqual(fake.calls, []);
  });
}

for (const actor of [joe, dell]) {
  test(`board answer dispatch records human ${actor.slug} and their current question revision`, async () => {
    const fake = new Fake({
      "from board_question": [{ question_id: "q1", revision: 1, choices: ["Proceed"],
        allow_free_text: false, default_answer: "Wait", asker_ref: "orchestrator:project" }],
      "insert into board_answer": params => [{ id: "answer-1", question_id: params[3],
        question_revision: params[4], answer_text: params[6], answered_by: params[7],
        answered_by_actor_id: params[8], default_overridden: params[9], status: "Sent" }],
    });
    const result = await executeRegisteredTool(fake, actor, "answer-board-question", { ...answerArgs });
    assert.deepEqual(result.answer, { id: "answer-1", question_id: "q1", question_revision: 1,
      answer_text: "Proceed", answered_by: actor.slug, answered_by_actor_id: actor.id,
      default_overridden: true, status: "Sent" });
    const [, params] = fake.calls.find(([sql]) => sql.includes("from board_question"));
    assert.deepEqual(params, ["carr-internal", actor.slug, "project", "q1", 1]);
  });
}

test("board answer discovery requires human presence while publication keeps sponsor authority", () => {
  const rows = mcpInventory(TOOLS);
  assert.equal(rows.find(row => row.operation === "answer-board-question").principal_mode,
    "server_verified_human");
  assert.equal(rows.find(row => row.operation === "publish-board-snapshot").principal_mode,
    "authenticated_registered_principal");
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
  const schema = readMigration(new URL("../../migrations/0740_board_answers.sql", import.meta.url));
  for (const table of ["board_snapshot", "board_question", "board_answer"])
    assert.match(schema, new RegExp(`create table public\\.${table}`));
  for (const table of ["board_snapshot", "board_question", "board_answer"]) {
    assert.match(schema, new RegExp(`on public\\.${table} for each row execute function ops\\.scac_reference_monitor_guard\\(\\)`));
    assert.match(schema, new RegExp(`on public\\.${table} for each statement execute function ops\\.scac_reference_monitor_guard\\(\\)`));
  }
  assert.match(schema, /unique \(organization_tenant_id,sponsoring_human_slug,board_id,question_id,question_revision\)/i);
});

test('hierarchy snapshots preserve evidence beyond the old display budget with a bounded v4 limit', async () => {
  const run = snapshot => TOOLS['publish-board-snapshot'].handler(new Fake(), sponsoredRobot, {
    board_id: 'hierarchy', base_version: 1, idempotency_key: '00000571-0000-4000-8000-000000000034', snapshot,
  });
  await assert.rejects(() => run({ schema: 'carr-progress-board.v4', evidence: 'x'.repeat(300000) }),
    error => error.payload?.error === 'board_version_conflict');
  await assert.rejects(() => run({ schema: 'carr-progress-board.v4', evidence: 'x'.repeat(2097152) }),
    error => error.payload?.error === 'board_snapshot_invalid');
  await assert.rejects(() => run({ schema: 'carr-progress-board.v3', evidence: 'x'.repeat(300000) }),
    error => error.payload?.error === 'board_snapshot_invalid');
});

test("board publication replies with a safe numeric version and refuses an unsafe one before its receipt", async () => {
  const publish = (version) => {
    const fake = new Fake({ "update board_snapshot": [{ id: "9007199254740993", board_id: "project", version }] });
    return [fake, TOOLS["publish-board-snapshot"].handler(fake, joe, { board_id: "project", base_version: 2,
      snapshot: { version: "display-version" }, idempotency_key: derivedIdempotencyKey("test", `safe-${version}`) })];
  };
  for (const version of ["3", "9007199254740991"])
    assert.deepEqual(await publish(version)[1], { ok: true, board_id: "project", version: Number(version) });
  for (const version of ["9007199254740992", "0", "1.5", null]) {
    const [fake, run] = publish(version);
    await assert.rejects(run, error => error instanceof ToolError && error.payload.error === "board_version_invalid");
    assert.ok(!fake.calls.some(([sql]) => sql.includes("insert into tool_call")), "no receipt for a refused reply");
  }
});
