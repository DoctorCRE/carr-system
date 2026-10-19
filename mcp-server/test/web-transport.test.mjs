import test from "node:test";
import assert from "node:assert/strict";
import { createMcpTransport, createSystemWorkTransport } from "../../dealroom/js/web-transport.js";

test("MCP transport calls a verb over same-origin JSON-RPC and returns its payload", async () => {
  const requests = [];
  const call = createMcpTransport({ fetchImpl: async (path, init) => {
    requests.push({ path, ...init, body: JSON.parse(init.body) });
    return new Response(JSON.stringify({ result: { content: [{ type: "text", text: '{"ok":true,"value":"synthetic"}' }] } }));
  } });
  assert.deepEqual(await call("read-example", { scope: "synthetic" }), { ok: true, value: "synthetic" });
  assert.deepEqual(requests, [{
    path: "/mcp", method: "POST", credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "read-example", arguments: { scope: "synthetic" } } },
  }]);
});

test("System Work transport bootstraps CSRF and unwraps challenged JSON writes", async () => {
  const requests = [];
  const responses = [{ csrf_token: "synthetic-csrf" }, { data: { ok: true } }];
  const transport = createSystemWorkTransport({ fetchImpl: async (path, init) => {
    requests.push({ path, ...init, ...(init.body ? { body: JSON.parse(init.body) } : {}) });
    return new Response(JSON.stringify(responses.shift()));
  } });
  assert.deepEqual(await transport.bootstrap(), { csrf_token: "synthetic-csrf" });
  assert.deepEqual(transport.session, { csrf_token: "synthetic-csrf" });
  assert.deepEqual(await transport.post("/api/system-work/report", { title: "Synthetic" }, "synthetic-challenge"), { ok: true });
  assert.deepEqual(requests, [
    { path: "/api/system-work/session", credentials: "same-origin", headers: { accept: "application/json" } },
    { path: "/api/system-work/report", method: "POST", credentials: "same-origin",
      headers: { "content-type": "application/json", accept: "application/json",
        "x-carr-csrf": "synthetic-csrf", "x-carr-action-challenge": "synthetic-challenge" }, body: { title: "Synthetic" } },
  ]);
});

test("Lead Board transport finds text content and preserves its typed refusal", async () => {
  const call = createMcpTransport({ surface: "lead-board", fetchImpl: async () =>
    new Response(JSON.stringify({ result: { content: [
      { type: "image", data: "synthetic" },
      { type: "text", text: '{"error":"version_conflict","message":"Changed elsewhere."}' },
    ] } })) });
  await assert.rejects(call("update-lead", {}), (error) => {
    assert.equal(error.message, "Changed elsewhere.");
    assert.equal(error.code, "version_conflict");
    assert.deepEqual(error.payload, { error: "version_conflict", message: "Changed elsewhere." });
    return true;
  });
});
