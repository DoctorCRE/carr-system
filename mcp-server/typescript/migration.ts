import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export class GateError extends Error {
  constructor(file: string, rule: string, remedy: string) {
    super(`${file}: ${rule}\nRemedy: ${remedy}`);
    this.name = "GateError";
  }
}

function requireRule(condition: unknown, file: string, rule: string, remedy: string): asserts condition {
  if (!condition) throw new GateError(file, rule, remedy);
}

export function object(path: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new GateError(path, "must be readable valid JSON", "Restore readable, valid JSON and rerun check:staged.");
  }
  requireRule(
    typeof value === "object" && value !== null && !Array.isArray(value),
    path,
    "must be a JSON object",
    "Replace the value with a JSON object and rerun check:staged.",
  );
  return Object.fromEntries(Object.entries(value));
}

function strings(value: unknown, file: string, field: string): string[] {
  requireRule(
    Array.isArray(value) && value.every((item: unknown) => typeof item === "string"),
    file,
    `${field} must be an array of paths`,
    `Restore ${field} as a path array; admit changed TypeScript to a strict project.`,
  );
  return value;
}

export const projectPaths = ["tsconfig.node.json", "tsconfig.worker.json"];

function compilerOptions(pkg: string, path: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(
      execFileSync(resolve(pkg, "node_modules/.bin/tsc"), ["--project", path, "--showConfig"], {
        cwd: pkg,
        encoding: "utf8",
        timeout: 120_000,
      }),
    );
  } catch {
    throw new GateError(
      path,
      "effective compiler configuration must resolve",
      "Repair this config and its extends chain, then rerun check:staged.",
    );
  }
  requireRule(
    typeof parsed === "object" && parsed !== null && "compilerOptions" in parsed,
    path,
    "compilerOptions must resolve",
    "Restore compilerOptions and rerun check:staged.",
  );
  const flags = parsed.compilerOptions;
  requireRule(
    typeof flags === "object" && flags !== null,
    path,
    "compilerOptions must be an object",
    "Restore compilerOptions and rerun check:staged.",
  );
  const options = Object.fromEntries(Object.entries(flags));
  requireRule(
    options.strict === true,
    path,
    "strict must be true",
    "Set strict=true in the effective config, including inherited overrides.",
  );
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
    requireRule(
      (options[flag] ?? options.strict) === true,
      path,
      `${flag} must be true`,
      `Set ${flag}=true in the effective config, including inherited overrides.`,
    );
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
    requireRule(
      options[flag] === true,
      path,
      `${flag} must be true`,
      `Set ${flag}=true in the effective config, including inherited overrides.`,
    );
  for (const flag of ["noCheck", "skipLibCheck", "skipDefaultLibCheck", "allowUnreachableCode", "allowUnusedLabels"])
    requireRule(
      options[flag] !== true,
      path,
      `${flag} suppresses diagnostics`,
      `Remove ${flag}=true from the effective config, including inherited overrides.`,
    );
  return options;
}

export function projects(pkg: string): string[] {
  const flags = compilerOptions(pkg, "tsconfig.base.json");
  for (const [flag, expected] of Object.entries({ target: "esnext", module: "nodenext", types: ["node"] }))
    requireRule(
      JSON.stringify(flags[flag]) === JSON.stringify(expected),
      "tsconfig.base.json",
      `${flag} must equal ${JSON.stringify(expected)}`,
      `Restore ${flag}=${JSON.stringify(expected)} in tsconfig.base.json.`,
    );
  for (const path of ["tsconfig.base.json", ...projectPaths]) {
    const config = object(resolve(pkg, path));
    for (const field of ["include", "exclude"])
      requireRule(
        config[field] === undefined,
        path,
        `${field} cannot change strict project coverage`,
        `Remove ${field}; admit files through the explicit files lists and Knip project list.`,
      );
  }
  return projectPaths.flatMap((path) => {
    const config = object(resolve(pkg, path));
    compilerOptions(pkg, path);
    return strings(config.files, path, "files").map((file) => resolve(pkg, file));
  });
}

