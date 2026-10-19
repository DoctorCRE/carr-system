import test from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";

const root = new URL("../../", import.meta.url);
const testDirectories = ["mcp-server/test", "dealroom/test", "practice-plugin/test", "control-room/test", "workspace/test"];

test("browser tests use the shared launcher instead of starting their own browser", async () => {
  const violations = [];
  for (const directory of testDirectories) {
    for (const file of await readdir(new URL(`${directory}/`, root))) {
      if (!/\.test\.(?:mjs|js)$/.test(file)) continue;
      const source = await readFile(new URL(`${directory}/${file}`, root), "utf8");
      if (/from\s*["'](?:@playwright\/test|playwright(?:-core)?|puppeteer(?:-core)?)["']/.test(source)
        || /\.\s*(?:launch|launchPersistentContext|launchServer)\s*\(/.test(source)) {
        violations.push(`${directory}/${file}`);
      }
    }
  }
  assert.deepEqual(violations, [], "Use the shared browser helper so launch policy and teardown cannot drift");
});
