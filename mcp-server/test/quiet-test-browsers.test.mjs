import test from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";

const root = new URL("../../", import.meta.url);
const launchers = new Set(["dealroom/test/chrome-launch.mjs", "practice-plugin/test/browser.mjs"]);
const testDirectories = ["mcp-server/test", "dealroom/test", "practice-plugin/test", "control-room/test", "workspace/test"];

test("browser tests use the shared launcher instead of starting their own browser", async () => {
  const violations = [];
  for (const directory of testDirectories) {
    for (const file of await readdir(new URL(`${directory}/`, root), { recursive: true })) {
      const name = `${directory}/${file}`;
      if (!/\.(?:mjs|js)$/.test(file) || launchers.has(name)) continue;
      const source = await readFile(new URL(`${directory}/${file}`, root), "utf8");
      if (/from\s*["'](?:@playwright\/test|playwright(?:-core)?|puppeteer(?:-core)?)["']/.test(source)
        || /\.\s*(?:launch|launchPersistentContext|launchServer)\s*\(/.test(source)
        || (name !== "mcp-server/test/chrome-launch.test.mjs" && /\blaunchChrome\b/.test(source))
        || /\b(?:spawn|spawnSync|execFile|execFileSync)\s*\(\s*["'][^"']*(?:[Cc]hrom|headless)/.test(source)) {
        violations.push(name);
      }
    }
  }
  assert.deepEqual(violations, [], "Use the shared browser helper so launch policy and teardown cannot drift");
});
