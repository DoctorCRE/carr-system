import test from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { matchesGlob } from "node:path";
import { suiteBatches } from "../run-tests.mjs";

test("every Node suite runs exactly once without the retired browser shim", () => {
  const files = ["z.test.js", "chrome-launch.test.mjs", "a.test.mjs", "future.test.js", "helper.mjs", "sample-browser.test.mjs", "future-browser.test.mjs"];
  const batches = suiteBatches(files);
  assert.deepEqual(batches[0], ["chrome-launch.test.mjs"]);
  assert.deepEqual(batches[1], ["future-browser.test.mjs"]);
  assert.deepEqual(batches[2], ["sample-browser.test.mjs"]);
  assert.deepEqual(batches[3], ["a.test.mjs", "future.test.js", "z.test.js"]);
  const all = batches.flat();
  assert.equal(new Set(all).size, all.length);
  assert.equal(all.length, files.length - 1);
});

test("collection works with only ordinary suites", () => {
  assert.deepEqual(suiteBatches(["a.test.mjs"]).filter(batch => batch.length), [["a.test.mjs"]]);
});

test("private PostgreSQL clusters run alone without dropping or duplicating suites", () => {
  const postgres = ["email-deal-progress-postgres.test.mjs", "a02-rule-enforcement-postgres.test.mjs", "confirm-merge-schema.test.mjs",
    "journey-one-clock-input-store.v5.test.mjs", "journey-one-clock-store.v5.test.mjs",
    "lease-radar-postgres.test.mjs", "local-deals-store.test.mjs", "whats-new-store.test.mjs",
    "self-closing-records-sql.test.mjs"];
  const files = ["chrome-launch.test.mjs",
    "ordinary.test.mjs", ...postgres];
  const batches = suiteBatches(files);
  for (const file of postgres) assert.deepEqual(batches.find(batch => batch.includes(file)), [file]);
  assert.deepEqual(batches.flat().sort(), files.sort());
});

test("browser suites run alone even when their filenames do not say browser", () => {
  const files = ["workspace-command-center-browser.test.mjs", "chrome-launch.test.mjs",
    "shared-oauth.test.mjs", "chromium-binary.test.mjs", "ordinary.test.mjs"];
  const batches = suiteBatches(files, ["shared-oauth.test.mjs", "chromium-binary.test.mjs"]);
  for (const file of ["shared-oauth.test.mjs", "chromium-binary.test.mjs"]) {
    assert.deepEqual(batches.find(batch => batch.includes(file)), [file]);
  }
  assert.deepEqual(batches.flat().sort(), files.sort());
});

test("Fallow sees exactly the suites loaded dynamically by the Node runner", async () => {
  const config = JSON.parse(await readFile(new URL("../.fallowrc.json", import.meta.url), "utf8"));
  const files = await readdir(new URL("./", import.meta.url));
  const matches = file => config.dynamicallyLoaded.some(pattern => matchesGlob(`test/${file}`, pattern));
  assert.deepEqual(files.filter(matches).sort(), suiteBatches(files).flat().sort());
  for (const file of ["future.test.js", "future.test.mjs"]) assert.equal(matches(file), true, file);
  for (const file of ["helper.mjs", "future.js", "nested/future.test.mjs", "../src/unused.js"])
    assert.equal(matches(file), false, file);
});
