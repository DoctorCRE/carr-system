import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export function object(path: string): Record<string, unknown> {
  const value: unknown = JSON.parse(readFileSync(path, "utf8"));
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value), path);
  return Object.fromEntries(Object.entries(value));
}

function strings(value: unknown): string[] {
  assert.ok(Array.isArray(value) && value.every((item: unknown) => typeof item === "string"));
  return value;
}

export const projectPaths = ["tsconfig.node.json", "tsconfig.worker.json"];

function compilerOptions(pkg: string, path: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(
    execFileSync(resolve(pkg, "node_modules/.bin/tsc"), ["--project", path, "--showConfig"], {
      cwd: pkg,
      encoding: "utf8",
      timeout: 120_000,
    }),
  );
  assert.ok(typeof parsed === "object" && parsed !== null && "compilerOptions" in parsed, path);
  const flags = parsed.compilerOptions;
  assert.ok(typeof flags === "object" && flags !== null, path);
  const options = Object.fromEntries(Object.entries(flags));
  assert.equal(options.strict, true, `${path} strict`);
  for (const flag of [
    "noImplicitAny",
    "strictNullChecks",
    "strictFunctionTypes",
    "strictBindCallApply",
    "strictPropertyInitialization",
    "noImplicitThis",
    "alwaysStrict",
    "useUnknownInCatchVariables",
    "strictBuiltinIteratorReturn",
  ])
    assert.equal(options[flag] ?? options.strict, true, `${path} ${flag}`);
  for (const flag of [
    "noEmit",
    "allowImportingTsExtensions",
    "erasableSyntaxOnly",
    "verbatimModuleSyntax",
    "noUncheckedIndexedAccess",
    "noUnusedLocals",
    "noUnusedParameters",
    "exactOptionalPropertyTypes",
  ])
    assert.equal(options[flag], true, `${path} ${flag}`);
  for (const flag of ["noCheck", "skipLibCheck", "skipDefaultLibCheck", "allowUnreachableCode", "allowUnusedLabels"])
    assert.notEqual(options[flag], true, `${path} ${flag} suppresses diagnostics`);
  return options;
}

export function projects(pkg: string): string[] {
  const flags = compilerOptions(pkg, "tsconfig.base.json");
  assert.equal(flags.target, "esnext");
  assert.equal(flags.module, "nodenext");
  assert.deepEqual(flags.types, ["node"]);
  return projectPaths.flatMap((path) => {
    const config = object(resolve(pkg, path));
    assert.equal(config.include, undefined, "strict projects use explicit file lists");
    assert.equal(config.exclude, undefined, "strict projects cannot exclude admitted files");
    compilerOptions(pkg, path);
    return strings(config.files).map((file) => resolve(pkg, file));
  });
}

export function migrationIssues(pkg: string, gated: string[]): string[] {
  const root = resolve(pkg, "..");
  const scope = object(resolve(pkg, "typescript/migration.json"));
  const allowed = new Set([...strings(scope.legacyJavaScript), ...strings(scope.toolExceptions)]);
  assert.ok(typeof scope.legacyTypeScript === "object" && scope.legacyTypeScript !== null);
  const legacy = new Map(Object.entries(scope.legacyTypeScript));
  const paths = new Set(
    execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], {
      cwd: root,
      encoding: "utf8",
    })
      .split("\0")
      .filter((path) => path && existsSync(resolve(root, path))),
  );
  const issues: string[] = [];
  for (const path of paths) {
    if (/\.(?:js|mjs|cjs|jsx)$/.test(path) && !allowed.has(path)) issues.push(`New first-party JavaScript: ${path}`);
    if (/\.[cm]?tsx?$/.test(path) && !gated.includes(resolve(root, path))) {
      const digest = createHash("sha256")
        .update(readFileSync(resolve(root, path)))
        .digest("hex");
      const expected = legacy.get(path);
      if (typeof expected !== "object" || expected === null || !("sha256" in expected) || expected.sha256 !== digest) {
        issues.push(`${path} must join a strict project before it changes`);
      }
    }
  }
  return issues;
}
