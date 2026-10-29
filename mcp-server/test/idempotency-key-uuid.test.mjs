// WR-000571 — the shared write envelope accepts only UUID idempotency keys.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";
import { publicError } from "../src/public-errors.ts";

const actor = { id: "00000000-0000-4000-8000-000000000010", slug: "synthetic-builder",
  display: "Synthetic builder", human: false, via: "local-token", client_id: "codex" };

function store() {
  const calls = [];
  return { calls, async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    calls.push(sql);
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) return { rows: [] };
    throw new Error(`Unexpected synthetic query: ${sql}`);
  } };
}
const args = (idempotency_key) => ({ idempotency_key, subject: "repo:jbookout/doctorcre-app",
  kind: "research_report", internal: true, epistemic_status: "observed", source: "/synthetic",
  observed_at: "2026-01-01T00:00:00+00:00", value: {} });

for (const bad of ["synthetic-fixed-key", "calcap-123", "12345", " 00000000-0000-4000-8000-000000000001", "not-a-uuid-at-all-0000-000000000000"]) {
  test(`write envelope refuses non-UUID idempotency_key ${JSON.stringify(bad)} before any query`, async () => {
    const db = store();
    await assert.rejects(TOOLS["record-finding"].handler(db, actor, args(bad)),
      (e) => {
        const shown = publicError(e, { inputSchema: TOOLS["record-finding"].inputSchema, fallback: "x" });
        return shown.error === "invalid_idempotency_key" && shown.fields?.includes("idempotency_key")
          && shown.hint.includes("idempotency_key");
      });
    assert.deepEqual(db.calls, []);
  });
}

test("write envelope still runs with a UUID idempotency_key", async () => {
  const db = store();
  await assert.rejects(TOOLS["record-finding"].handler(db, actor, args("00000571-0000-4000-8000-000000000128")),
    (e) => !/idempotency/.test(String(e.error)));
  assert.ok(db.calls.some((s) => s.startsWith("select request_hash, response")));
});

// The database reads these two RW02 key shapes back, so exactly them stay accepted.
test("the RW02 shapes the database reads back are accepted on their own verbs only", async () => {
  const pageStop = `rw02-read:${"a".repeat(24)}:page-stop:0`;
  const episode = `rw02-missing-joe:${"b".repeat(32)}:e2`;
  const refused = (verb, key) => TOOLS[verb].handler(store(), actor, { idempotency_key: key })
    .then(() => null, (e) => e.payload?.error);
  assert.notEqual(await refused("record-salesforce-page-stop", pageStop), "invalid_idempotency_key");
  assert.notEqual(await refused("add-loop", episode), "invalid_idempotency_key");
  assert.equal(await refused("record-finding", pageStop), "invalid_idempotency_key");
  assert.equal(await refused("record-salesforce-page-stop", `rw02-read:${"a".repeat(24)}:other`), "invalid_idempotency_key");
  assert.equal(await refused("add-loop", `rw02-missing-joe:${"b".repeat(32)}:e1`), "invalid_idempotency_key");
});
