import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

test("Claude continuity stdio proxy keeps the bearer out of config and exposes three verbs", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "carr-continuity-proxy-"));
  const tokenFile = path.join(temp, "mcp-tokens.env");
  const proxy = path.join(temp, "continuity-stdio-proxy.mjs");
  fs.copyFileSync(new URL("../continuity-stdio-proxy.mjs", import.meta.url), proxy);
  fs.copyFileSync(new URL("../local-client-auth.mjs", import.meta.url),
    path.join(temp, "local-client-auth.mjs"));
  fs.writeFileSync(tokenFile, "CARR_CLAUDE_CONTINUITY_MCP_TOKEN=secret-claude\n", { mode: 0o600 });
  const seen = [];
  const server = http.createServer((request, response) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", chunk => { body += chunk; });
    request.on("end", () => {
      assert.equal(request.headers.authorization, "Bearer secret-claude");
      const message = JSON.parse(body);
      seen.push(message);
      if (message.id === 4) {
        response.writeHead(503, { "content-type": "text/plain" });
        response.end("secret upstream detail /private/path");
        return;
      }
      const result = message.method === "initialize" ? {
        protocolVersion: "2025-06-18", serverInfo: { name: "full-carr", version: "secret" },
        instructions: "call standing-context and load the full store",
        capabilities: { tools: {} },
      } : message.method === "tools/list" ? { tools: [
        { name: "claude-checkpoint" }, { name: "claude-read-recovery" },
        { name: "claude-record-event" }, { name: "log-activity" },
      ] } : { content: [{ type: "text", text: "ok" }] };
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result }));
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const child = spawn(process.execPath, [proxy], {
    env: { ...process.env, CARR_MCP_ENV: tokenFile,
      CARR_MCP_URL: `http://127.0.0.1:${server.address().port}/mcp` },
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stdout = "", stderr = "";
  child.stdout.on("data", chunk => { stdout += chunk; });
  child.stderr.on("data", chunk => { stderr += chunk; });
  child.stdin.end([
    { jsonrpc: "2.0", id: 0, method: "initialize", params: {} },
    { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} },
    { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "log-activity", arguments: {} } },
    { jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "claude-checkpoint", arguments: {} } },
    { jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "claude-read-recovery", arguments: {} } },
  ].map(value => JSON.stringify(value)).join("\n") + "\n" + "not-json\n" + "x".repeat(2_000_001) + "\n");
  const code = await new Promise(resolve => child.on("close", resolve));
  server.close();
  fs.rmSync(temp, { recursive: true, force: true });
  assert.equal(code, 0, stderr);
  const responses = stdout.trim().split("\n").map(JSON.parse);
  assert.equal(responses[0].result.serverInfo.name, "carr-continuity");
  assert.match(responses[0].result.instructions, /semantic milestones/);
  assert.doesNotMatch(responses[0].result.instructions, /standing-context|full store/);
  assert.deepEqual(responses[1].result.tools.map(tool => tool.name), [
    "claude-checkpoint", "claude-read-recovery", "claude-record-event",
  ]);
  assert.equal(responses[2].error.message, "not_in_claude_continuity_profile");
  assert.equal(responses[3].result.content[0].text, "ok");
  assert.equal(responses[4].error.message, "continuity_proxy_failure");
  assert.deepEqual(seen.map(message => message.id), [0, 1, 3, 4]);
  assert.doesNotMatch(stdout + stderr, /secret-claude|secret upstream|private\/path|mcp-tokens/);
});
