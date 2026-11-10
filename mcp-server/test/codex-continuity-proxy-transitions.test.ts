import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const proxy = new URL("../codex-continuity-stdio-proxy.ts", import.meta.url);
const token = "fixture-continuity-transition";

async function run(lines: string[], mode = 0o600, failure = "", missing = false) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "continuity-transitions-"));
  const credential = path.join(root, "token.env");
  fs.writeFileSync(credential, missing ? "" : `CARR_CODEX_CONTINUITY_MCP_TOKEN=${token}\n`, { mode });
  const seen: unknown[] = [];
  let active = 0;
  let maximum = 0;
  const server = http.createServer((request, response) => {
    const resolved = path.resolve(
      root,
      `.${decodeURIComponent(new URL(request.url ?? "/", "http://localhost").pathname)}`,
    );
    if (!resolved.startsWith(root + path.sep) || resolved !== path.join(root, "mcp")) {
      response.writeHead(404).end();
      return;
    }
    active++;
    maximum = Math.max(maximum, active);
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk: string) => {
      body += chunk;
    });
    request.on("end", () => {
      const message: unknown = JSON.parse(body);
      seen.push(message);
      const id = message !== null && typeof message === "object" && "id" in message ? message.id : null;
      assert.equal(request.headers.authorization, `Bearer ${token}`);
      if (failure === "disconnect") {
        active--;
        request.socket.destroy();
        return;
      }
      if (failure === "timeout") {
        response.on("close", () => {
          active--;
        });
        return;
      }
      setTimeout(() => {
        active--;
        response.writeHead(failure === "http" ? 503 : 200, { "content-type": "application/json" });
        response.end(JSON.stringify({ jsonrpc: "2.0", id, result: {} }));
      }, 15);
    });
  });
  try {
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const child = spawn(process.execPath, [proxy.pathname], {
      env: { ...process.env, CARR_MCP_ENV: credential, CARR_MCP_URL: `http://127.0.0.1:${address.port}/mcp` },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "",
      stderr = "";
    child.stdout.setEncoding("utf8").on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.setEncoding("utf8").on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.stdin.on("error", () => {});
    child.stdin.end(lines.join("\n") + "\n");
    const [code] = await once(child, "close");
    assert.doesNotMatch(stdout + stderr, new RegExp(token));
    return { code, stdout, stderr, seen, maximum };
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    fs.rmSync(root, { recursive: true, force: true });
  }
}

const request = JSON.stringify({ jsonrpc: "2.0", id: 7, method: "ping" });
const notification = JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" });

test("proxy sequences requests and notifications and closes at EOF", async () => {
  const result = await run(["  ", request, notification, request]);
  assert.equal(result.code, 0);
  assert.equal(result.maximum, 1);
  assert.deepEqual(result.seen, [JSON.parse(request), JSON.parse(notification), JSON.parse(request)]);
  assert.equal(result.stdout.trim().split("\n").length, 2);
  assert.equal(result.stderr, "");
});

test("proxy diagnoses malformed and oversized input without protocol output", async () => {
  const result = await run(["not-json", "null", "x".repeat(2_000_001), notification]);
  assert.equal(result.code, 0);
  assert.equal(result.stdout, "");
  assert.equal(result.seen.length, 1);
  assert.equal(result.stderr.split("refused malformed or oversized input").length - 1, 3);
});

for (const failure of ["http", "disconnect", "timeout"]) {
  test(`proxy sanitizes ${failure} failures for requests and notifications`, { timeout: 70_000 }, async () => {
    const result = await run([request, notification], 0o600, failure);
    assert.equal(result.code, 0);
    assert.deepEqual(JSON.parse(result.stdout), {
      jsonrpc: "2.0",
      id: 7,
      error: { code: -32603, message: "continuity_proxy_failure" },
    });
    assert.equal(result.stderr, "carr-codex-continuity proxy: refused malformed or oversized input\n");
    assert.equal(result.seen.length, 2);
  });
}

for (const missing of [false, true]) {
  test(`proxy refuses ${missing ? "missing" : "insecure"} credentials before forwarding`, async () => {
    const result = await run([request], missing ? 0o600 : 0o644, "", missing);
    assert.notEqual(result.code, 0);
    assert.equal(result.seen.length, 0);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, missing ? /missing .* in secure token file/ : /refusing MCP token file/);
  });
}
