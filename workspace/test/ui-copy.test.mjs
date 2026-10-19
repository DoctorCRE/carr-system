import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
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
  const workspace = new URL("../", import.meta.url);
  const findings = await checkUiCopy([
    new URL("public/", workspace),
    new URL("fixtures/", workspace)
  ]);

  assert.deepEqual(findings, []);
});
