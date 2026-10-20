import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { spawnSync } from "node:child_process";
import { checkUiCopy } from "../scripts/check-ui-copy.mjs";

test("UI copy lint reports banned voice with a path and matched text", async t => {
  const root = await mkdtemp(join(tmpdir(), "carr-ui-copy-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(join(root, "screen.html"), "What changed?\nWHAT\nneeds your decision.");

  const findings = await checkUiCopy([root]);

  assert.equal(findings.length, 1);
  assert.match(findings[0].path, /screen\.html$/);
  assert.match(findings[0].message, /WHAT needs your/i);
});

test("UI copy lint accepts factual copy and ignores vendor and non-UI files", async t => {
  const root = await mkdtemp(join(tmpdir(), "carr-ui-copy-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, "vendor"));
  await writeFile(join(root, "screen.json"), '{"empty":"No pending decisions."}');
  await writeFile(join(root, "vendor", "bundle.js"), "Nothing needs you.");
  await writeFile(join(root, "notes.md"), "Everything is clear.");

  assert.deepEqual(await checkUiCopy([root]), []);
});

test("UI copy lint rejects the related assistant-like status phrases", async t => {
  const root = await mkdtemp(join(tmpdir(), "carr-ui-copy-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(join(root, "statuses.js"), [
    "Nothing needs your decision.",
    "What needs your attention?",
    "This needs you.",
    "Deals that need you.",
    "Things that need you.",
    "One thing is broken.",
    "1 thing is broken.",
    "Nothing is stuck.",
    "Nothing is waiting on you.",
    "Everything is clear."
  ].join("\n"));

  const findings = await checkUiCopy([root]);

  assert.equal(findings.length, 10);
});

test("UI copy lint covers the live CARR UI and fixtures", async () => {
  const result = spawnSync(process.execPath, [new URL("../scripts/check-ui-copy.mjs", import.meta.url).pathname], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("UI copy CLI exits unsuccessfully for a planted UI phrase", async t => {
  const root = await mkdtemp(join(tmpdir(), "carr-ui-copy-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(join(root, "planted.html"), "Nothing needs you right now.");
  const result = spawnSync(process.execPath, [new URL("../scripts/check-ui-copy.mjs", import.meta.url).pathname, root], { encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /planted\.html:1: banned UI copy/);
});


test("default CI lint covers every shipped UI source and fixture root", async t => {
  const root = await realpath(await mkdtemp(join(tmpdir(), "carr-ui-roots-")));
  t.after(() => rm(root, { recursive: true, force: true }));
  const paths = ["workspace/public", "workspace/fixtures", "dealroom", "practice-plugin/ui", "practice-plugin/src", "practice-plugin/test"];
  for (const path of [...paths, "workspace/scripts"]) await mkdir(join(root, path), { recursive: true });
  const checker = join(root, "workspace/scripts/check-ui-copy.mjs");
  await writeFile(checker, await readFile(new URL("../scripts/check-ui-copy.mjs", import.meta.url)));
  const run = () => spawnSync(process.execPath, [checker], { encoding: "utf8" });
  const clean = run();
  assert.equal(clean.status, 0, clean.stdout + clean.stderr);
  for (const path of paths) {
    const planted = join(root, path, "planted.js");
    await writeFile(planted, 'const label = "Needs you";');
    const result = run();
    assert.equal(result.status, 1, `${path}: ${result.stdout}${result.stderr}`);
    assert.match(result.stderr, /planted\.js:1: banned UI copy/);
    await rm(planted);
  }
});
