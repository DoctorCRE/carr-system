import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

test("Codex continuity stdio proxy keeps the bearer out of config and exposes two verbs", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "carr-continuity-proxy-"));
  const tokenFile = path.join(temp, "mcp-tokens.env");
  fs.writeFileSync(tokenFile, "CARR_CODEX_CONTINUITY_MCP_TOKEN=secret-codex\n", { mode: 0o600 });
  const seen = [];
  const server = http.createServer((request, response) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", chunk => { body += chunk; });
    request.on("end", () => {
      assert.equal(request.headers.authorization, "Bearer secret-codex");
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
        { name: "codex-checkpoint" }, { name: "codex-read-recovery" },
        { name: "log-activity" },
      ] } : { content: [{ type: "text", text: "ok" }] };
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result }));
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const child = spawn(process.execPath, [new URL("../continuity-stdio-proxy.mjs", import.meta.url).pathname, "--codex"], {
    env: { ...process.env, CARR_MCP_ENV: tokenFile,
      CARR_MCP_URL: `http://127.0.0.1:${server.address().port}/mcp` },
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stdout = "", stderr = "";
  child.stdout.on("data", chunk => { stdout += chunk; });
  child.stderr.on("data", chunk => { stderr += chunk; });
  const storageCliffState = { objective: "", next_action: "verify", progress: [{ text: "done" }] };
  storageCliffState.objective = "a".repeat(23998 - Buffer.byteLength(JSON.stringify(storageCliffState)));
  assert.equal(Buffer.byteLength(JSON.stringify(storageCliffState)), 23998);
  // Four colons and two commas add six storage bytes; this copy is exactly 24KB.
  const storageLimitState = { ...storageCliffState, objective: storageCliffState.objective.slice(4) };
  const referenceCapacityState = {
    objective: "retain full logical refs", next_action: "verify physical budget",
    latest_corrections: [{ text: "current evidence", refs: Array.from({ length: 1000 }, (_value, index) =>
      `ref:${index}:${"x".repeat(90)}`) }],
    progress: [{ text: "x".repeat(10_700) }],
  };
  child.stdin.end([
    { jsonrpc: "2.0", id: 0, method: "initialize", params: {} },
    { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} },
    { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "log-activity", arguments: {} } },
    { jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "codex-checkpoint", arguments: {} } },
    { jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "codex-read-recovery", arguments: {} } },
    { jsonrpc: "2.0", id: 5, method: "tools/call", params: { name: "codex-checkpoint", arguments: { state: { objective: "é".repeat(12000) } } } },
    { jsonrpc: "2.0", id: 6, method: "tools/call", params: { name: "claude-checkpoint", arguments: {} } },
    { jsonrpc: "2.0", id: 7, method: "tools/call", params: { name: "codex-checkpoint", arguments: { state: storageCliffState } } },
    { jsonrpc: "2.0", id: 8, method: "tools/call", params: { name: "codex-checkpoint", arguments: { state: storageLimitState } } },
    { jsonrpc: "2.0", id: 9, method: "tools/call", params: { name: "codex-checkpoint", arguments: { state: referenceCapacityState } } },
  ].map(value => JSON.stringify(value)).join("\n") + "\n" + "not-json\n" + "x".repeat(2_000_001) + "\n");
  const code = await new Promise(resolve => child.on("close", resolve));
  server.close();
  fs.rmSync(temp, { recursive: true, force: true });
  assert.equal(code, 0, stderr);
  const responses = stdout.trim().split("\n").map(JSON.parse);
  assert.equal(responses[0].result.serverInfo.name, "carr-codex-continuity");
  assert.match(responses[0].result.instructions, /semantic checkpoints/);
  assert.doesNotMatch(responses[0].result.instructions, /standing-context|full store/);
  assert.deepEqual(responses[1].result.tools.map(tool => tool.name), [
    "codex-checkpoint", "codex-read-recovery",
  ]);
  assert.equal(responses[2].error.message, "not_in_codex_continuity_profile");
  assert.equal(responses[3].result.content[0].text, "ok");
  assert.equal(responses[4].error.message, "continuity_proxy_failure");
  assert.equal(responses[5].error.message, "codex_continuity_payload_too_large");
  assert.equal(responses[6].error.message, "not_in_codex_continuity_profile");
  assert.equal(responses[7].error.message, "codex_continuity_payload_too_large");
  assert.equal(responses[8].result.content[0].text, "ok");
  assert.equal(responses[9].result.content[0].text, "ok");
  assert.deepEqual(seen.map(message => message.id), [0, 1, 3, 4, 8, 9]);
  assert.doesNotMatch(stdout + stderr, /secret-codex|secret upstream|private\/path|mcp-tokens/);
});

test("Codex installer copies its reviewed checkout and preserves unrelated configuration", async () => {
  const { execFileSync } = await import("node:child_process");
  const script = new URL("../../ops/config-as-code.py", import.meta.url).pathname;
  const proof = execFileSync("python3", ["-c", `
import copy, importlib.util, json, sys, tempfile
from pathlib import Path
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('config_fixture', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.REPO = '/not-the-reviewed-checkout'
original = {'unrelated': {'retain': True}, 'mcp_servers': {'carr': {'url': 'https://example.test', 'disabled_tools': ['prior']}}}
current = copy.deepcopy(original)
def read():
    return {'config': copy.deepcopy(current), 'version': 'fixture-version'}
def write(edits, version):
    assert version == 'fixture-version'
    for edit in edits:
        parts = edit['keyPath'].split('.')
        node = current
        for key in parts[:-1]:
            node = node.setdefault(key, {})
        node[parts[-1]] = copy.deepcopy(edit['value'])
    return read()
m._codex_user_config_layer = read
m._write_codex_config_edits = write
with tempfile.TemporaryDirectory() as home, patch.object(Path, 'home', return_value=Path(home)):
    assert m.cmd_install_codex_continuity_mcp(True) == 0
    assert m.cmd_install_codex_continuity_mcp(False) == 0
    installed = Path(home) / '.config/carr/codex-continuity/continuity-stdio-proxy.mjs'
    reviewed = Path(sys.argv[1]).resolve().parents[1] / 'mcp-server/continuity-stdio-proxy.mjs'
    assert installed.read_bytes() == reviewed.read_bytes()
    installed_manifest = Path(home) / '.config/carr/codex-continuity/continuity-reference-manifest.mjs'
    reviewed_manifest = Path(sys.argv[1]).resolve().parents[1] / 'mcp-server/continuity-reference-manifest.mjs'
    assert installed_manifest.read_bytes() == reviewed_manifest.read_bytes()
    assert current['unrelated'] == original['unrelated']
    assert current['mcp_servers']['carr']['url'] == original['mcp_servers']['carr']['url']
    assert current['mcp_servers']['carr']['disabled_tools'] == ['prior', 'codex-checkpoint', 'codex-read-recovery']
    server = current['mcp_servers']['carr-codex-continuity']
    assert server['args'][-1] == '--codex'
    assert server['tools'] == {name: {'approval_mode': 'approve'} for name in ['codex-checkpoint', 'codex-read-recovery']}
`, script], { encoding: "utf8" });
  assert.equal(proof.trim().split("\n").map(JSON.parse).every(row => row.ok), true);
});
