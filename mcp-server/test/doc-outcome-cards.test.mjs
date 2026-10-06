import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { TOOLS, docOutcomeCardsProjection } from "../src/tools.js";

class ToolError extends Error { constructor(x) { super(x.error); Object.assign(this, x); } }
const card = routing_state => ({ routing_state, native_task_id: { value: null, unavailable_reason: "no_authoritative_native_task_join" }, session_entry: { auto_launch: false } });

test("outcome cards are a closed actor-context read", () => {
  const tool = TOOLS["read-doc-outcome-cards"];
  assert.equal(tool.write, undefined);
  assert.equal(tool.writerConnection, true);
  assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(), ["cursor", "limit"]);
});

test("all B09 states preserve unavailable native task without launch", () => {
  const facts = { ok: true, schema_version: "doc-outcome-cards.v2", cards: ["queued", "active", "waiting", "failed", "unknown", "verified"].map(card) };
  assert.deepEqual(docOutcomeCardsProjection(facts, ToolError), { ...facts, more: false, next_cursor: null, as_of: undefined, correlation_version: undefined });
});

test("invented state or auto launch is refused", () => {
  assert.throws(() => docOutcomeCardsProjection({ ok: true, schema_version: "doc-outcome-cards.v2", cards: [card("done")] }, ToolError), /invalid/);
  assert.throws(() => docOutcomeCardsProjection({ ok: true, schema_version: "doc-outcome-cards.v2", cards: [{ ...card("active"), session_entry: { auto_launch: true } }] }, ToolError), /invalid/);
});

test("SQL cursor, attempts, and fallback stay source-bound", () => {
  const sql = fs.readFileSync(new URL("../../migrations/0546_read_doc_outcome_cards_successor.sql", import.meta.url), "utf8");
  assert.match(sql, /from page p where p\.rn=page_limit/);
  assert.doesNotMatch(sql, /max\(updated_at\).*max\(id::text\)/s);
  assert.match(sql, /ops\.job_attempt x[\s\S]*x\.job_id=j\.id and x\.attempt=j\.attempt/);
  assert.match(sql, /'job-attempt:'\|\|job_attempt_id::text/);
  assert.match(sql, /j\.id job_id,j\.state job_state/);
  assert.match(sql, /ops\.capability_agent_session s on s\.id=e\.agent_session_id and s\.work_request_id=w\.id/);
  assert.match(sql, /when job_state in \('failed','timed_out','cancelled','dead_lettered'\) then 'failed'[\s\S]*when job_state='succeeded' and state in \('confirmed_closed','released'\) then 'verified'/);
  assert.match(sql, /native_surface in \('codex_desktop','claude_desktop'\)[\s\S]*session_state in \('cancelled','completed'\)/);
  assert.match(sql, /x\.issued_at<=as_of and x\.created_at<=as_of[\s\S]*y\.issued_at<=as_of and y\.created_at<=as_of[\s\S]*j\.created_at<=as_of and j\.updated_at<=as_of[\s\S]*s\.created_at<=as_of and s\.updated_at<=as_of[\s\S]*x\.started_at<=as_of/);
});
