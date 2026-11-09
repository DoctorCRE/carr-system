// Test validation of nullable required arguments.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { ToolError } from "../src/tool-error.ts";

// Keep the legacy registry at its runtime seam until its separate conversion.
const registryPath = "../src/tools.js";
const registry: unknown = await import(registryPath);
function object(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
const legacy = object(registry);
const validate = legacy.assertRequiredArgs;
assert.ok(typeof validate === "function");
function assertRequiredArgs(schema: object, args: object): void {
  assert.ok(typeof validate === "function");
  validate(schema, args);
}

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
  assert.throws(
    () => assertRequiredArgs(schema, { name: null, when: null, prior: null }),
    (e) =>
      e instanceof ToolError &&
      e.payload.error === "missing_required" &&
      Array.isArray(e.payload.missing) &&
      e.payload.missing.join() === "name",
  );
});

test("an absent key is still missing even when nullable", () => {
  assert.throws(
    () => assertRequiredArgs(schema, { name: "x", prior: null }),
    (e) => e instanceof ToolError && Array.isArray(e.payload.missing) && e.payload.missing.join() === "when",
  );
});

test("an empty string is still missing even when nullable", () => {
  assert.throws(
    () => assertRequiredArgs(schema, { name: "x", when: "", prior: null }),
    (e) => e instanceof ToolError && Array.isArray(e.payload.missing) && e.payload.missing.join() === "when",
  );
});

test("registry imports reject nonexistent APIs and never infer any", () => {
  const server = fileURLToPath(new URL("..", import.meta.url));
  const scratch = mkdtempSync(join(tmpdir(), "registry-types-"));
  const suites = [
    ["prepare-conversation", "TOOLS", "executeRegisteredTool"],
    ["promote-pool-research-gate", "TOOLS"],
    ["renewal-lease-ledger", "TOOLS", "executeRegisteredTool"],
    ["required-args-nullable", "assertRequiredArgs"],
    ["research-sites", "TOOLS", "executeRegisteredTool"],
    ["resource-observation", "executeRegisteredTool"],
  ];
  try {
    const files: string[] = [];
    const sources: string[] = [];
    for (const [suite, ...bindings] of suites) {
      assert.ok(suite);
      const source = readFileSync(resolve(server, "test", `${suite}.test.ts`), "utf8").replaceAll(
        '"../src/',
        `"${resolve(server, "src")}/`,
      );
      const path = join(scratch, `${suite}.mts`);
      files.push(path);
      sources.push(source);
      writeFileSync(path, source);
      assert.ok(bindings.length);
    }
    const config = join(scratch, "tsconfig.json");
    writeFileSync(
      config,
      JSON.stringify({
        extends: resolve(server, "tsconfig.node.json"),
        files,
        compilerOptions: {
          noUnusedLocals: false,
          noUnusedParameters: false,
          typeRoots: [resolve(server, "node_modules/@types")],
        },
      }),
    );
    const compile = () =>
      spawnSync(process.execPath, [resolve(server, "node_modules/typescript/bin/tsc"), "-p", config], {
        encoding: "utf8",
        timeout: 60_000,
      });
    const positive = compile();
    assert.equal(positive.status, 0, positive.stdout + positive.stderr);
    for (const [index, [, ...bindings]] of suites.entries()) {
      const path = files[index];
      const source = sources[index];
      assert.ok(path && source);
      writeFileSync(
        path,
        source +
          "\ntype IsAny<T> = 0 extends (1 & T) ? true : false;\n" +
          bindings
            .map(
              (binding) =>
                `${binding}.thisMemberDoesNotExist();\nconst unchecked${binding}: IsAny<typeof ${binding}> = true;\n`,
            )
            .join(""),
      );
    }
    const negative = compile();
    assert.equal(negative.status, 1, negative.stdout + negative.stderr);
    for (const [suite] of suites) {
      const diagnostics = negative.stdout.split("\n").filter((line) => line.includes(`${suite}.mts(`));
      assert.ok(
        diagnostics.some((line) => line.includes("TS2339") && line.includes("thisMemberDoesNotExist")),
        diagnostics.join("\n"),
      );
      assert.ok(
        diagnostics.some((line) => line.includes("TS2322") && line.includes("false")),
        diagnostics.join("\n"),
      );
    }
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
});
