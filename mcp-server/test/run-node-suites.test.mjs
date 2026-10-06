import test from "node:test";
import assert from "node:assert/strict";
import { suiteBatches } from "../run-tests.mjs";

test("the Chrome shim runs alone, and every other Node suite still runs exactly once", () => {
  const files = ["z.test.js", "workspace-command-center-browser.test.mjs", "chrome-launch.test.mjs", "a.test.mjs", "future.test.js", "helper.mjs", "tour-feedback-browser.test.mjs", "future-browser.test.mjs"];
  const batches = suiteBatches(files);
  assert.deepEqual(batches[0], ["workspace-command-center-browser.test.mjs"]);
  assert.deepEqual(batches[1], ["chrome-launch.test.mjs"]);
  assert.deepEqual(batches[2], ["future-browser.test.mjs"]);
  assert.deepEqual(batches[3], ["tour-feedback-browser.test.mjs"]);
  assert.deepEqual(batches[4], ["a.test.mjs", "future.test.js", "z.test.js"]);
  const all = batches.flat();
  assert.equal(new Set(all).size, all.length);
  assert.equal(all.length, files.length - 1);
});

test("a missing browser shim fails collection instead of silently dropping browser coverage", () => {
  assert.throws(() => suiteBatches(["a.test.mjs"]), /browser shim/);
});

test("private PostgreSQL clusters run alone without dropping or duplicating suites", () => {
  const postgres = ["a02-rule-enforcement-postgres.test.mjs", "confirm-merge-schema.test.mjs",
    "journey-one-clock-input-store.v5.test.mjs", "journey-one-clock-store.v5.test.mjs",
    "lease-radar-postgres.test.mjs", "local-deals-store.test.mjs", "whats-new-store.test.mjs"];
  const files = ["workspace-command-center-browser.test.mjs", "chrome-launch.test.mjs",
    "ordinary.test.mjs", ...postgres];
  const batches = suiteBatches(files);
  for (const file of postgres) assert.deepEqual(batches.find(batch => batch.includes(file)), [file]);
  assert.deepEqual(batches.flat().sort(), files.sort());
});
