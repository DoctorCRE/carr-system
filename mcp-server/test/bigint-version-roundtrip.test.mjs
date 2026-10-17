import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-4000-8000-000000000002", slug: "joe", human: true };
const memoryId = "02970000-0000-4000-8000-000000000010";
const successorId = "02970000-0000-4000-8000-000000000011";
const answerId = "20000000-0000-4000-8000-000000000001";

class Fake {
  constructor(plan = {}) { this.plan = plan; this.receipts = new Map(); }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.includes("select request_hash, response"))
      return { rows: this.receipts.has(params[0]) ? [this.receipts.get(params[0])] : [] };
    if (sql.includes("insert into tool_call")) {
      this.receipts.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    for (const [fragment, value] of Object.entries(this.plan))
      if (sql.includes(fragment)) return { rows: typeof value === "function" ? value(params) : structuredClone(value) };
    return { rows: [] };
  }
}

test("a published board version can be read and passed straight back to publish", async () => {
  const fake = new Fake({
    "from board_snapshot": [{ board_id: "project", version: "2", snapshot_json: { version: "display-value" } }],
    "update board_snapshot": params => {
      assert.equal(typeof params[5], "number");
      return [{ id: "9007199254740993", board_id: "project", version: String(params[5] + 1) }];
    },
  });
  const read = await TOOLS["read-progress-board"].handler(fake, joe, { board_id: "project" });
  const args = {
    idempotency_key: "board-round-trip", board_id: "project",
    base_version: read.snapshot.version, snapshot: read.snapshot.snapshot_json,
  };
  const out = await TOOLS["publish-board-snapshot"].handler(fake, joe, args);
  fake.receipts.get(args.idempotency_key).response.snapshot.version = "3";
  const replay = await TOOLS["publish-board-snapshot"].handler(fake, joe, args);
  const next = await TOOLS["publish-board-snapshot"].handler(fake, joe, {
    ...args, idempotency_key: "board-after-replay", base_version: replay.snapshot.version,
  });
  assert.equal(read.snapshot.version, 2);
  assert.equal(read.snapshot.snapshot_json.version, "display-value");
  assert.equal(out.snapshot.version, 3);
  assert.equal(out.snapshot.id, "9007199254740993");
  assert.equal(replay.replayed, true);
  assert.equal(replay.snapshot.version, 3);
  assert.equal(next.snapshot.version, 4);
});

test("profile versions round-trip from the roster through successive assignments", async () => {
  const fake = new Fake({
    "from agent_profile": [{ profile_key: "builder", version: "1" }],
    "update agent_profile": params => {
      assert.equal(typeof params[4], "number");
      return [{ id: "9007199254740993", profile_key: "builder", version: String(params[4] + 1) }];
    },
    "insert into agent_profile_assignment": [{ id: "9007199254740994" }],
    "insert into partner_room_turn": [{ id: "9007199254740995", at: "now" }],
  });
  const read = await TOOLS["read-profiles"].handler(fake, joe, {});
  const args = { profile_key: "builder", model: "model", status: "active" };
  const first = {
    ...args, idempotency_key: "profile-first", base_version: read.profiles[0].version,
  };
  const assigned = await TOOLS["assign-profile"].handler(fake, joe, first);
  fake.receipts.get(first.idempotency_key).response.profile.version = "2";
  const replay = await TOOLS["assign-profile"].handler(fake, joe, first);
  const next = await TOOLS["assign-profile"].handler(fake, joe, {
    ...args, idempotency_key: "profile-second", base_version: replay.profile.version,
  });
  assert.equal(read.profiles[0].version, 1);
  assert.equal(assigned.profile.version, 2);
  assert.equal(replay.replayed, true);
  assert.equal(replay.profile.version, 2);
  assert.equal(next.profile.version, 3);
  assert.equal(next.profile.id, "9007199254740993");
  assert.equal(next.assignment_id, "9007199254740994");
  assert.equal(next.receipt_seq, "9007199254740995");
});

test("memory review and recall versions can be reused for lifecycle writes", async () => {
  const fake = new Fake({
    "from memory_item m": [{ id: memoryId, version: "1", status: "candidate", work_request_version: "7", evidence: [] }],
    "select id, kind, statement": [{ id: memoryId, version: "2", work_request_version: null, status: "promoted" }],
    "set status='promoted'": params => {
      assert.equal(params[1], 1);
      return [{ id: memoryId, version: "2", status: "promoted" }];
    },
    "set status='corrected'": params => {
      assert.equal(params[1], 2);
      return [{ id: memoryId, version: "3", status: "corrected", kind: "preference" }];
    },
    "insert into memory_item": [{ id: successorId, version: "1", status: "candidate", predecessor_id: memoryId }],
    "set status='forgotten'": params => {
      assert.equal(params[1], 1);
      return [{ id: successorId, version: "2", status: "forgotten" }];
    },
    "insert into memory_evidence": [{ id: "9007199254740993" }],
  });
  const review = await TOOLS["review-memory"].handler(fake, joe, { memory_id: memoryId });
  const promoted = await TOOLS["promote-memory"].handler(fake, joe, {
    idempotency_key: "memory-promote", memory_id: memoryId, base_version: review.memory.version, reason: "confirmed",
  });
  const recalled = await TOOLS["recall-memory"].handler(fake, joe, { query: "preference" });
  const correctionArgs = {
    idempotency_key: "memory-correct", memory_id: memoryId, base_version: recalled.memories[0].version,
    statement: "Use concise updates", reason: "corrected",
  };
  const corrected = await TOOLS["correct-memory"].handler(fake, joe, correctionArgs);
  fake.receipts.get(correctionArgs.idempotency_key).response.memory.version = "1";
  const replay = await TOOLS["correct-memory"].handler(fake, joe, correctionArgs);
  const forgotten = await TOOLS["forget-memory"].handler(fake, joe, {
    idempotency_key: "memory-forget", memory_id: replay.memory.id,
    base_version: replay.memory.version, reason: "no longer applies",
  });
  assert.equal(review.memory.version, 1);
  assert.equal(review.memory.work_request_version, 7);
  assert.equal(promoted.memory.version, 2);
  assert.equal(recalled.memories[0].version, 2);
  assert.equal(recalled.memories[0].work_request_version, null);
  assert.equal(corrected.memory.version, 1);
  assert.equal(replay.replayed, true);
  assert.equal(replay.memory.version, 1);
  assert.equal(corrected.evidence_id, "9007199254740993");
  assert.equal(forgotten.memory.version, 2);
});

