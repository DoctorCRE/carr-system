import { test } from "node:test";
import assert from "node:assert/strict";
import { validateRuleScope, hasUsableRuleScope, ToolError } from "../src/tools.js";

for (const scope of [
  { operation: "amend-rule" }, { tools: ["Bash"] }, { verb: "teach" },
  { bash_patterns: ["git push *"] }, { path_globs: ["migrations/*.sql"] },
  { task: "Reviewing a migration" }, { topic: "Salesforce phase reconciliation" },
  { moment: "Before amending a rule" }, { applies_to: "attended CARR review work" },
  { source: "import", moment: "Before creating a contact" },
]) test(`usable scope selects ${JSON.stringify(scope)}`, () => {
  assert.equal(hasUsableRuleScope(scope), true);
  assert.doesNotThrow(() => validateRuleScope(scope, { requireUsable: true }));
});

for (const scope of [undefined, {}, { section: "record-layer" },
  { source: "rule text", personal_to: "joe" }, { kind: "personal", context: "Joe development, all his sessions" },
  { task: "development" }, { tools: ["*"] }, { moment: "   " }, { topic: [] },
  { task: "all sessions" }, { moment: {} }, { verb: 42 },
]) test(`scope lacks a selection condition ${JSON.stringify(scope)}`, () => {
  assert.equal(hasUsableRuleScope(scope), false);
  assert.throws(() => validateRuleScope(scope, { requireUsable: true }),
    e => e instanceof ToolError && e.payload.error === "rule_scope_required");
  assert.doesNotThrow(() => validateRuleScope(scope), "backfill phase keeps shape-only validation");
});
