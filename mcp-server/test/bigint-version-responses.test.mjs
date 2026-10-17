import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const actor = { id: "10000000-0000-4000-8000-000000000002", slug: "joe", human: true };
const id = "20000000-0000-4000-8000-000000000001";
const opaqueId = "9007199254740993";
const board = { board_id: "project" };
const question = { ...board, question_id: "question", prompt: "Choose", asker_ref: "desk" };
const cases = [
  ["read-profiles", {}, ["profiles", 0], "profile"],
  ["assign-profile", { profile_key: "builder", status: "parked", base_version: 2 }, ["profile"], "profile"],
  ["read-progress-board", board, ["snapshot"], "board"],
  ["read-board-answers", { after_cursor: 0, asker_ref: "desk" }, ["answers", 0], "board"],
  ["publish-board-snapshot", { ...board, base_version: 2, snapshot: {} }, ["snapshot"], "board"],
  ["ask-board-question", { ...question, base_version: 0 }, ["question"], "board"],
  ["revise-board-question", { ...question, base_version: 2 }, ["question"], "board"],
  ["answer-board-question", { ...board, question_id: "question", base_version: 2, answer_text: "Yes" }, ["answer"], "board"],
  ["acknowledge-board-answer", { answer_id: id, asker_ref: "desk", base_version: 2 }, ["answer"], "board"],
  ["record-board-answer-applied", { answer_id: id, base_version: 2, effect_ref: "effect" }, ["answer"], "board"],
  ["observe-memory", { kind: "fact", statement: "Synthetic fact", scope: "shared",
    evidence: { source_type: "human", observation: "Synthetic observation" } }, ["memory"], "memory"],
  ["recall-memory", { query: "synthetic" }, ["memories", 0], "memory"],
  ["review-memory", { memory_id: id }, ["memory"], "memory"],
  ["promote-memory", { memory_id: id, base_version: 2, reason: "Synthetic confirmation" }, ["memory"], "memory"],
  ["correct-memory", { memory_id: id, base_version: 2, statement: "Synthetic correction", reason: "Synthetic correction" }, ["memory"], "memory"],
  ["forget-memory", { memory_id: id, base_version: 2, reason: "Synthetic removal" }, ["memory"], "memory"],
];
const at = (object, path) => path.reduce((value, key) => value[key], object);

class Database {
  constructor(version) { this.version = version; this.receipt = null; this.writes = 0; }
  async query(sql, params = []) {
    if (sql.includes("select request_hash, response")) return { rows: this.receipt ? [this.receipt] : [] };
    if (sql.includes("insert into tool_call")) {
      this.receipt = { request_hash: params[3], response: JSON.parse(params[4]) };
      return { rows: [] };
    }
    if (/^\s*(insert|update)/i.test(sql)) this.writes++;
    return { rows: [{ id: opaqueId, cursor: opaqueId, version: this.version,
      revision: this.version, question_revision: this.version, answer_version: this.version,
      profile_key: "builder", status: "candidate", kind: "fact", scope: "shared",
      board_id: "project", question_id: "question", choices: [], allow_free_text: true,
      default_answer: null, asker_ref: "desk", evidence: [],
      snapshot_json: { version: "display-version", id: opaqueId },
      charter: ["2"], provenance: { version: "source-version" },
    }] };
  }
}

for (const [verb, input, path, family] of cases) {
  const args = { ...input, ...(TOOLS[verb].write ? { idempotency_key: `safe-version-${verb}` } : {}) };
  const run = db => TOOLS[verb].handler(db, actor, args);
  test(`${verb} returns safe numeric versions and preserves opaque IDs`, async () => {
    for (const version of ["2", "9007199254740991"]) {
      const result = await run(new Database(version));
      const record = at(result, path);
      assert.equal(record.version, Number(version));
      assert.equal(record.id, opaqueId);
      if (family === "board") {
        for (const field of ["revision", "question_revision", "answer_version"])
          assert.equal(record[field], Number(version));
        assert.equal(record.cursor, opaqueId);
        assert.deepEqual(record.snapshot_json, { version: "display-version", id: opaqueId });
      }
      if (family === "profile") assert.deepEqual(record.charter, ["2"]);
      if (family === "memory") assert.deepEqual(record.provenance, { version: "source-version" });
    }
  });
  test(`${verb} refuses unsafe or invalid output versions`, async () => {
    for (const version of ["9007199254740992", "9007199254740993", "0", "-1", "1.5", null]) {
      const db = new Database(version);
      await assert.rejects(() => run(db), error => error instanceof ToolError
        && error.payload.error === `${family}_version_invalid`);
      assert.equal(db.receipt, null);
    }
  });
  if (!TOOLS[verb].write) continue;
  test(`${verb} converts legacy replay versions without a second write`, async () => {
    const db = new Database("2");
    await run(db);
    const writes = db.writes;
    const stored = at(db.receipt.response, path);
    for (const version of ["2", "9007199254740991"]) {
      stored.version = version;
      if (family === "board")
        for (const field of ["revision", "question_revision", "answer_version"]) stored[field] = version;
      const result = await run(db);
      assert.equal(result.replayed, true);
      assert.equal(at(result, path).version, Number(version));
      assert.equal(at(result, path).id, opaqueId);
      assert.equal(db.writes, writes);
    }
    stored.version = "9007199254740993";
    await assert.rejects(() => run(db), error => error instanceof ToolError
      && error.payload.error === `${family}_version_invalid`);
    assert.equal(db.writes, writes);
  });
}
