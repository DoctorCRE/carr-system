import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
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

function baselineScope(root: string): {
  base: string;
  javaScript: Set<string>;
  typeScript: Set<string>;
} {
  const branch = process.env.GITHUB_BASE_REF || "main";
  const base = `refs/remotes/origin/${branch}`;
  const git = (args: string[]) =>
    execFileSync("git", args, { cwd: root, encoding: "utf8", timeout: 30_000, stdio: "pipe" });
  try {
    const present = spawnSync("git", ["rev-parse", "--verify", `${base}^{commit}`], {
      cwd: root,
      timeout: 30_000,
      stdio: "pipe",
    });
    if (present.status !== 0) git(["fetch", "--depth=1", "origin", `refs/heads/${branch}:${base}`]);
    const path = "mcp-server/typescript/migration.json";
    if (!git(["ls-tree", "--name-only", base, "--", path]).trim()) {
      process.stdout.write(`Legacy baseline ${base} has no migration list; allowing initial gate introduction\n`);
      const paths = new Set(git(["ls-tree", "-r", "--name-only", base]).trim().split("\n"));
      return { base, javaScript: paths, typeScript: paths };
    }
    const parsed: unknown = JSON.parse(git(["show", `${base}:${path}`]));
    assert.ok(typeof parsed === "object" && parsed !== null && !Array.isArray(parsed));
    const scope = Object.fromEntries(Object.entries(parsed));
    const javaScript = new Set(strings(scope.legacyJavaScript));
    return { base, javaScript, typeScript: new Set(strings(scope.legacyTypeScript)) };
  } catch (cause) {
    throw new Error(`Cannot read legacy migration baseline from ${base}; refusing staged check`, { cause });
  }
}

export function migrationIssues(pkg: string, gated: string[]): string[] {
  const root = resolve(pkg, "..");
  const scope = object(resolve(pkg, "typescript/migration.json"));
  const allowed = new Set([...strings(scope.legacyJavaScript), ...strings(scope.toolExceptions)]);
  const legacy = new Set(strings(scope.legacyTypeScript));
  const paths = new Set(
    execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], {
      cwd: root,
      encoding: "utf8",
    })
      .split("\0")
      .filter((path) => path && existsSync(resolve(root, path))),
  );
  const issues: string[] = [];
  const baseline = baselineScope(root);
  for (const path of strings(scope.legacyJavaScript)) {
    if (!baseline.javaScript.has(path))
      issues.push(`Legacy JavaScript list may only shrink: ${path}; convert the file into a strict project`);
  }
  for (const path of legacy.keys()) {
    if (!baseline.typeScript.has(path))
      issues.push(`Legacy TypeScript list may only shrink: ${path}; convert the file into a strict project`);
  }
  for (const path of paths) {
    if (/\.(?:js|mjs|cjs|jsx)$/.test(path) && !allowed.has(path))
      issues.push(
        `New first-party JavaScript: ${path}; convert the file into a strict project, or for a pre-existing main file, list it`,
      );
    if (/\.[cm]?tsx?$/.test(path) && !gated.includes(resolve(root, path))) {
      const baseSource = spawnSync("git", ["show", `${baseline.base}:${path}`], { cwd: root, timeout: 30_000 });
      if (
        !legacy.has(path) ||
        baseSource.status !== 0 ||
        !readFileSync(resolve(root, path)).equals(baseSource.stdout)
      ) {
        issues.push(
          `${path} must join a strict project before it changes; convert the file into a strict project, or for a pre-existing main file, list it`,
        );
      }
    }
  }
  return issues;
}
