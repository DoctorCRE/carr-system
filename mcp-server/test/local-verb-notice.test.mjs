// Tests that ./run.sh call stdout stays one JSON value when the untrusted-text notice arrives.
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { toolCallContent } from "../src/untrusted-text.js";

const LOCAL_VERB = fileURLToPath(new URL("../local-verb.mjs", import.meta.url));

test("local-verb prints the payload alone on stdout and the notice on stderr", () => {
  const payload = { activities: [{ kind: "email_in", summary: "Email from Pat: x", detail: "Inbound body" }] };
  const content = toolCallContent(payload);
  assert.equal(content.length, 2);
  const dir = mkdtempSync(path.join(tmpdir(), "local-verb-notice-"));
  try {
    const preload = path.join(dir, "preload.mjs");
    writeFileSync(preload, `globalThis.fetch = async () => ({ ok: true, status: 200,
      text: async () => ${JSON.stringify(JSON.stringify({ jsonrpc: "2.0", id: 1, result: { content } }))} });\n`);
    const run = spawnSync(process.execPath, ["--import", preload, LOCAL_VERB, "get-deal-room", "{}"], {
      encoding: "utf8", timeout: 20000,
      env: { HOME: dir, PATH: process.env.PATH, CARR_MCP_LOCAL_TOKEN: "synthetic-fixture-bearer",
        CARR_MCP_ENV: path.join(dir, "absent-file"), CARR_MCP_URL: "http://fixture.invalid/mcp" },
    });
    assert.equal(run.status, 0, run.stderr);
    assert.deepEqual(JSON.parse(run.stdout), payload);
    assert.match(run.stderr, /"untrusted_text"/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
