// Synthetic contract evidence for the filer's chosen existing storage verb.
import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";

const actor = { id: "00000000-0000-4000-8000-000000000010", slug: "synthetic-builder",
  display: "Synthetic builder", human: false, via: "local-token", client_id: "codex" };
const repoId = "00000000-0000-4000-8000-000000000020";
const flagId = "00000000-0000-4000-8000-000000000030";

class FindingStore {
  calls = new Map();
  findings = [];
  events = [];
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response"))
      return { rows: this.calls.has(params[0]) ? [this.calls.get(params[0])] : [] };
    if (sql.includes("to_regclass('public.code_subject')"))
      return { rows: [{ registry: true, read_side: true }] };
    if (sql.startsWith("select id from code_subject")) {
      assert.equal(params[0], "jbookout/doctorcre-app");
      assert.equal(params[1], null);
      return { rows: [{ id: repoId }] };
    }
    if (sql.startsWith("insert into record_flag")) {
      this.findings.push({ subject_type: params[0], subject_id: params[1], kind: params[2],
        value: JSON.parse(params[3]), source: params[4], observed_at: params[5] });
      return { rows: [{ id: flagId, observed_at: params[5] }] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push(params);
      return { rows: [{ id: flagId }] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.calls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    throw new Error(`Unexpected synthetic query: ${sql}`);
  }
}

test("record-finding retains a multi-page Dot report with metadata and citations, then replays once", async () => {
  const db = new FindingStore();
  const text = "# Synthetic UX report\r\n" + "Synthetic café λ\r\n".repeat(15000)
    + "[Source](https://example.org/research)\r\n";
  const value = { job: "030-UX-synthetic", topic: "Synthetic UX report", date: "2026-01-01", text,
    citations: ["https://example.org/research"], claims_independently_verified: false };
  const args = { idempotency_key: "synthetic-dot-contract", subject: "repo:jbookout/doctorcre-app",
    kind: "research_report", internal: true, epistemic_status: "observed",
    source: "/synthetic/reports/030-UX-synthetic.txt", observed_at: "2026-01-01T00:00:00+00:00", value };
  const first = await TOOLS["record-finding"].handler(db, actor, args);
  assert.equal(first.ok, true);
  assert.equal(first.flag_id, flagId);
  assert.equal(db.findings.length, 1);
  // Read the stored artifact, including all full-text bytes and passed fields.
  const stored = db.findings[0];
  assert.equal(stored.subject_type, "repo");
  assert.equal(stored.subject_id, repoId);
  assert.equal(stored.source, args.source);
  assert.equal(stored.observed_at, args.observed_at);
  assert.deepEqual(stored.value, { found: true, ...value, internal: true, epistemic_status: "observed" });
  assert.equal(db.events.length, 1);
  const replay = await TOOLS["record-finding"].handler(db, actor, args);
  assert.equal(replay.replayed, true);
  assert.equal(replay.flag_id, flagId);
  assert.equal(db.findings.length, 1);
  assert.equal(db.events.length, 1);
  await assert.rejects(() => TOOLS["record-finding"].handler(db, actor,
    { ...args, value: { ...value, text: "changed report" } }), /key_reuse/);
});
