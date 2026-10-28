// Tests what the tool_call replay ledger stores for a write's response.
import test from "node:test";
import assert from "node:assert/strict";
import { readMigration, migrationNames } from "./migration-source.testhelper.mjs";
import { REPLAY_RESPONSE_LIMIT, REPLAY_WINDOW, durableReceipt, replayReceipt } from "../src/replay-receipt.ts";

test("a response under the limit is stored whole", () => {
  const result = { ok: true, loop_id: "L-1", note: "x".repeat(100) };
  assert.deepEqual(JSON.parse(replayReceipt(result)), result);
});

test("an oversized response is stored as a small stand-in that keeps its identifiers", () => {
  const result = { ok: true, attach_id: "a1", version: 3, done: false, gone: null,
    stores: [{ index: "x".repeat(REPLAY_RESPONSE_LIMIT) }], long: "y".repeat(500) };
  const stored = replayReceipt(result);
  assert.ok(stored.length < 1024, "the stand-in is bounded");
  const receipt = JSON.parse(stored);
  assert.deepEqual(receipt, { ok: true, attach_id: "a1", version: 3, done: false, gone: null,
    response_omitted: { chars: JSON.stringify(result).length,
      reread: "the write already ran; read the record for its current state" } });
});

test("the replay window in code matches the one the database enforces", () => {
  const name = migrationNames().find(file => /_tool_call_retention\.sql$/.test(file));
  assert.ok(name, "the retention migration exists");
  const sql = readMigration(name);
  assert.equal(REPLAY_WINDOW, "30 days");
  assert.ok(sql.includes(`interval '${REPLAY_WINDOW}'`), "trigger and cleanup use the same window");
});

test("a receipt the database reads back by key outlives the replay window", () => {
  assert.equal(durableReceipt("add-loop", "rw02-missing-joe:" + "a".repeat(32)), true);
  assert.equal(durableReceipt("record-salesforce-page-stop", `rw02-read:${"b".repeat(24)}:page-stop:3`), true);
  for (const verb of ["review-and-triage", "accept-ready-plan", "accept-outcome-feedback"])
    assert.equal(durableReceipt(verb, "00000000-0000-4000-8000-000000000001"), true, verb);
  assert.equal(durableReceipt("add-loop", "00000000-0000-4000-8000-000000000001"), false);
  assert.equal(durableReceipt("publish-board-snapshot", "00000000-0000-4000-8000-000000000001"), false);
});
