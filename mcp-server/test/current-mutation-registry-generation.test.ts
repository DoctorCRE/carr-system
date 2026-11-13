// Tests reproducible generation of current mutation contracts.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const inventory: unknown = await import(new URL("../../ops/scac-mutation-inventory.ts", import.meta.url).href);
assert.ok(
  typeof inventory === "object" &&
    inventory !== null &&
    "renderCurrentContracts" in inventory &&
    typeof inventory.renderCurrentContracts === "function",
);
const render = inventory.renderCurrentContracts;
function renderCurrentContracts(tools: object, current: string): string {
  const output: unknown = render(tools, current);
  assert.ok(typeof output === "string");
  return output;
}
const registered: unknown = await import(new URL("../src/tools.js", import.meta.url).href);
assert.ok(typeof registered === "object" && registered !== null);
assert.ok("TOOLS" in registered);
function record(value: unknown): asserts value is Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value));
}
const TOOLS = registered.TOOLS;
record(TOOLS);
assert.ok(typeof TOOLS === "object" && TOOLS !== null);

const current = readFileSync(new URL("../src/scac-mutation-registry.current.generated.ts", import.meta.url), "utf8");

test("current contract generation is reproducible from the assembled tools", () => {
  assert.equal(renderCurrentContracts(TOOLS, current), current);
});

test("editing a module without changing any verb contract leaves the registry unchanged", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "registry-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const module = join(dir, "verbs.js");
  const tools = { example: { registrySource: module, inputSchema: { type: "object" }, write: true } };
  writeFileSync(module, "export const before = 1;\n");
  const before = renderCurrentContracts(tools, current);
  writeFileSync(module, "export const after = 2;\n");
  assert.equal(renderCurrentContracts(tools, current), before);
});

test("current contract generation removes a retired operation and preserves the database seal", async () => {
  const { "propose-ready-plan": removed, ...remaining } = TOOLS;
  assert.ok(removed);
  const output = renderCurrentContracts(remaining, current);
  const registry: unknown = await import(`data:text/javascript;base64,${Buffer.from(output).toString("base64")}`);
  record(registry);
  const operations = registry.SCAC_MUTATION_OPERATIONS;
  record(operations);
  assert.equal(operations["propose-ready-plan"], undefined);
  assert.equal(Object.keys(operations).length, Object.keys(remaining).length);
  assert.equal(
    output.slice(0, output.indexOf("export const SCAC_MUTATION_OPERATIONS")),
    current.slice(0, current.indexOf("export const SCAC_MUTATION_OPERATIONS")),
  );
});

test("generation is idempotent and independent of tool insertion order", () => {
  const reversed = Object.fromEntries(Object.entries(TOOLS).reverse());
  const first = renderCurrentContracts(reversed, current);
  assert.equal(first, current);
  assert.equal(renderCurrentContracts(TOOLS, first), first);
});

test("schema changes update only the operation projection and keep sealed metadata", () => {
  const tools = {
    example: { registrySource: "ops/scac-mutation-inventory.ts", inputSchema: { type: "object" }, write: false },
  };
  const first = renderCurrentContracts(tools, current);
  const second = renderCurrentContracts({ example: { ...tools.example, inputSchema: { type: "string" } } }, current);
  assert.notEqual(first, second);
  const prefix = current.slice(0, current.indexOf("export const SCAC_MUTATION_OPERATIONS"));
  assert.ok(first.startsWith(prefix));
  assert.ok(second.startsWith(prefix));
});

test("the renamed TypeScript generator remains a discovered script entrypoint", async () => {
  const { isScriptEntrypoint } = await import("../../ops/scac-mutation-inventory.ts");
  assert.equal(isScriptEntrypoint("ops/scac-mutation-inventory.ts", false, "#!/usr/bin/env node\n"), true);
  assert.equal(isScriptEntrypoint("ops/test-fixture.ts", false, "#!/usr/bin/env node\n"), false);
});

test("plist parsing preserves values across comments and XML declarations", async () => {
  const { parsePlistXml } = await import("../../ops/scac-mutation-inventory.ts");
  assert.deepEqual(
    parsePlistXml(
      '<?xml version="1.0"?><!DOCTYPE plist><plist><!-- outer --><dict><key>Label</key><string>before<!-- inner -->after &amp; safe</string><key>RunAtLoad</key><false/></dict></plist>',
    ),
    { Label: "beforeafter & safe", RunAtLoad: false },
  );
});

test("plist parsing refuses malformed comment boundaries", async () => {
  const { parsePlistXml } = await import("../../ops/scac-mutation-inventory.ts");
  assert.throws(() => parsePlistXml("<<!-- removed -->!--<plist><dict/></plist>"));
  assert.throws(() => parsePlistXml("<!-- unclosed"));
});
