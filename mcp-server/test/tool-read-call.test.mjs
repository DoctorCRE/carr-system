// tool-read-call.test.mjs — coverage for Phase 1's read-call recording
// (migration 0108, mcp.js's callTool read branch). withEnvelope() in tools.js
// was the ONLY writer of tool_call, and it only ran inside WRITE handler
// bodies — every read verb, including the boot call (standing-context),
// reached executeRegisteredTool directly with no record left anywhere. This
// file proves the fix: a read verb records exactly one row with correct
// identity fields, a failed read still records (with ok:false), no response
// body or argument value is ever stored, and a logging failure never
// surfaces to the caller.
//
// Run with: node --test mcp-server/test/tool-read-call.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { ToolError } from "../src/tools.js";
import { callTool, readCallInsertSQL, recordReadCall, scheduleWriterReadCall } from "../src/mcp.js";

const JOE = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "claude" };

// ────────────────────────────────────────────────────────────────────────
// readCallInsertSQL — pure, no DB. Proves the statement shape and that it
// structurally CANNOT carry an argument value or a response body: the
// function's only inputs are (actor, verb, ok, errorKind).
// ────────────────────────────────────────────────────────────────────────

test("readCallInsertSQL: targets tool_read_call and carries the identity columns tool_call already uses", () => {
  const { text, params } = readCallInsertSQL(JOE, "standing-context", true, null);
  assert.match(text, /insert into tool_read_call/);
  assert.match(text, /verb, actor_slug, actor_id, ok, error_kind, via, client_id/);
  assert.match(text, /organization_tenant_id, sponsoring_human_slug, personal_scope, authorization_class/);
  // params order: verb, actor_slug (also feeds the actor_id subquery via $2),
  // ok, error_kind, via, client_id, org_tenant, sponsoring_slug, personal_scope, auth_class
  assert.deepEqual(params, [
    "standing-context", "joe", true, null, "oauth-google", "claude",
    "carr-internal", "joe", "joe-personal", "verified_partner",
  ]);
});

test("readCallInsertSQL: a failed call carries ok:false and a short error_kind, never a raw message", () => {
  const { params } = readCallInsertSQL(JOE, "get-deal-room", false, "not_a_deal");
  assert.equal(params[2], false);
  assert.equal(params[3], "not_a_deal");
});

test("readCallInsertSQL: the function's own signature proves no argument value or response body can reach it", () => {
  assert.equal(readCallInsertSQL.length, 4); // (actor, verb, ok, errorKind) — nothing else
});

test("readCallInsertSQL: reads carry no idempotency_key — that column belongs to tool_call's write-replay contract only", () => {
  const { text } = readCallInsertSQL(JOE, "find", true, null);
  assert.doesNotMatch(text, /idempotency_key/);
  assert.doesNotMatch(text, /request_hash/);
  assert.doesNotMatch(text, /response/);
});

// ────────────────────────────────────────────────────────────────────────
// recordReadCall — DI'd insert function, no network. Proves exactly one row
// is written per call, a failed read still writes a row, and a logging
// failure is swallowed rather than propagated.
// ────────────────────────────────────────────────────────────────────────

test("recordReadCall: a successful read records exactly one row with correct identity fields", async () => {
  const calls = [];
  const insertFn = async (text, params) => { calls.push({ text, params }); return []; };
  await recordReadCall(insertFn, JOE, "standing-context", true, null);
  assert.equal(calls.length, 1);
  assert.match(calls[0].text, /insert into tool_read_call/);
  assert.equal(calls[0].params[0], "standing-context"); // verb
  assert.equal(calls[0].params[1], "joe");               // actor_slug
  assert.equal(calls[0].params[2], true);                // ok
  assert.equal(calls[0].params[3], null);                // error_kind
});

test("recordReadCall: a failed read still records, with ok:false and the classified error_kind", async () => {
  const calls = [];
  const insertFn = async (text, params) => { calls.push({ text, params }); return []; };
  await recordReadCall(insertFn, JOE, "get-deal-room", false, "not_a_deal");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].params[2], false);
  assert.equal(calls[0].params[3], "not_a_deal");
});

test("recordReadCall: never stores a response body or an argument value — the insert function only ever sees what readCallInsertSQL built", async () => {
  const calls = [];
  const insertFn = async (text, params) => { calls.push({ text, params }); return []; };
  await recordReadCall(insertFn, JOE, "find", true, null);
  const serialized = JSON.stringify(calls[0].params);
  assert.doesNotMatch(serialized, /deal_id|premises|secret|argument/);
});

test("recordReadCall: an insert failure is swallowed, never thrown — a logging failure must never surface as a read failure", async () => {
  const insertFn = async () => { throw new Error("network blip"); };
  await assert.doesNotReject(recordReadCall(insertFn, JOE, "standing-context", true, null));
});

