// Test version round trips and unsafe increment refusal.
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
    idempotency_key: "18312413-26a0-52e6-a202-7bb70d659fae", board_id: "project",
    base_version: read.snapshot.version, snapshot: read.snapshot.snapshot_json,
  };
  const out = await TOOLS["publish-board-snapshot"].handler(fake, joe, args);
  fake.receipts.get(args.idempotency_key).response.snapshot.version = "3";
  const replay = await TOOLS["publish-board-snapshot"].handler(fake, joe, args);
  const next = await TOOLS["publish-board-snapshot"].handler(fake, joe, {
    ...args, idempotency_key: "d7bc9101-69e8-5bb7-bca9-e25c4cdb4df4", base_version: replay.snapshot.version,
  });
  assert.equal(read.snapshot.version, 2);
  assert.equal(read.snapshot.snapshot_json.version, "display-value");
  assert.equal(out.snapshot.version, 3);
  assert.equal(out.snapshot.id, "9007199254740993");
  assert.equal(replay.replayed, true);
  assert.equal(replay.snapshot.version, 3);
  assert.equal(next.snapshot.version, 4);
});



test("a question revision at MAX_SAFE_INTEGER refuses an unsafe increment before database use", async () => {
  const fake = { query: async () => { throw new Error("database must not be queried"); } };
  await assert.rejects(() => TOOLS["revise-board-question"].handler(fake, joe, {
    idempotency_key: "1a2a62de-5e87-5ed7-a5cf-b50c91ab37ee", board_id: "project", question_id: "q1",
    base_version: 9007199254740991, prompt: "Updated?", asker_ref: "orchestrator:project",
  }), error => error instanceof ToolError && error.payload.error === "board_base_version_invalid");
});