function baselineScope(
  root: string,
  env: NodeJS.ProcessEnv,
): {
  base: string;
  javaScript: Set<string>;
  typeScript: Set<string>;
} {
  const git = (args: string[]) =>
    execFileSync("git", args, { cwd: root, encoding: "utf8", timeout: 30_000, stdio: "pipe" }).trim();
  let base = "unresolved";
  try {
    const candidate = git(["rev-parse", "--verify", "HEAD^{commit}"]);
    const mode = env.GITHUB_EVENT_NAME;
    if (mode === "pull_request" || mode === "merge_group") {
      const event = object(env.GITHUB_EVENT_PATH ?? "");
      const payload = event[mode];
      if (typeof payload !== "object" || payload === null) throw new Error("Missing event payload");
      const fields = Object.fromEntries(Object.entries(payload));
      let declared: unknown = fields.base_sha;
      if (mode === "pull_request") {
        const prBase = fields.base;
        declared = typeof prBase === "object" && prBase !== null && "sha" in prBase ? prBase.sha : undefined;
      }
      if (typeof declared !== "string" || !/^[0-9a-f]{40}$/.test(declared))
        throw new Error("Missing immutable event base SHA");
      base = declared;
      if (env.GITHUB_SHA !== candidate || (mode === "merge_group" && fields.head_sha !== candidate))
        throw new Error("Checkout does not match event candidate");
      if (mode === "pull_request") {
        const parents = git(["rev-list", "--parents", "-n", "1", candidate]).split(" ");
        const prHead = fields.head;
        if (
          parents.length !== 3 ||
          typeof prHead !== "object" ||
          prHead === null ||
          !("sha" in prHead) ||
          prHead.sha !== parents[2]
        )
          throw new Error("Synthetic merge does not match event PR head");
        const parent = parents[1];
        if (!parent || !git(["rev-list", "--first-parent", parent]).split("\n").includes(base))
          throw new Error("Event base is not on the synthetic merge's first-parent history");
        base = parent;
      } else if (!git(["rev-list", "--first-parent", candidate]).split("\n").includes(base)) {
        throw new Error("Event base is not on the merge group's first-parent history");
      }
    } else if (mode === "push") {
      const event = object(env.GITHUB_EVENT_PATH ?? "");
      if (env.GITHUB_SHA !== candidate || event.after !== candidate)
        throw new Error("Checkout does not match event candidate");
      if (typeof event.before !== "string" || !/^[0-9a-f]{40}$/.test(event.before))
        throw new Error("Missing immutable push base SHA");
      base =
        event.before === "0".repeat(40) ? git(["merge-base", candidate, "refs/remotes/origin/main"]) : event.before;
      if (base === candidate) throw new Error("Push baseline cannot be the candidate itself");
      git(["merge-base", "--is-ancestor", base, candidate]);
    } else {
      base = git(["merge-base", candidate, "refs/remotes/origin/main"]);
    }
    base = git(["rev-parse", "--verify", `${base}^{commit}`]);
    process.stdout.write(`Legacy migration baseline ${base}; candidate ${candidate}; mode ${mode ?? "local"}\n`);
    const path = "mcp-server/typescript/migration.json";
    if (!git(["ls-tree", "--name-only", base, "--", path]).trim()) {
      process.stdout.write(`Legacy baseline ${base} has no migration list; allowing initial gate introduction\n`);
      const paths = new Set(git(["ls-tree", "-r", "--name-only", base]).trim().split("\n"));
      return { base, javaScript: paths, typeScript: paths };
    }
    const parsed: unknown = JSON.parse(git(["show", `${base}:${path}`]));
    requireRule(
      typeof parsed === "object" && parsed !== null && !Array.isArray(parsed),
      path,
      "baseline must be an object",
      "Restore the base migration list.",
    );
    const scope = Object.fromEntries(Object.entries(parsed));
    const javaScript = new Set(
      strings(scope.legacyJavaScript, "mcp-server/typescript/migration.json", "legacyJavaScript"),
    );
    return {
      base,
      javaScript,
      typeScript: new Set(strings(scope.legacyTypeScript, "mcp-server/typescript/migration.json", "legacyTypeScript")),
    };
  } catch (error) {
    const detail = error instanceof Error ? error.message.split("\n")[0] : "Unknown baseline error";
    throw new GateError(
      "mcp-server/typescript/migration.json",
      `Cannot read legacy migration baseline from ${base}; refusing staged check (${detail})`,
      "Restore the event payload, matching candidate and exact base commit history (local: origin/main), then rerun check:staged. No later branch tip is substituted.",
    );
  }
}

export function migrationIssues(pkg: string, gated: string[], env: NodeJS.ProcessEnv = process.env): string[] {
  const root = resolve(pkg, "..");
  const scope = object(resolve(pkg, "typescript/migration.json"));
  requireRule(
    scope.toolExceptions === undefined,
    "mcp-server/typescript/migration.json",
    "toolExceptions is unsupported; new JavaScript must join a strict project",
    "Remove toolExceptions and convert new JavaScript into a strict TypeScript project.",
  );
  const allowed = new Set(strings(scope.legacyJavaScript, "mcp-server/typescript/migration.json", "legacyJavaScript"));
  const legacy = new Set(strings(scope.legacyTypeScript, "mcp-server/typescript/migration.json", "legacyTypeScript"));
  const paths = new Set(
    execFileSync("git", ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], {
      cwd: root,
      encoding: "utf8",
    })
      .split("\0")
      .filter((path) => path && existsSync(resolve(root, path))),
  );
  const issues: string[] = [];
  const baseline = baselineScope(root, env);
  const baselinePaths = new Set(
    execFileSync("git", ["ls-tree", "-r", "--name-only", baseline.base], { cwd: root, encoding: "utf8" })
      .trim()
      .split("\n"),
  );
  for (const path of strings(scope.legacyJavaScript, "mcp-server/typescript/migration.json", "legacyJavaScript")) {
    if (!baseline.javaScript.has(path))
      issues.push(`Legacy JavaScript list may only shrink: ${path}; convert the file into a strict project`);
  }
  for (const path of legacy.keys()) {
    if (!baseline.typeScript.has(path))
      issues.push(`Legacy TypeScript list may only shrink: ${path}; convert the file into a strict project`);
  }
  for (const path of paths) {
    if (/\.(?:js|mjs|cjs|jsx)$/.test(path) && (!allowed.has(path) || !baselinePaths.has(path)))
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