test("notification-feed writer read: one synthetic call yields one metadata-only audit row", async () => {
  const rows = [], pending = [];
  const scheduled = scheduleWriterReadCall(
    { writerConnection: true, write: false }, JOE, "notification-feed", true, null,
    promise => pending.push(promise), async (text, params) => { rows.push({ text, params }); },
  );
  assert.equal(scheduled, true);
  assert.equal(pending.length, 1);
  await Promise.all(pending);
  assert.equal(rows.length, 1);
  assert.match(rows[0].text, /insert into tool_read_call/);
  assert.deepEqual(rows[0].params.slice(0, 4), ["notification-feed", "joe", true, null]);
  assert.equal(rows[0].params.length, 10, "only identity and outcome metadata reach the audit row");
});

test("mutating writer: synthetic calls never create a read-call row", async () => {
  const rows = [], pending = [];
  const insert = async () => { rows.push(true); };
  for (const tool of [{ write: true }, { writerConnection: true, write: true }]) {
    assert.equal(scheduleWriterReadCall(tool, JOE, "acknowledge-notification", true, null,
      promise => pending.push(promise), insert), false);
  }
  assert.equal(pending.length, 0);
  assert.equal(rows.length, 0);
});

test("R03 standing report keeps the migration proof's existing CSV fields in place", async () => {
  const { readFile } = await import("node:fs/promises");
  const sql = await readFile(new URL("../../ops/r03-human-queue-health.sql", import.meta.url), "utf8");
  const projection = sql.slice(sql.lastIndexOf("\nselect r.slug as recipient,"),
    sql.lastIndexOf("\n  from recipients r"));
  const columns = [...projection.matchAll(/\bas\s+([a-z_]+)\b/g)].map(match => match[1]);
  assert.deepEqual(columns, [
    "recipient", "eligible_actions", "persisted_notifications", "in_app_rows",
    "acknowledged", "unnotified", "missing_in_app_rows", "missing_source_event",
    "failed_attempts", "unresolved_failed_attempts", "deduped_attempts",
    "deferred_excluded", "unnotified_age_unknown", "oldest_unnotified_days",
    "breach_response", "device_rows", "feed_call_observations",
    "successful_feed_call_observations", "latest_feed_call_at",
  ]);
});

// ────────────────────────────────────────────────────────────────────────
// callTool's read branch — proves the wiring: recording is scheduled via
// ctx.waitUntil (never blocking the caller), fires for a successful read,
// fires for a failed read too, and is skipped cleanly (never throws) when
// no writer credential is present — the exact shape call-verb-passthrough's
// and sponsor-runtime's existing env={} tests already rely on.
// ────────────────────────────────────────────────────────────────────────

function fakeReaderClientEnv({ readerRows = [] } = {}) {
  const waited = [];
  return {
    env: {
      // neon() validates URL SHAPE at construction even though list-verbs'
      // handler never calls .query() — a syntactically valid but unreachable
      // host is enough; nothing here ever dials out.
      DATABASE_URL_READER: "postgres://fake:fake@localhost.invalid/fake",
      DATABASE_URL_WRITER: undefined, // no writer credential in this fake — recording must no-op, not throw
      ctx: { waitUntil: (p) => waited.push(p) },
    },
    waited,
  };
}

test("callTool read branch: never throws when DATABASE_URL_WRITER is absent, and the read itself still succeeds normally", async () => {
  // list-verbs is a read verb with no DB dependency in its handler, so this
  // exercises the wrapping try/finally without needing a real connection.
  const { env, waited } = fakeReaderClientEnv();
  const result = await callTool(env, JOE, "list-verbs", {}, "full");
  assert.equal(result.ok, true);
  assert.ok(Array.isArray(result.verbs) && result.verbs.length > 0);
  assert.equal(waited.length, 0); // nothing scheduled — no writer credential to record through
});

// ────────────────────────────────────────────────────────────────────────
// Actor-scoped read doors take a read-only writer transaction, so the ordinary
// reader branch alone cannot measure notification-feed use. Source assertion,
// same convention sponsor-runtime.test.mjs uses for a dispatch seam that
// cannot be invoked without a live database.
// ────────────────────────────────────────────────────────────────────────

test("mcp.js: both read connection routes record metadata, while write verbs do not", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/mcp.js", import.meta.url), "utf8");
  const writerBranch = src.slice(src.indexOf("const needsAuthority = requiresAuthorityConnection"),
    src.indexOf("export async function dispatch("));
  assert.match(src, /if \(connectionRouteForTool\(tool\) === "reader"\)[\s\S]*?waitUntil\?\.\(recordReadCall\(/,
    "ordinary reader calls must still be recorded");
  assert.match(writerBranch, /const writerRead = tool\.writerConnection === true && !tool\.write/,
    "only read-only writer calls are eligible for the second audit path");
  assert.match(writerBranch, /begin read only/,
    "audited writer reads must keep their read-only transaction");
  assert.match(writerBranch, /if \(writerRead && env\?\.DATABASE_URL_WRITER\)[\s\S]*?scheduleWriterReadCall\(/,
    "recording must be detached from the response and guarded against writes");
});
