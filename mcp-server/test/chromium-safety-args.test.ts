import assert from "node:assert/strict";
import test from "node:test";
import { CHROMIUM_SAFETY_ARGS } from "../../tools/chromium-safety-args.ts";

test("Chromium safety flags retain their exact values and order", () => {
  assert.deepEqual(CHROMIUM_SAFETY_ARGS, ["--use-mock-keychain", "--password-store=basic"]);
});

test("Chromium safety imports share one frozen array", async () => {
  const imported = await import("../../tools/chromium-safety-args.ts");
  assert.equal(imported.CHROMIUM_SAFETY_ARGS, CHROMIUM_SAFETY_ARGS);
  assert.equal(Object.isFrozen(CHROMIUM_SAFETY_ARGS), true);
  assert.throws(() => Object.assign(CHROMIUM_SAFETY_ARGS, { 0: "--unsafe" }), TypeError);
  assert.throws(() => Object.assign(CHROMIUM_SAFETY_ARGS, { 2: "--extra" }), TypeError);
  assert.deepEqual([...CHROMIUM_SAFETY_ARGS], ["--use-mock-keychain", "--password-store=basic"]);
});
