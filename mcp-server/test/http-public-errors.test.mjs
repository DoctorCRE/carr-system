import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { publicError } from "../src/public-errors.js";
import { createCaptureHandler } from "../src/capture.js";

const source = readFileSync(new URL("../src/index.js", import.meta.url), "utf8");
const correlationId = "d170d000-0000-4000-8000-000000000032";
const diagnostic = "SELECT * FROM private_table; constraint private_constraint; synthetic-private-row";
const failure = () => Object.assign(new Error(diagnostic), { code: "XX000", detail: diagnostic });
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });

function lift(name, params, bindings) {
  const start = source.indexOf(`async function ${name}(${params}) {`);
  assert.ok(start >= 0);
  const end = source.indexOf("\n}\n", start);
  return new Function(...Object.keys(bindings), `${source.slice(start, end + 3)}; return ${name};`)(...Object.values(bindings));
}

for (const route of ["health", "ingest", "pipeline", "capture"]) {
  test(`${route} hides database diagnostics and logs the caller's correlation id`, async t => {
    const logs = [];
    t.mock.method(console, "error", line => logs.push(JSON.parse(line)));
    const env = { CORRELATION_ID: correlationId,
      INGEST_TOKENS: '{"fixture":"synthetic"}', CAPTURE_TOKENS: '{"fixture":"synthetic"}' };
    let response;
    const neon = () => { throw failure(); };
    if (route === "health") response = await lift("health", "env", { neon, json, publicError })(env);
    if (route === "ingest") response = await lift("ingest", "request, env", {
      neon, json, publicError, INGEST_TRANSPORT: { max_body_bytes: 1024 },
    })(new Request("https://synthetic.example/ingest", { method: "POST",
      headers: { authorization: "Bearer synthetic" }, body: "{}" }), env);
    if (route === "pipeline") response = await lift("pipelineApi", "request, env, actor", {
      neon: () => ({}), json, publicError, pipelineChanges: async () => { throw failure(); },
    })(new Request("https://synthetic.example/pipeline/changes"), env, {});
    if (route === "capture") response = await createCaptureHandler({
      withWriter: async () => { throw failure(); },
    }).fetch(new Request("https://synthetic.example/capture/claim", { method: "POST",
      headers: { authorization: "Bearer synthetic" }, body: JSON.stringify({ nonce: "synthetic",
        device_id: "fixture", mode: "meeting", started_at: "2026-10-23T12:00:00Z",
        consent: { announced_at: "2026-10-23T11:59:59Z" } }),
    }), env);
    assert.equal(response.status, 503);
    const body = await response.json();
    assert.equal(body.correlation_id, correlationId);
    assert.equal(body.error, "database_unavailable");
    assert.doesNotMatch(JSON.stringify(body), /stack|SELECT|private_table|private_constraint|synthetic-private-row/i);
    assert.ok(logs.some(log => log.correlation_id === correlationId && JSON.stringify(log).includes(diagnostic)));
  });
}

test('release keeps unavailable readbacks safe and correlated', async t => {
  const { buildRelease } = await import('../src/release.js');
  const logs = [];
  t.mock.method(console, 'error', line => logs.push(JSON.parse(line)));
  const body = await buildRelease({ env: { CORRELATION_ID: correlationId },
    sql: async () => { throw failure(); }, verbCount: 1 });
  assert.equal(body.ok, true);
  assert.equal(body.schema.highest_applied_migration, null);
  assert.equal(body.schema.applied_count, null);
  assert.equal(body.schema.ledger_sha256, null);
  assert.equal(body.doctrine_generation.value, null);
  for (const readback of [body.schema, body.doctrine_generation]) {
    assert.equal(readback.correlation_id, correlationId);
    assert.equal(readback.error, 'database_unavailable');
    assert.equal(readback.reason, 'Database readback is unavailable.');
  }
  assert.doesNotMatch(JSON.stringify(body), /stack|SELECT|private_table|private_constraint|synthetic-private-row/i);
  assert.equal(logs.filter(log => log.correlation_id === correlationId &&
    log.diagnostic.stack.includes(diagnostic)).length, 2);
});
