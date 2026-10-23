import test from "node:test";
import assert from "node:assert/strict";
import { dispatch } from "../src/mcp.js";
import { ToolError, pgConstraintError } from "../src/tools.js";
import { Pool } from "@neondatabase/serverless";

const correlationId = "d170d000-0000-4000-8000-000000000031";
const privateText = 'SELECT * FROM private_table; constraint private_constraint; synthetic-private-row';

async function failure(t, error, { correlated = true, method = "tools/call", outer = false } = {}) {
  const logs = [];
  t.mock.method(console, "error", line => logs.push(JSON.parse(line)));
  const env = correlated ? { CORRELATION_ID: correlationId } : {};
  // Throw at the real reader connection seam; no network or database is used.
  Object.defineProperty(env, "DATABASE_URL_READER", { get() { throw error; } });
  if (outer) Object.defineProperty(env, "ctx", { set() { throw error; } });
  const request = new Request("https://synthetic.example/mcp", {
    method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 31,
      method, params: { name: "list-verbs", arguments: {} } }),
  });
  const response = await dispatch(request, env, {}, { slug: "joe", human: true });
  const body = await response.json();
  const payload = body.result ? JSON.parse(body.result.content[0].text) : body.error.data;
  assert.match(payload.correlation_id, /^[a-f0-9-]{36}$/);
  if (correlated) assert.equal(payload.correlation_id, correlationId);
  assert.doesNotMatch(JSON.stringify(body), /stack|SELECT|private_table|private_constraint|synthetic-private-row|Failing row|\bat .+\(/i);
  assert.ok(logs.some(log => log.correlation_id === payload.correlation_id &&
    JSON.stringify(log).includes("synthetic-private-row")), "full diagnostics are logged under the public id");
  return payload;
}

test("MCP hides internal exceptions and logs diagnostics with the caller's id", async t => {
  const payload = await failure(t, new Error(privateText));
  assert.equal(payload.error, "unhandled_verb_failure");
});

test("MCP hides raw database errors and generates an id without middleware", async t => {
  await failure(t, Object.assign(new Error(privateText), {
    code: "XX000", table: "private_table", constraint: "private_constraint",
    detail: "Failing row contains (synthetic-private-row)",
  }), { correlated: false });
});

test("MCP projects translated database validation errors without schema diagnostics", async t => {
  const error = pgConstraintError(Object.assign(new Error(privateText), {
    code: "23514", table: "private_table", constraint: "private_constraint",
    detail: "Failing row contains (synthetic-private-row)", column: "filter",
  }));
  error.payload.rule = privateText;
  error.payload.columns = ["filter", "private_table"];
  const payload = await failure(t, error);
  assert.equal(payload.error, "invalid_field_value");
  assert.deepEqual(payload.fields, ["filter"]);
});

for (const [code, extra] of [
  ["version_conflict", { current_version: 3 }], ["needs_confirm", {}],
  ["not_found", {}], ["unauthorized", {}], ["loop_not_open", {}],
  ["value_not_in_declared_vocabulary", { field: "filter" }],
]) test(`MCP preserves the public ${code} contract`, async t => {
  const payload = await failure(t, new ToolError({ error: code, ...extra,
    detail: privateText, hint: privateText, nested: { stack: privateText } }));
  assert.equal(payload.error, code);
  if (code === "version_conflict") assert.equal(payload.current_version, 3);
  if (code === "value_not_in_declared_vocabulary") assert.deepEqual(payload.fields, ["filter"]);
});

test("unknown ToolError codes cannot carry diagnostic text onto the wire", async t => {
  const payload = await failure(t, new ToolError({ error: privateText }));
  assert.equal(payload.error, "unhandled_verb_failure");
});

test("outer JSON-RPC errors also hide diagnostics", async t => {
  const payload = await failure(t, new Error(privateText), { outer: true });
  assert.equal(payload.error, "internal_error");
});

test("a writer database failure is translated and its original stack is logged", async t => {
  const logs = [];
  t.mock.method(console, "error", line => logs.push(JSON.parse(line)));
  const original = Object.assign(new Error(privateText), {
    code: "23514", column: "summary", constraint: "private_constraint", detail: privateText,
  });
  t.mock.method(Pool.prototype, "connect", async () => ({
    query: async sql => {
      if (sql === "rollback") return { rows: [] };
      throw original;
    }, release() {},
  }));
  t.mock.method(Pool.prototype, "end", async () => {});
  const request = new Request("https://synthetic.example/mcp", { method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 32, method: "tools/call", params: {
      name: "log-activity", arguments: { idempotency_key: "d170d000-0000-4000-8000-000000000033",
        kind: "note", ref: "SYNTHETIC", summary: "Synthetic fixture" },
    } }),
  });
  const body = await (await dispatch(request, { CORRELATION_ID: correlationId }, {}, { slug: "joe", human: true })).json();
  const payload = JSON.parse(body.result.content[0].text);
  assert.equal(payload.error, "invalid_field_value");
  assert.deepEqual(payload.fields, ["summary"]);
  assert.equal(payload.correlation_id, correlationId);
  assert.doesNotMatch(JSON.stringify(body), /stack|SELECT|private_table|private_constraint|synthetic-private-row/i);
  assert.ok(logs.some(log => log.correlation_id === correlationId && log.diagnostic.cause.stack === original.stack));
});
