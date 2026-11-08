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

export function projects(pkg: string): string[] {
  const base = object(resolve(pkg, "tsconfig.base.json"));
  assert.ok(typeof base.compilerOptions === "object" && base.compilerOptions !== null);
  const flags = Object.fromEntries(Object.entries(base.compilerOptions));
  for (const flag of [
    "strict",
    "noEmit",
    "allowImportingTsExtensions",
    "erasableSyntaxOnly",
    "verbatimModuleSyntax",
    "noUncheckedIndexedAccess",
    "noUnusedLocals",
    "noUnusedParameters",
    "exactOptionalPropertyTypes",
  ])
    assert.equal(flags[flag], true, flag);
  assert.equal(flags.target, "esnext");
  assert.equal(flags.module, "nodenext");
  assert.deepEqual(flags.types, ["node"]);
  return projectPaths.flatMap((path) => {
    const config = object(resolve(pkg, path));
    assert.equal(config.extends, "./tsconfig.base.json");
    assert.equal(config.include, undefined, "strict projects use explicit file lists");
    assert.equal(config.exclude, undefined, "strict projects cannot exclude admitted files");
    if (config.compilerOptions !== undefined) {
      assert.ok(typeof config.compilerOptions === "object" && config.compilerOptions !== null);
      for (const [key, value] of Object.entries(config.compilerOptions)) {
        if (flags[key] === true) assert.equal(value, true, `${path} ${key}`);
      }
    }
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
