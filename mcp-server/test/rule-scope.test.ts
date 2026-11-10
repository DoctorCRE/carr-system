// Test rule scope selection and visibility.

import assert from "node:assert/strict";
import { test } from "node:test";

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Expected object");
  return Object.fromEntries(Object.entries(value));
}
const registry = record(await import(new URL("../src/tools.js", import.meta.url).href));
const validate = registry.validateRuleScope;
const usable = registry.hasUsableRuleScope;
function validateRuleScope(...args: unknown[]) {
  if (typeof validate !== "function") throw new TypeError("Expected scope validator");
  return validate(...args);
}
function hasUsableRuleScope(scope: unknown): unknown {
  if (typeof usable !== "function") throw new TypeError("Expected scope predicate");
  return usable(scope);
}

import { ToolError } from "../src/tool-error.ts";

for (const scope of [
  { operation: "amend-rule" },
  { tools: ["Bash"] },
  { verb: "teach" },
  { bash_patterns: ["git push *"] },
  { path_globs: ["migrations/*.sql"] },
  { task: "Reviewing a migration" },
  { topic: "Salesforce phase reconciliation" },
  { moment: "Before amending a rule" },
  { applies_to: "attended CARR review work" },
  { source: "import", moment: "Before creating a contact" },
])
  test(`usable scope selects ${JSON.stringify(scope)}`, () => {
    assert.equal(hasUsableRuleScope(scope), true);
    assert.doesNotThrow(() => validateRuleScope(scope, { requireUsable: true }));
  });

for (const scope of [
  undefined,
  {},
  { section: "record-layer" },
  { source: "rule text", personal_to: "joe" },
  { kind: "personal", context: "Joe development, all his sessions" },
  { task: "development" },
  { tools: ["*"] },
  { moment: "   " },
  { topic: [] },
  { task: "all sessions" },
  { moment: {} },
  { verb: 42 },
])
  test(`scope lacks a selection condition ${JSON.stringify(scope)}`, () => {
    assert.equal(hasUsableRuleScope(scope), false);
    assert.throws(
      () => validateRuleScope(scope, { requireUsable: true }),
      (e) => e instanceof ToolError && e.payload.error === "rule_scope_required",
    );
    assert.doesNotThrow(() => validateRuleScope(scope), "backfill phase keeps shape-only validation");
  });
