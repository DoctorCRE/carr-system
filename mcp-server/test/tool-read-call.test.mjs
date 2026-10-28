// tool-read-call.test.mjs — coverage for read-call counting (0108's read
// trace, a per-day counter since 1032). withEnvelope() in tools.js records
// writes only, so this is the only trace a read verb leaves. This file proves a
// read verb bumps exactly one counter keyed by (day, verb, sponsor, via, ok), a
// failed read is still counted (ok:false), no response body or argument value
// can reach the statement, the per-call actor lookup is gone, and a counting
// failure never surfaces to the caller.
//
// Run with: node --test mcp-server/test/tool-read-call.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { callTool, readCallCountSQL, recordReadCall, scheduleWriterReadCall } from "../src/mcp.js";

const JOE = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "claude" };

// ────────────────────────────────────────────────────────────────────────
// readCallCountSQL — pure, no DB. Proves the statement shape and that it
// structurally CANNOT carry an argument value or a response body: the
// function's only inputs are (actor, verb, ok).
// ────────────────────────────────────────────────────────────────────────

test("readCallCountSQL: upserts one daily tool_read_count counter keyed by day, verb, sponsor, via and ok", () => {
  const { text, params } = readCallCountSQL(JOE, "standing-context", true);
  assert.match(text, /insert into tool_read_count as c \(day, verb, sponsoring_human_slug, via, ok, n, last_at\)/);
  assert.match(text, /\(now\(\) at time zone 'UTC'\)::date/);
  assert.match(text, /on conflict on constraint tool_read_count_key/);
  assert.match(text, /do update set n = c\.n \+ 1, last_at = greatest\(c\.last_at, excluded\.last_at\)/);
  assert.deepEqual(params, ["standing-context", "joe", "oauth-google", true]);
});

test("readCallCountSQL: no per-call actor lookup and no tool_read_call row", () => {
  const { text } = readCallCountSQL(JOE, "standing-context", true);
  assert.doesNotMatch(text, /from actor|actor_id|tool_read_call/);
});

test("readCallCountSQL: a failed call is counted under ok:false", () => {
  const { params } = readCallCountSQL(JOE, "get-deal-room", false);
  assert.equal(params[3], false);
});

test("readCallCountSQL: a caller with no sponsor or door label is still counted under null keys", () => {
  const { params } = readCallCountSQL({ slug: "codex", human: false }, "find", true);
  assert.deepEqual(params, ["find", null, null, true]);
});

test("readCallCountSQL: the function's own signature proves no argument value or response body can reach it", () => {
  assert.equal(readCallCountSQL.length, 3); // (actor, verb, ok) — nothing else
});

test("readCallCountSQL: reads carry no idempotency_key — that column belongs to tool_call's write-replay contract only", () => {
  const { text } = readCallCountSQL(JOE, "find", true);
  assert.doesNotMatch(text, /idempotency_key/);
  assert.doesNotMatch(text, /request_hash/);
  assert.doesNotMatch(text, /response/);
});

// ────────────────────────────────────────────────────────────────────────
// recordReadCall — DI'd insert function, no network. Proves exactly one
// statement per call, a failed read is still counted, and a counting failure
// is swallowed rather than propagated.
// ────────────────────────────────────────────────────────────────────────

test("recordReadCall: a successful read sends exactly one counter upsert", async () => {
  const calls = [];
  const insertFn = async (text, params) => { calls.push({ text, params }); return []; };
  await recordReadCall(insertFn, JOE, "standing-context", true);
  assert.equal(calls.length, 1);
  assert.match(calls[0].text, /insert into tool_read_count/);
  assert.deepEqual(calls[0].params, ["standing-context", "joe", "oauth-google", true]);
});

test("recordReadCall: a failed read is still counted, with ok:false", async () => {
  const calls = [];
  const insertFn = async (text, params) => { calls.push({ text, params }); return []; };
  await recordReadCall(insertFn, JOE, "get-deal-room", false);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].params[3], false);
});

test("recordReadCall: never stores a response body or an argument value — the insert function only ever sees what readCallCountSQL built", async () => {
  const calls = [];
  const insertFn = async (text, params) => { calls.push({ text, params }); return []; };
  await recordReadCall(insertFn, JOE, "find", true);
  const serialized = JSON.stringify(calls[0].params);
  assert.doesNotMatch(serialized, /deal_id|premises|secret|argument/);
});

test("recordReadCall: an insert failure is swallowed, never thrown — a counting failure must never surface as a read failure", async () => {
  const insertFn = async () => { throw new Error("network blip"); };
  await assert.doesNotReject(recordReadCall(insertFn, JOE, "standing-context", true));
});

test("notification-feed writer read: one synthetic call bumps one metadata-only counter", async () => {
  const rows = [], pending = [];
  const scheduled = scheduleWriterReadCall(
    { writerConnection: true, write: false }, JOE, "notification-feed", true,
    promise => pending.push(promise), async (text, params) => { rows.push({ text, params }); },
  );
  assert.equal(scheduled, true);
  assert.equal(pending.length, 1);
  await Promise.all(pending);
  assert.equal(rows.length, 1);
  assert.match(rows[0].text, /insert into tool_read_count/);
  assert.deepEqual(rows[0].params, ["notification-feed", "joe", "oauth-google", true],
    "only sponsor, door and outcome reach the counter");
});

test("mutating writer: synthetic calls never create a read-call row", async () => {
  const rows = [], pending = [];
  const insert = async () => { rows.push(true); };
  for (const tool of [{ write: true }, { writerConnection: true, write: true }]) {
    assert.equal(scheduleWriterReadCall(tool, JOE, "acknowledge-notification", true,
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
  const writerBranch = src.slice(src.indexOf("const needsAuthority = tool.authorityOnly"),
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
