// Tests reproducible generation of current mutation contracts.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { currentContracts } from "../../ops/scac-mutation-inventory.mjs";
import { SCAC_MUTATION_OPERATIONS } from "../src/scac-mutation-registry.current.generated.js";
import { TOOLS } from "../src/tools.js";

const current = readFileSync(new URL("../src/scac-mutation-registry.current.generated.js", import.meta.url), "utf8");

test("current contract generation is reproducible from the assembled tools", () => {
  assert.deepEqual(currentContracts(TOOLS), SCAC_MUTATION_OPERATIONS);
});

test("editing a module without changing any verb contract leaves the registry unchanged", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "registry-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const module = join(dir, "verbs.js");
  const tools = { example: { registrySource: module, inputSchema: { type: "object" }, write: true } };
  writeFileSync(module, "export const before = 1;\n");
  const before = currentContracts(tools, current);
  writeFileSync(module, "export const after = 2;\n");
  assert.deepEqual(currentContracts(tools), before);
});

test("current contract generation removes a retired operation and preserves the database seal", async () => {
  const { "propose-ready-plan": removed, ...remaining } = TOOLS;
  assert.ok(removed);
  const output = currentContracts(remaining);
  assert.equal(output['propose-ready-plan'], undefined);
  assert.equal(Object.keys(output).length, Object.keys(remaining).length);
  assert.equal(readFileSync(new URL('../src/scac-mutation-registry.current.generated.js', import.meta.url), 'utf8'), current);
});
