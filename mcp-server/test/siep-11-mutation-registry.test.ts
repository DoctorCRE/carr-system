// Test mutation registry contracts and default-deny dispatch.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const inventory: unknown = await import(new URL("../../ops/scac-mutation-inventory.ts", import.meta.url).href);
const assertCurrentContracts = record(inventory).assertCurrentContracts;
assert.ok(typeof assertCurrentContracts === "function");

import {
  assertClosedTopLevel,
  assertRegisteredOperation,
  MutationRegistryRefusal,
  mutationManifestIdentity,
  registeredOperation,
  SCAC_MUTATION_REGISTRY_DIGEST,
} from "../src/mutation-registry.ts";

const admission: unknown = await import(new URL("./helpers/registry-admission.mjs", import.meta.url).href);
const assertAdmits = record(admission).assertAdmits;
assert.ok(typeof assertAdmits === "function");
const legacy: unknown = await import(new URL("../src/tools.js", import.meta.url).href);
const TOOLS = record(record(legacy).TOOLS);
function record(value: unknown): Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null);
  return Object.fromEntries(Object.entries(value));
}
function requiredOperation(name: string) {
  const operation = registeredOperation(name);
  assert.ok(operation);
  return operation;
}
test("current generated write contracts match every assembled handler", () => {
  assert.equal(assertCurrentContracts(TOOLS), true);
});
test("unknown, changed, and open operation contracts refuse deterministically", async () => {
  const source = record(TOOLS["add-loop"]);
  await assert.rejects(
    assertRegisteredOperation("not-reviewed", source, {}),
    (error) => error instanceof MutationRegistryRefusal && error.error === "unregistered_operation",
  );
  await assert.rejects(
    assertRegisteredOperation("add-loop", { ...source, write: !source.write }, {}),
    (error) => error instanceof MutationRegistryRefusal && error.error === "mutation_contract_mismatch",
  );
  assert.throws(
    () => assertClosedTopLevel("add-loop", source, { text: "safe", actor: "joe" }),
    (error) => error instanceof MutationRegistryRefusal && error.error === "unregistered_operation_fields",
  );
  assert.equal(registeredOperation("not-reviewed"), null);
});

test("composites expose exact reviewed edges and generic dispatch stays default deny", () => {
  assert.deepEqual(requiredOperation("stamp-touch").delegates_to, ["log-activity"]);
  assert.deepEqual(requiredOperation("resolve-candidate").delegates_to, [
    "log-activity",
    "new-deal",
    "patch-deal-field",
    "set-next-step",
  ]);
  assert.deepEqual(requiredOperation("find-and-catch-up").delegates_to, ["catch-me-up", "find"]);
  assert.deepEqual(requiredOperation("prepare-conversation").delegates_to, ["find-and-catch-up", "who-do-we-know"]);
  assert.deepEqual(requiredOperation("morning-brief").delegates_to, [
    "claim-card",
    "deal-room-board",
    "loop-board",
    "today-triage",
  ]);
  assert.deepEqual(requiredOperation("call-verb").delegates_to, ["*registered_operation"]);
  assert.match(
    readFileSync(new URL("../src/tools.js", import.meta.url), "utf8"),
    /executeRegisteredTool\(c, actor, "log-activity"/,
  );
});

test("lead workspace verbs remain available and admitted", async () => {
  await assertAdmits([
    "find-rule",
    "record-lead-contact",
    "claim-lead",
    "link-lead-client",
    "update-lead",
    "lead-board",
    "unfinished-work",
  ]);
});

test("runtime manifest exposes the same sealed digest as registry consumers", () => {
  assert.equal(mutationManifestIdentity().registry_digest, SCAC_MUTATION_REGISTRY_DIGEST);
});
