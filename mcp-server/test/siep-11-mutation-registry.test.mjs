// Test mutation registry contracts and default-deny dispatch.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { assertCurrentContracts, sha256 } from "../../ops/scac-mutation-inventory.mjs";
import { assertClosedTopLevel, assertRegisteredOperation, MutationRegistryRefusal, registeredOperation } from "../src/mutation-registry.js";
import { TOOLS } from "../src/tools.js";
test("current generated write contracts match every assembled handler", () => {
  assert.equal(assertCurrentContracts(TOOLS), true);
});
test("unknown, changed, and open operation contracts refuse deterministically", async () => {
  const source = TOOLS["add-loop"];
  await assert.rejects(
    assertRegisteredOperation("not-reviewed", source, {}),
    error => error instanceof MutationRegistryRefusal && error.error === "unregistered_operation",
  );
  await assert.rejects(
    assertRegisteredOperation("add-loop", { ...source, write: !source.write }, {}),
    error => error instanceof MutationRegistryRefusal && error.error === "mutation_contract_mismatch",
  );
  assert.throws(
    () => assertClosedTopLevel("add-loop", source, { text: "safe", actor: "joe" }),
    error => error instanceof MutationRegistryRefusal && error.error === "unregistered_operation_fields",
  );
  assert.equal(registeredOperation("not-reviewed"), null);
});

test("composites expose exact reviewed edges and generic dispatch stays default deny", () => {
  assert.deepEqual(registeredOperation("stamp-touch").delegates_to, ["log-activity"]);
  assert.deepEqual(registeredOperation("resolve-candidate").delegates_to,
    ["log-activity", "new-deal", "patch-deal-field", "set-next-step"]);
  assert.deepEqual(registeredOperation("find-and-catch-up").delegates_to, ["catch-me-up", "find"]);
  assert.deepEqual(registeredOperation("prepare-conversation").delegates_to,
    ["find-and-catch-up", "who-do-we-know"]);
  assert.deepEqual(registeredOperation("morning-brief").delegates_to,
    ["claim-card", "deal-room-board", "loop-board", "today-triage"]);
  assert.deepEqual(registeredOperation("call-verb").delegates_to, ["*registered_operation"]);
  assert.match(readFileSync(new URL("../src/tools.js", import.meta.url), "utf8"),
    /executeRegisteredTool\(c, actor, "log-activity"/);
});
