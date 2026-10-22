import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { renderCurrentContracts } from "../../ops/scac-mutation-inventory.mjs";
import { TOOLS } from "../src/tools.js";

const current = readFileSync(new URL("../src/scac-mutation-registry.current.generated.js", import.meta.url), "utf8");

test("current contract generation is reproducible from the assembled tools", () => {
  assert.equal(renderCurrentContracts(TOOLS, current), current);
});

test("editing a module without changing any verb contract leaves the registry unchanged", () => {
  const module = join(mkdtempSync(join(tmpdir(), "registry-")), "verbs.js");
  const tools = { example: { registrySource: module, inputSchema: { type: "object" }, write: true } };
  writeFileSync(module, "export const before = 1;\n");
  const before = renderCurrentContracts(tools, current);
  writeFileSync(module, "export const after = 2;\n");
  assert.equal(renderCurrentContracts(tools, current), before);
});

test("current contract generation removes a retired operation and preserves the database seal and grants", async () => {
  const { "propose-ready-plan": removed, ...remaining } = TOOLS;
  assert.ok(removed);
  const output = renderCurrentContracts(remaining, current);
  const registry = await import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
  assert.equal(registry.SCAC_MUTATION_OPERATIONS["propose-ready-plan"], undefined);
  assert.equal(Object.keys(registry.SCAC_MUTATION_OPERATIONS).length, Object.keys(remaining).length);
  assert.equal(output.slice(0, output.indexOf("export const SCAC_MUTATION_OPERATIONS")),
    current.slice(0, current.indexOf("export const SCAC_MUTATION_OPERATIONS")));
  assert.equal(output.slice(output.indexOf("export const SCAC_MUTATION_CURRENT_DB_GRANTS")),
    current.slice(current.indexOf("export const SCAC_MUTATION_CURRENT_DB_GRANTS")));
});
