// Tests the inventory of approved browser launchers.
import test from "node:test";
import assert from "node:assert/strict";
import { cp, mkdir, mkdtemp, rm, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const inventory = new URL("./quiet-test-browsers.test.mjs", import.meta.url);

async function check(t, name, source) {
  const root = await mkdtemp(path.join(tmpdir(), "browser-inventory-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const directory of ["mcp-server/test", "dealroom/test", "practice-plugin/test", "control-room/test", "workspace/test", path.dirname(name)]) {
    await mkdir(path.join(root, directory), { recursive: true });
  }
  await cp(inventory, path.join(root, "mcp-server/test/quiet-test-browsers.test.mjs"));
  await writeFile(path.join(root, name), source);
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  return spawnSync(process.execPath, ["--test", "--test-name-pattern=^tests and tools", "mcp-server/test/quiet-test-browsers.test.mjs"], {
    cwd: root, env, encoding: "utf8", timeout: 10_000,
  });
}

const fixtures = JSON.parse(await readFile(new URL("./fixtures/browser-launch-inventory.json", import.meta.url), "utf8"));
for (const fixture of fixtures) {
  test(`inventory ${fixture.allowed ? "accepts" : "rejects"} ${fixture.name}`, async t => {
    const result = await check(t, fixture.path, fixture.source);
    assert.equal(result.status, fixture.allowed ? 0 : 1, result.stdout + result.stderr);
    if (!fixture.allowed) assert.ok(result.stdout.includes(fixture.path), result.stdout);
  });
}
