import test from "node:test";
import assert from "node:assert/strict";
import { assertRequiredArgs } from "../src/tools.js";

const schema = {
  type: "object",
  properties: {
    name: { type: "string" },
    when: { type: ["string", "null"] },
    prior: { anyOf: [{ type: "string" }, { type: "null" }] },
  },
  required: ["name", "when", "prior"],
};

test("a declared-nullable required field accepts an explicit null", () => {
  assert.doesNotThrow(() => assertRequiredArgs(schema, { name: "x", when: null, prior: null }));
});

test("a non-nullable required field still refuses null", () => {
  assert.throws(() => assertRequiredArgs(schema, { name: null, when: null, prior: null }),
    (e) => e.payload.error === "missing_required" && e.payload.missing.join() === "name");
});

test("an absent key is still missing even when nullable", () => {
  assert.throws(() => assertRequiredArgs(schema, { name: "x", prior: null }),
    (e) => e.payload.missing.join() === "when");
});

test("an empty string is still missing even when nullable", () => {
  assert.throws(() => assertRequiredArgs(schema, { name: "x", when: "", prior: null }),
    (e) => e.payload.missing.join() === "when");
});
