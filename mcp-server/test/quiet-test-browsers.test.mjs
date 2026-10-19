import test from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";

const root = new URL("../../", import.meta.url);
const launchers = new Set(["dealroom/test/chrome-launch.mjs", "practice-plugin/test/browser.mjs"]);
const ignored = new Set([".git", "node_modules", ".venv", "__pycache__", "out"]);

async function* sources(directory = "") {
  for (const entry of await readdir(new URL(`${directory || "."}/`, root), { withFileTypes: true })) {
    const name = directory ? `${directory}/${entry.name}` : entry.name;
    if (entry.isDirectory() && !ignored.has(entry.name)) yield* sources(name);
    else if (entry.isFile() && (/\.(?:[cm]?[jt]sx?|py|(?:ba|z)?sh)$/.test(name) || !entry.name.includes("."))
      && /(?:^|\/)(?:tests?|tools|ops|bin)(?:\/|$)/.test(name)) yield name;
  }
}

function launchesExecutable(source, shell) {
  const bindings = new Map([...source.matchAll(/\b([\w$]+)\s*=\s*(\[[\s\S]*?\]|[^;\n]+)/g)]
    .map(([, name, value]) => [name, value]));
  const isBrowser = (expression, seen = new Set()) => {
    const executable = expression.trim().replace(/^\[\s*/, "").split(",")[0];
    if (/chrom(?:e|ium)|headless[-_]shell|^browser\b/i.test(executable)) return true;
    for (const name of executable.match(/[\w$]+/g) ?? []) {
      if (seen.has(name) || !bindings.has(name)) continue;
      seen.add(name);
      if (isBrowser(bindings.get(name), seen)) return true;
    }
    return false;
  };
  const processLaunch = [...source.matchAll(/\b(?:spawn(?:Sync)?|exec(?:Sync|File|FileSync)?|Popen|subprocess\.(?:run|call|check_call|check_output)|os\.system)\s*\(\s*(\[[\s\S]*?\]|[^,\n)]+)/g)]
    .some(([, expression]) => isBrowser(expression));
  return processLaunch || (shell && [...source.matchAll(/^[\t ]*(?:exec\s+)?(?:env\s+)?("[^"]+"|'[^']+'|[^\s;|&#]+)/gm)]
    .some(([, command]) => !/^\w+=/.test(command) && isBrowser(command)));
}

test("tests and tools use the shared browser launchers", async () => {
  const violations = [];
  for await (const name of sources()) {
    if (launchers.has(name)) continue;
    const source = await readFile(new URL(name, root), "utf8");
    const javascript = /\.[cm]?[jt]sx?$/.test(name);
    const browserImport = /(?:from\s*|import\s*\(|require\s*\()\s*["'](?:@playwright\/test|playwright(?:-core)?|puppeteer(?:-core)?)["']|(?:from|import)\s+playwright\b/.test(source);
    const browserApi = javascript
      ? /\.\s*(?:launch|launchPersistentContext|launchServer)\s*\(/.test(source)
      : /\b(?:chromium|puppeteer)\s*\.\s*launch\s*\(|\.\s*launch_(?:persistent_context|server)\s*\(/.test(source);
    const shell = /\.(?:ba|z)?sh$/.test(name) || /^#![^\n]*\b(?:ba|z)?sh\b/.test(source);
    const unownedTest = /(?:^|\/)tests?\//.test(name)
      && name !== "mcp-server/test/chrome-launch.test.mjs" && /\blaunchChrome\b/.test(source);
    if (browserImport || browserApi || unownedTest || launchesExecutable(source, shell)) violations.push(name);
  }
  assert.deepEqual(violations, [], "Use the shared browser helper so launch policy and teardown cannot drift");
});
