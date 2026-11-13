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
  if (JSON.stringify(graph.project) !== JSON.stringify([...new Set(files.map((file) => relative(pkg, file)))].sort()))
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
    let syntax: ReturnType<typeof parse>;
    try {
      syntax = parse(readFileSync(file, "utf8"), { sourceType: "module", plugins: ["typescript", "jsx"] });
    } catch (error) {
      if (
        !(error instanceof SyntaxError) ||
        !("code" in error) ||
        (error.code !== "BABEL_PARSER_SYNTAX_ERROR" && error.code !== "BABEL_PARSER_SOURCETYPE_MODULE_REQUIRED") ||
        !("loc" in error) ||
        typeof error.loc !== "object" ||
        error.loc === null ||
        !("line" in error.loc) ||
        typeof error.loc.line !== "number" ||
        !("column" in error.loc) ||
        typeof error.loc.column !== "number"
      )
        throw error;
      throw new GateError(
        `${relative(pkg, file)}:${error.loc.line}:${error.loc.column + 1}`,
        error.message,
        "Repair this file's TypeScript syntax and rerun check:staged.",
      );
    }
    syntaxIssues(syntax);
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

  run("tsc", ["--build", "tsconfig.node-compat.json"]);
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
  if (!(error instanceof GateError)) throw error;
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
}
