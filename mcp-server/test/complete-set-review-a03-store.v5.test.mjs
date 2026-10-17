import assert from "node:assert/strict";
import test from "node:test";
import { TOOLS, executeRegisteredTool } from "../src/tools.js";

test("retired record-layer verbs are unavailable through MCP dispatch", async () => {
  for (const name of [
    "accept-benchmark-manifest-draft",
    "advance-journey-one-clock",
    "assign-execution-route",
    "attest-attempt-evaluation",
    "attest-capability-project",
    "attest-execution-environment-conformance",
    "begin-capability-project",
    "issue-execution-envelope",
    "open-complete-set-review",
    "prepare-capability-project",
    "produce-assurance-fabric-preactivation-receipt",
    "produce-foundation-assurance-benchmark-coverage",
    "produce-foundation-control-plane-preactivation-receipt",
    "produce-global-execution-contract-receipt",
    "produce-global-no-phi-boundary-receipt",
    "produce-global-prompt-injection-boundary-receipt",
    "produce-global-secrets-boundary-receipt",
    "produce-global-source-authority-receipt",
    "propose-benchmark-manifest-draft",
    "propose-evaluation-case",
    "read-complete-set-review",
    "record-assurance-health-evidence",
    "record-attempt-receipt",
    "record-complete-set-adjudication",
    "record-complete-set-finding-set",
    "record-complete-set-participant",
    "record-foundation-assurance-minimum-outcome",
    "record-gate-zero-read-only-outcome",
    "record-model-role-revision",
    "register-execution-environment-provider",
    "review-benchmark-manifest-draft",
    "seal-complete-set-review-round",
    "set-current-model-role-revision",
    "start-capability-project",
    "transition-evaluation-case",
    "transition-execution-environment-provider"
]) {
    assert.equal(Object.hasOwn(TOOLS, name), false, name);
    await assert.rejects(executeRegisteredTool({}, { slug: "joe" }, name, {}),
      error => error.payload?.error === "unknown_tool");
  }
});

for (const [name, args, sqlFunction, field, stored] of [
  ['read-model-role', { role_key: 'reviewer' }, 'ops.model_role_readback', 'readback', { role_key: 'reviewer', versions: [{ version: 2, description: 'Independent review' }] }],
  ['read-benchmark-manifest', { benchmark_ref: 'benchmark:fixture' }, 'ops.benchmark_readback', 'readback', { benchmark_ref: 'benchmark:fixture', measurements: [{ duration_ms: 125 }] }],
]) test(`${name} preserves versioned records without a lifecycle write`, async () => {
  const calls = [];
  const client = { query: async (sql, params) => {
    calls.push({ sql, params });
    assert.ok(sql.startsWith(`select ${sqlFunction}(`));
    return { rows: [{ [field]: stored }] };
  } };
  const result = await TOOLS[name].handler(client, { slug: 'joe' }, args);
  assert.deepEqual(result, { ok: true, ...stored });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].params, Object.values(args));
});

test('the Journey One read preserves stored start and pause history', async () => {
  const scope = 'sha256:' + 'a'.repeat(64);
  const clock = { history: { events: [
    { type: 'clock_started', observed_at: '2026-09-01T15:00:00Z' },
    { type: 'paused', observed_at: '2026-09-02T15:00:00Z' },
  ] } };
  const client = { query: async (sql, params) => {
    if (sql.startsWith('select ops.j1_clock_scope_bindings(')) {
      assert.deepEqual(params, [scope, null]);
      return { rows: [{ bindings: { by_scope: { clock_key: 'clock:fixture' } } }] };
    }
    assert.ok(sql.startsWith('select ops.j1_clock_history('));
    assert.deepEqual(params, ['clock:fixture']);
    return { rows: [{ clock }] };
  } };
  assert.deepEqual(await TOOLS['read-journey-one-clock'].handler(client, {}, { clock_scope_key: scope }),
    { ok: true, clock_scope_key: scope, clock_key: 'clock:fixture', clock_started: true, clock });
});
