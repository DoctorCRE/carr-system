import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { object } from "./migration.ts";

const source = resolve(import.meta.dirname, "..");
const fixtureEnv = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith("GIT_")));

test("staged gate accepts clean code and refuses each negative seed", () => {
  const root = mkdtempSync(join(tmpdir(), "carr-typescript-"));
  try {
    const pkg = join(root, "mcp-server");
    cpSync(join(source, "typescript"), join(pkg, "typescript"), { recursive: true });
    for (const file of [
      "package.json",
      "tsconfig.base.json",
      "tsconfig.node.json",
      "tsconfig.worker.json",
      "biome.json",
      "knip.json",
    ]) {
      cpSync(join(source, file), join(pkg, file));
    }
    symlinkSync(join(source, "node_modules"), join(pkg, "node_modules"), "dir");
    writeFileSync(
      join(pkg, "typescript/migration.json"),
      JSON.stringify({ legacyJavaScript: [], toolExceptions: [], legacyTypeScript: {} }),
    );
    execFileSync("git", ["init", "-q", root], { env: fixtureEnv });
    writeFileSync(join(root, ".gitignore"), "node_modules/\n");
    const run = () =>
      spawnSync(process.execPath, [join(pkg, "typescript/check.ts")], {
        cwd: root,
        env: fixtureEnv,
        encoding: "utf8",
        timeout: 120_000,
      });
    const clean = run();
    assert.equal(clean.status, 0, clean.stdout + clean.stderr);
    const cases: [string, string, RegExp][] = [
      ["outside.js", "export const value = 1;\n", /new first-party JavaScript/i],
      ["mcp-server/new.js", "export const value = 1;\n", /new first-party JavaScript/i],
      ["mcp-server/typescript/seed.ts", "export const value: any = 1;\n", /any/i],
      ["mcp-server/typescript/seed.ts", "// @ts-ignore\nexport const value = 1;\n", /suppression/i],
      ["mcp-server/typescript/seed.ts", "export const value = [1][0]!;\n", /non-null/i],
      ["mcp-server/typescript/seed.ts", "export const value = 1\n", /biome|format/i],
      [
        "mcp-server/typescript/seed.ts",
        "export const usedSeed = 1;\nexport const unusedSeed = 2;\n",
        /Unused exports/i,
      ],
      ["mcp-server/typescript/seed.ts", "export const value: string = 1;\n", /TS2322/],
      ["mcp-server/unlisted.ts", "export {};\n", /strict project/i],
    ];
    for (const [path, text, expected] of cases) {
      assert.ok(path && text && expected instanceof RegExp);
      const nodeConfig = join(pkg, "tsconfig.node.json");
      const original = readFileSync(nodeConfig, "utf8");
      const graphPath = join(pkg, "knip.json");
      const originalGraph = readFileSync(graphPath, "utf8");
      if (path.endsWith("seed.ts")) {
        const config: unknown = JSON.parse(original);
        assert.ok(typeof config === "object" && config !== null && "files" in config && Array.isArray(config.files));
        config.files.push("typescript/seed.ts");
        const graph: unknown = JSON.parse(originalGraph);
        assert.ok(typeof graph === "object" && graph !== null && "project" in graph && Array.isArray(graph.project));
        graph.project.push("typescript/seed.ts");
        writeFileSync(graphPath, JSON.stringify(graph));
        writeFileSync(nodeConfig, JSON.stringify(config));
      }
      const file = join(root, path);
      const testPath = join(pkg, "typescript/gate.test.ts");
      const originalTest = readFileSync(testPath, "utf8");
      if (text.includes("unusedSeed")) {
        writeFileSync(
          testPath,
          originalTest
            .replace(
              'import { object } from "./migration.ts";',
              'import { object } from "./migration.ts";\nimport { usedSeed } from "./seed.ts";',
            )
            .replace("const source =", "assert.equal(usedSeed, 1);\n\nconst source ="),
        );
      }
      writeFileSync(file, text);
      const result = run();
      rmSync(file);
      writeFileSync(nodeConfig, original);
      writeFileSync(graphPath, originalGraph);
      writeFileSync(testPath, originalTest);
      assert.notEqual(result.status, 0, path);
      assert.match(result.stdout + result.stderr, expected, path);
    }
    const legacyFile = join(pkg, "legacy.ts");
    const legacySource = "export const legacy = 1;\n";
    writeFileSync(legacyFile, legacySource);
    writeFileSync(
      join(pkg, "typescript/migration.json"),
      JSON.stringify({
        legacyJavaScript: [],
        toolExceptions: [],
        legacyTypeScript: {
          "mcp-server/legacy.ts": { sha256: createHash("sha256").update(legacySource).digest("hex") },
        },
      }),
    );
    const unchanged = run();
    assert.equal(unchanged.status, 0, unchanged.stdout + unchanged.stderr);
    writeFileSync(legacyFile, `${legacySource}export const changed = 2;\n`);
    const changed = run();
    assert.notEqual(changed.status, 0);
    assert.match(changed.stdout + changed.stderr, /strict project/);
    rmSync(legacyFile);
    const failures: string[] = [];
    for (const path of ["tsconfig.base.json", "tsconfig.node.json", "tsconfig.worker.json"]) {
      const configPath = join(pkg, path);
      const original = readFileSync(configPath, "utf8");
      const overrides: [string, boolean][] = [
        ["strict", false],
        ["noImplicitAny", false],
        ["strictNullChecks", false],
        ["strictFunctionTypes", false],
        ["strictBindCallApply", false],
        ["strictPropertyInitialization", false],
        ["noImplicitThis", false],
        ["alwaysStrict", false],
        ["useUnknownInCatchVariables", false],
        ["strictBuiltinIteratorReturn", false],
        ["noUncheckedIndexedAccess", false],
        ["noUnusedLocals", false],
        ["noUnusedParameters", false],
        ["exactOptionalPropertyTypes", false],
        ["erasableSyntaxOnly", false],
        ["verbatimModuleSyntax", false],
        ["noCheck", true],
        ["skipLibCheck", true],
        ["skipDefaultLibCheck", true],
        ["allowUnreachableCode", true],
        ["allowUnusedLabels", true],
      ];
      for (const [flag, value] of overrides) {
        const config = object(configPath);
        config.compilerOptions ??= {};
        assert.ok(typeof config.compilerOptions === "object" && config.compilerOptions !== null);
        config.compilerOptions = { ...config.compilerOptions, [flag]: value };
        writeFileSync(configPath, JSON.stringify(config));
        const result = run();
        writeFileSync(configPath, original);
        if (result.status === 0) failures.push(`${path} accepted ${flag}=${value}`);
        else assert.match(result.stdout + result.stderr, new RegExp(flag), `${path} ${flag}`);
      }
      if (path === "tsconfig.base.json") continue;
      const intermediate = join(pkg, "tsconfig.intermediate.json");
      const config = object(configPath);
      config.extends = "./tsconfig.intermediate.json";
      writeFileSync(configPath, JSON.stringify(config));
      writeFileSync(intermediate, JSON.stringify({ extends: "./tsconfig.base.json" }));
      const inheritedClean = run();
      if (inheritedClean.status !== 0) failures.push(`${path} refused clean intermediate extends`);
      writeFileSync(
        intermediate,
        JSON.stringify({ extends: "./tsconfig.base.json", compilerOptions: { noImplicitAny: false } }),
      );
      const inheritedOverride = run();
      if (inheritedOverride.status === 0) failures.push(`${path} accepted inherited noImplicitAny=false`);
      writeFileSync(configPath, original);
      rmSync(intermediate);
    }
    assert.deepEqual(failures, [], failures.join("\n"));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("package owners require Node 26 and pin every dependency exactly", () => {
  for (const path of [join(source, "package.json"), resolve(source, "../practice-plugin/package.json")]) {
    const pkg = object(path);
    assert.ok(typeof pkg.engines === "object" && pkg.engines !== null && "node" in pkg.engines);
    assert.equal(pkg.engines.node, ">=26");
    for (const name of ["dependencies", "devDependencies", "overrides"]) {
      const group = pkg[name];
      assert.ok(typeof group === "object" && group !== null);
      for (const version of Object.values(group))
        assert.match(String(version), /^(?:\d+\.\d+\.\d+(?:-[\w.]+)?|\$\w+)$/);
    }
  }
});
