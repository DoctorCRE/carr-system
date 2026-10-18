import assert from "node:assert/strict";
import test from "node:test";
import { evidenceActivationTools, isAttemptReliabilityVisibilityRefusal } from "../src/evidence-activation.js";

class TestToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.payload = payload;
  }
}

const actor = { slug: "joe", human: true };
const visibilityError = () => Object.assign(new Error("attempt reliability is not visible to tenant"), { code: "P0001" });

function readHandler(queryImpl) {
  const calls = [];
  const client = {
    query: async (sql, params) => {
      calls.push({ sql, params });
      return queryImpl(sql, params);
    },
  };
  const tools = evidenceActivationTools({
    withEnvelope: async (_client, _actor, _verb, _args, fn) => fn(),
    ToolError: TestToolError,
  });
  return { handler: tools["read-attempt-reliability"].handler, client, calls };
}

function allTools(queryImpl) {
  const calls = [];
  const client = { query: async (sql, params) => { calls.push({ sql, params }); return queryImpl(sql, params); } };
  const tools = evidenceActivationTools({
    withEnvelope: async (_client, _actor, verb, args, fn) => ({ verb, args, result: await fn() }),
    ToolError: TestToolError,
  });
  return { tools, client, calls };
}

test("a nonexistent attempt is a stable not-found refusal", async () => {
  const { handler, client } = readHandler((sql) => /set_config/.test(sql) ? { rows: [] } : { rows: [{ reliability: null }] });
  await assert.rejects(
    handler(client, actor, { attempt_id: "attempt:missing" }),
    (error) => error instanceof TestToolError && error.payload.error === "attempt_reliability_not_found",
  );
});

test("a foreign-tenant visibility refusal is mapped without exposing the database error", async () => {
  const { handler, client } = readHandler((sql) => /set_config/.test(sql) ? { rows: [] } : (() => { throw visibilityError(); })());
  await assert.rejects(
    handler(client, actor, { attempt_id: "attempt:foreign" }),
    (error) => error instanceof TestToolError && error.payload.error === "attempt_reliability_not_found" && !("cause" in error.payload),
  );
});

test("only the exact expected refusal is translated; unexpected database faults still escape", async () => {
  assert.equal(isAttemptReliabilityVisibilityRefusal(visibilityError()), true);
  assert.equal(isAttemptReliabilityVisibilityRefusal(Object.assign(new Error("attempt reliability is not visible to tenant"), { code: "XX000" })), false);
  assert.equal(isAttemptReliabilityVisibilityRefusal(Object.assign(new Error("different database failure"), { code: "P0001" })), false);

  const unexpected = Object.assign(new Error("connection reset"), { code: "08006" });
  const { handler, client } = readHandler((sql) => /set_config/.test(sql) ? { rows: [] } : (() => { throw unexpected; })());
  await assert.rejects(
    handler(client, actor, { attempt_id: "attempt:db-fault" }),
    (error) => error === unexpected,
  );
});

test("context activation installs the derived tenant before compiling the accepted bundle", async () => {
  const bundle = { schema_version: "context-bundle.v1", bundle_digest: `sha256:${"b".repeat(64)}` };
  const { tools, client, calls } = allTools((sql, params) => {
    if (/activate-context-bundle:tenant/.test(sql)) return { rows: [] };
    if (/compile_context_bundle/.test(sql)) {
      assert.match(calls[0].sql, /set_config\('carr\.organization_tenant_id'/);
      assert.deepEqual(calls[0].params, ["carr-internal"]);
      assert.deepEqual(params, ["WR-000007", "PLAN-1befe10dc250-v1", "carr-internal"]);
      return { rows: [{ bundle }] };
    }
    if (/activate_context_bundle/.test(sql)) {
      return { rows: [{ binding_id: "ctx-0123456789abcdef", bundle_digest: bundle.bundle_digest, replayed: false }] };
    }
    throw new Error(`unexpected query: ${sql}`);
  });

  const response = await tools["activate-context-bundle"].handler(client, actor, {
    human_ref: "WR-000007",
    plan_ref: "PLAN-1befe10dc250-v1",
    idempotency_key: "428e0608-69c7-469d-8562-d702cd1853cd",
  });

  assert.equal(response.result.binding_id, "ctx-0123456789abcdef");
  assert.equal(response.result.replayed, false);
  assert.equal(calls.length, 3);
});

test("activation read and render bind tenant in the same serverless statement", async () => {
  const { tools, client, calls } = allTools((sql, params) => {
    assert.match(sql, /with tenant_scope as materialized/);
    assert.match(sql, /set_config\('carr\.organization_tenant_id'/);
    assert.deepEqual(params, ["carr-internal", "WR-000007", "ctx-0123456789abcdef"]);
    if (/read_context_activation/.test(sql)) return { rows: [{ activation: { binding: { binding_id: "ctx-0123456789abcdef" } } }] };
    if (/render_context_activation_for_brief/.test(sql)) return { rows: [{ items: [] }] };
    throw new Error(`unexpected query: ${sql}`);
  });
  const args = { human_ref: "WR-000007", binding_id: "ctx-0123456789abcdef" };
  assert.equal((await tools["read-context-activation"].handler(client, actor, args)).activation.binding.binding_id, args.binding_id);
  assert.deepEqual((await tools["render-context-activation"].handler(client, actor, args)).items, []);
  assert.equal(calls.length, 2);
});


test("execution provider configuration remains readable after lifecycle retirement", async () => {
  const providers = [{ provider_ref: "environment-provider:fixture:v1", state: "active" }];
  const { tools, client } = allTools(() => ({ rows: [{ providers }] }));
  assert.deepEqual(await tools["read-execution-environment-providers"].handler(client), { ok: true, providers });
});
