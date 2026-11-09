import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import { parse } from "@babel/parser";
import { GateError, migrationIssues, object, projectPaths, projects } from "./migration.ts";

function main(): void {
  const pkg = resolve(import.meta.dirname, "..");
  const files = projects(pkg);
  const issues = migrationIssues(pkg, files);
  const graph = object(resolve(pkg, "knip.json"));
  if (JSON.stringify(graph.project) !== JSON.stringify(files.map((file) => relative(pkg, file))))
    throw new GateError(
      "knip.json",
      "project must cover every admitted file",
      "Align project with the strict configs' explicit files lists.",
    );

  function syntaxIssues(value: unknown): void {
    if (Array.isArray(value)) {
      for (const child of value) syntaxIssues(child);
    } else if (typeof value === "object" && value !== null) {
      if ("type" in value) {
        if (value.type === "TSAnyKeyword") issues.push("Explicit any is forbidden");
        if (value.type === "TSNonNullExpression") issues.push("Non-null assertions are forbidden");
        if (
          (value.type === "CommentLine" || value.type === "CommentBlock") &&
          "value" in value &&
          typeof value.value === "string" &&
          /@ts-(?:ignore|expect-error|nocheck)|biome-ignore/.test(value.value)
        ) {
          issues.push("Suppression directives are forbidden");
        }
      }
      for (const child of Object.values(value)) syntaxIssues(child);
    }
  }

  for (const file of files) {
    const start = issues.length;
    syntaxIssues(parse(readFileSync(file, "utf8"), { sourceType: "module", plugins: ["typescript", "jsx"] }));
    for (let index = start; index < issues.length; index++) issues[index] = `${relative(pkg, file)}: ${issues[index]}`;
  }
  if (issues.length) {
    process.stderr.write(
      `${issues.join("\n")}\nRemedy: Resolve each file refusal above; convert new or changed files into a strict project and remove prohibited syntax.\n`,
    );
    process.exit(1);
  }

  function run(tool: string, args: string[]): void {
    const result = spawnSync(resolve(pkg, "node_modules/.bin", tool), args, {
      cwd: pkg,
      stdio: "inherit",
      timeout: 120_000,
    });
    if (result.error || result.status !== 0)
      throw new GateError(
        tool,
        "staged tool check failed",
        `Resolve the ${tool} diagnostics above and rerun check:staged.`,
      );
  }

  for (const path of projectPaths) {
    const config = object(resolve(pkg, path));
    if (Array.isArray(config.files) && config.files.length > 0) run("tsc", ["-p", path]);
    else process.stdout.write(`${path}: no admitted files yet; legacy Worker contracts remain in typecheck\n`);
  }
  run("biome", ["check", "--error-on-warnings", ...files.map((file) => relative(pkg, file))]);
  // Legacy dependencies remain owned by their package checks until their modules join this graph.
  run("knip", ["--include", "files,exports,types,duplicates"]);
}

try {
  main();
} catch (error) {
  const refusal =
    error instanceof GateError
      ? error
      : new GateError(
          "typescript/check.ts",
          "staged input could not be checked",
          "Repair the admitted files and gate inputs, then rerun check:staged.",
        );
  process.stderr.write(`${refusal.message}\n`);
  process.exitCode = 1;
}
