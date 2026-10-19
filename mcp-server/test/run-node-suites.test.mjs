import test from "node:test";
import assert from "node:assert/strict";
import { suiteBatches } from "../run-tests.mjs";

test("every Node suite runs exactly once without the retired browser shim", () => {
  const files = ["z.test.js", "chrome-launch.test.mjs", "a.test.mjs", "future.test.js", "helper.mjs", "tour-feedback-browser.test.mjs", "future-browser.test.mjs"];
  const batches = suiteBatches(files);
  assert.deepEqual(batches[0], ["chrome-launch.test.mjs"]);
  assert.deepEqual(batches[1], ["future-browser.test.mjs"]);
  assert.deepEqual(batches[2], ["tour-feedback-browser.test.mjs"]);
  assert.deepEqual(batches[3], ["a.test.mjs", "future.test.js", "z.test.js"]);
  const all = batches.flat();
  assert.equal(new Set(all).size, all.length);
  assert.equal(all.length, files.length - 1);
});

test("collection works with only ordinary suites", () => {
  assert.deepEqual(suiteBatches(["a.test.mjs"]).filter(batch => batch.length), [["a.test.mjs"]]);
});

test("private PostgreSQL clusters run alone without dropping or duplicating suites", () => {
  const postgres = ["a02-rule-enforcement-postgres.test.mjs", "confirm-merge-schema.test.mjs",
    "journey-one-clock-input-store.v5.test.mjs", "journey-one-clock-store.v5.test.mjs",
    "lease-radar-postgres.test.mjs", "local-deals-store.test.mjs", "whats-new-store.test.mjs",
    "self-closing-records-sql.test.mjs"];
  const files = ["chrome-launch.test.mjs",
    "ordinary.test.mjs", ...postgres];
  const batches = suiteBatches(files);
  for (const file of postgres) assert.deepEqual(batches.find(batch => batch.includes(file)), [file]);
  assert.deepEqual(batches.flat().sort(), files.sort());
});
