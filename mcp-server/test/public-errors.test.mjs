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

test('record-signal commits the signal with a safe notification recovery result', async t => {
  const { executeRegisteredTool } = await import('../src/tools.js');
  const logs = [], queries = [];
  t.mock.method(console, 'error', line => logs.push(JSON.parse(line)));
  const signal = { id: '50000000-0000-4000-8000-000000000001', producer: 'synthetic',
    signal_key: 'synthetic', signal_kind: 'threshold', subject_type: 'deal', subject_ref: 'fixture',
    metric_name: 'count', observed_value: 2, threshold_value: 1, severity: 'critical' };
  const db = { async query(text) {
    const sql = text.replace(/\s+/g, ' ').trim();
    queries.push(sql);
    if (sql.startsWith('select request_hash')) return { rows: [] };
    if (sql.includes('to_regclass')) return { rows: [{ signals: true, runs: true, routes: true }] };
    if (sql.startsWith('insert into signal_event')) return { rows: [signal] };
    if (sql.startsWith('select ops.mint_notification')) throw new Error(privateText);
    if (/^(savepoint|release savepoint|rollback to savepoint|insert into event|insert into tool_call)/.test(sql))
      return { rows: [] };
    throw new Error(`Unhandled synthetic query: ${sql}`);
  } };
  const result = await executeRegisteredTool(db, { id: '10000000-0000-4000-8000-000000000001',
    slug: 'joe', human: true, correlation_id: correlationId }, 'record-signal', {
    ...Object.fromEntries(Object.entries(signal).filter(([key]) => key !== 'id')),
    comparison: 'gt', detected_at: '2026-10-23T12:00:00Z', evidence_refs: ['synthetic:fixture'],
    idempotency_key: 'synthetic-signal',
  });
  assert.equal(result.ok, true);
  assert.deepEqual(result.signal, signal);
  assert.deepEqual(result.notification, { ok: true, minted: false,
    reason_id: 'notification_mint_unavailable', correlation_id: correlationId });
  assert.ok(queries.includes('rollback to savepoint carr_r03_mint'));
  assert.ok(queries.includes('release savepoint carr_r03_mint'));
  assert.ok(queries.some(sql => sql.startsWith('insert into tool_call')), 'successful signal receipt is retained');
  assert.doesNotMatch(JSON.stringify(result), /stack|SELECT|private_table|private_constraint|synthetic-private-row/i);
  assert.ok(logs.some(log => log.correlation_id === correlationId && log.diagnostic.stack.includes(privateText)));
});

for (const delegated of [false, true]) {
  for (const [variant, rows, expected] of [
    ['deal', [{ display_name: 'Synthetic A', status: 'research', client_ref: 'C-1' },
      { display_name: 'Synthetic B', status: 'tour', client_ref: 'C-2' }],
      [{ name: 'Synthetic A', phase: 'research', client_ref: 'C-1' },
        { name: 'Synthetic B', phase: 'tour', client_ref: 'C-2' }]],
    ['party', [{ display_name: 'Synthetic A', ref: 'C-1', subject_type: 'client', city: 'Fixture' },
      { display_name: 'Synthetic B', ref: 'C-2', subject_type: 'client', city: null }],
      [{ name: 'Synthetic A', ref: 'C-1', kind: 'client', city: 'Fixture' },
        { name: 'Synthetic B', ref: 'C-2', kind: 'client', city: null }]],
  ]) test(`${delegated ? 'delegated' : 'direct'} MCP ambiguous ${variant} lookup retains safe choices`, async t => {
    const { neonConfig } = await import('@neondatabase/serverless');
    t.mock.method(console, 'error', () => {});
    const previous = neonConfig.fetchFunction;
    t.after(() => { neonConfig.fetchFunction = previous; });
    neonConfig.fetchFunction = async (_url, options) => {
      const { query } = JSON.parse(options.body);
      const selected = variant === 'party' && query.includes("subject_type='deal'") ? [] : rows;
      const names = Object.keys(rows[0]);
      return Response.json({ fields: names.map(name => ({ name, dataTypeID: 25 })),
        rows: selected.map(row => names.map(name => row[name])), rowCount: selected.length,
        command: 'SELECT', rowAsArray: true });
    };
    const request = new Request('https://synthetic.example/mcp', { method: 'POST',
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: delegated
        ? { name: 'call-verb', arguments: { verb: 'get-deal-room', args: { deal: 'Synthetic' } } }
        : { name: 'get-deal-room', arguments: { deal: 'Synthetic' } } }),
    });
    const body = await (await dispatch(request, { CORRELATION_ID: correlationId,
      DATABASE_URL_READER: 'postgres://fake:fake@synthetic.invalid/fixture' }, {},
    { slug: 'joe', human: true })).json();
    assert.equal(body.result.isError, true);
    const payload = JSON.parse(body.result.content[0].text);
    assert.equal(payload.error, 'needs_disambiguation');
    assert.deepEqual(payload.candidates, expected);
    assert.equal(payload.correlation_id, correlationId);
  });
}

for (const candidate of [
  { loop_id: 'synthetic-loop', kind: 'open_loop', section: 'hot', renders_into: 'loops.md' },
  { party_id: 'synthetic-party', party_name: 'Synthetic', party_city: 'Fixture', party_state: 'AL' },
  { id: 'synthetic-building', address: '1 Fixture', city: 'Fixture' },
  { name: 'Synthetic' }, { ref: 'P-1', name: 'Synthetic' },
]) test(`disambiguation curates the ${Object.keys(candidate).join('/')} candidate shape`, async t => {
  const { publicError } = await import('../src/public-errors.js');
  t.mock.method(console, 'error', () => {});
  const payload = publicError(new ToolError({ error: 'needs_disambiguation',
    candidates: [{ ...candidate, detail: privateText, stack: privateText, nested: { cause: privateText } }],
    retired_aliases: [{ ref: 'P-2', name: 'Retired synthetic', detail: privateText }],
    hint: privateText, detail: privateText }), { env: { CORRELATION_ID: correlationId } });
  assert.equal(payload.error, 'needs_disambiguation');
  assert.deepEqual(payload.candidates, [candidate]);
  assert.deepEqual(payload.retired_aliases, [{ ref: 'P-2', name: 'Retired synthetic' }]);
  assert.doesNotMatch(JSON.stringify(payload), /stack|SELECT|private_table|private_constraint|synthetic-private-row/i);
});