test("board question revisions and answer versions round-trip through their writes", async () => {
  let revision = "1", answer = null;
  const fake = new Fake({
    "insert into board_question": params => {
      revision = String(params[4] === "Updated?" ? 1 : params[4]);
      return [{ id: "9007199254740993", question_id: "q1", revision }];
    },
    "from board_question q": () => [{ question_id: "q1", revision, answer_version: answer?.version ?? null }],
    "update board_question": params => {
      assert.equal(params[4], 1);
      return [{ revision }];
    },
    "from board_question where": params => {
      assert.equal(params[4], 2);
      return [{ question_id: "q1", revision, choices: [], allow_free_text: true,
        default_answer: null, asker_ref: "orchestrator:project" }];
    },
    "insert into board_answer": () => {
      answer = { id: answerId, version: "1", question_revision: revision, cursor: "9007199254740993" };
      return [answer];
    },
    "from board_answer a where": () => answer ? [answer] : [],
    "update board_answer a set received_at": params => {
      assert.equal(params[5], 1);
      answer = { ...answer, version: "2", status: "Received" };
      return [answer];
    },
    "update board_answer a set applied_at": params => {
      assert.equal(params[5], 2);
      answer = { ...answer, version: "3", status: "Applied" };
      return [answer];
    },
  });
  const question = { board_id: "project", question_id: "q1", prompt: "Updated?",
    asker_ref: "orchestrator:project", allow_free_text: true };
  const asked = await TOOLS["ask-board-question"].handler(fake, joe, {
    ...question, idempotency_key: "question-ask", base_version: 0,
  });
  const read = await TOOLS["read-progress-board"].handler(fake, joe, { board_id: "project" });
  assert.equal(read.questions[0].answer_version, null);
  const revised = await TOOLS["revise-board-question"].handler(fake, joe, {
    ...question, idempotency_key: "question-revise", base_version: read.questions[0].revision,
  });
  const sent = await TOOLS["answer-board-question"].handler(fake, joe, {
    board_id: "project", question_id: "q1", answer_text: "Proceed",
    idempotency_key: "question-answer", base_version: revised.question.revision,
  });
  const inbox = await TOOLS["read-board-answers"].handler(fake, joe, {
    after_cursor: 0, asker_ref: "orchestrator:project",
  });
  const received = await TOOLS["acknowledge-board-answer"].handler(fake, joe, {
    idempotency_key: "answer-received", answer_id: inbox.answers[0].id,
    asker_ref: "orchestrator:project", base_version: inbox.answers[0].version,
  });
  const applied = await TOOLS["record-board-answer-applied"].handler(fake, joe, {
    idempotency_key: "answer-applied", answer_id: received.answer.id,
    base_version: received.answer.version, effect_ref: "pr:1854",
  });
  const finalRead = await TOOLS["read-progress-board"].handler(fake, joe, { board_id: "project" });
  assert.equal(asked.question.revision, 1);
  assert.equal(revised.question.revision, 2);
  assert.equal(sent.answer.question_revision, 2);
  assert.equal(sent.answer.version, 1);
  assert.equal(inbox.answers[0].question_revision, 2);
  assert.equal(inbox.answers[0].version, 1);
  assert.equal(inbox.answers[0].cursor, "9007199254740993");
  assert.equal(received.answer.version, 2);
  assert.equal(applied.answer.version, 3);
  assert.equal(finalRead.questions[0].answer_version, 3);
});

for (const name of ["assign-profile", "promote-memory", "correct-memory", "forget-memory"]) {
  test(`${name} refuses unsafe or nonnumeric base versions before database use`, async () => {
    const fake = { query: async () => { throw new Error("database must not be queried"); } };
    const args = name === "assign-profile"
      ? { profile_key: "builder", model: "model", status: "active" }
      : { memory_id: memoryId, statement: "Updated", reason: "confirmed" };
    for (const base_version of [9007199254740992, "1", 0, -1, 1.5, null, undefined])
      await assert.rejects(() => TOOLS[name].handler(fake, joe, {
        ...args, idempotency_key: `invalid-${name}`, base_version,
      }), error => error instanceof ToolError && error.payload.error ===
        (name === "assign-profile" ? "profile_base_version_invalid" : "memory_base_version_invalid"));
  });
}

test("a question revision at MAX_SAFE_INTEGER refuses an unsafe increment before database use", async () => {
  const fake = { query: async () => { throw new Error("database must not be queried"); } };
  await assert.rejects(() => TOOLS["revise-board-question"].handler(fake, joe, {
    idempotency_key: "revision-exhausted", board_id: "project", question_id: "q1",
    base_version: 9007199254740991, prompt: "Updated?", asker_ref: "orchestrator:project",
  }), error => error instanceof ToolError && error.payload.error === "board_base_version_invalid");
});
