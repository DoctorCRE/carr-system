import { spawn, spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { discoverSuites } from "./suite-discovery.ts";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const LAUNCH_REGRESSIONS = "chrome-launch.test.mjs";

export function suiteBatches(files: string[], browserFiles: string[] = [], postgresFiles: string[] = []): string[][] {
  const suites = files.filter((file) => /\.test\.(?:js|mjs|ts)$/.test(file)).sort();
  const browsers = suites.filter(
    (file) =>
      file !== LAUNCH_REGRESSIONS && (browserFiles.includes(file) || /-browser\.test\.(?:js|mjs|ts)$/.test(file)),
  );
  const postgres = suites.filter(
    (file) =>
      (postgresFiles.includes(file) || /-postgres\.test\.(?:js|mjs|ts)$/.test(file)) && !browsers.includes(file),
  );
  return [
    suites.filter((file) => file === LAUNCH_REGRESSIONS),
    ...browsers.map((file) => [file]),
    ...postgres.map((file) => [file]),
    suites.filter((file) => ![LAUNCH_REGRESSIONS, ...browsers, ...postgres].includes(file)),
  ];
}

type Snapshot = { snapshot: string; key: string; close: () => Promise<void> };

async function snapshot(value: unknown): Promise<Snapshot> {
  if (typeof value !== "object" || value === null || !("close" in value) || typeof value.close !== "function")
    throw new Error("Invalid PostgreSQL snapshot handle");
  const close = value.close;
  if (
    !("snapshot" in value) ||
    typeof value.snapshot !== "string" ||
    !("key" in value) ||
    typeof value.key !== "string"
  ) {
    await close();
    throw new Error("Invalid PostgreSQL snapshot metadata");
  }
  return {
    snapshot: value.snapshot,
    key: value.key,
    close: async () => {
      await close();
    },
  };
}

async function main() {
  const inventory: unknown = JSON.parse(await readFile(path.join(ROOT, "suite-inventory.json"), "utf8"));
  const discovered = await discoverSuites(path.join(ROOT, "test"), inventory);
  const selected: unknown =
    process.env.CARR_CI_UNIT_SUITES === undefined ? undefined : JSON.parse(process.env.CARR_CI_UNIT_SUITES);
  if (
    selected !== undefined &&
    (!Array.isArray(selected) ||
      !selected.length ||
      new Set(selected).size !== selected.length ||
      selected.some((file: unknown) => typeof file !== "string" || !discovered.includes(file)))
  )
    throw new Error("unit shard has empty, duplicate or missing suites");
  const files: string[] = Array.isArray(selected) ? selected : discovered;
  const browserFiles: string[] = [];
  const postgresFiles: string[] = [];
  for (const file of files.filter((file) => /\.test\.(?:js|mjs|ts)$/.test(file))) {
    const source = await readFile(path.join(ROOT, "test", file), "utf8");
    if (source.includes("chrome-launch.mjs")) browserFiles.push(file);
    if (
      /-postgres\.test\.(?:js|mjs|ts)$/.test(file) ||
      /disposable-postgres\.ts|postgres-database\.mjs|initdb/.test(source)
    )
      postgresFiles.push(file);
  }
  const { CARR_CI_UNIT_SUITES: selection, ...suiteEnv } = process.env;
  const failures: { file: string; output: string }[] = [];
  let template: Snapshot | undefined;
  let baselineTemplate: Snapshot | undefined;
  try {
    if (postgresFiles.length) {
      const capacity = spawnSync(process.env.CARR_CI_PYTHON || "python3", [path.join(ROOT, "../lib/pg_capacity.py")], {
        encoding: "utf8",
        timeout: 15000,
      });
      if (capacity.status !== 0)
        throw new Error(
          `PostgreSQL capacity preflight refused: ${capacity.stdout} ${capacity.stderr} ${capacity.error || ""}`,
        );
      const started = performance.now();
      const { acquireDisposablePostgres } = await import("./test/helpers/disposable-postgres.ts");
      baselineTemplate = await snapshot(
        await acquireDisposablePostgres({
          prefix: "mcp-snapshot-",
          template: true,
          baseline: true,
          pgCtl: undefined,
          sourceTemplate: undefined,
        }),
      );
      template = await snapshot(
        await acquireDisposablePostgres({
          prefix: "mcp-snapshot-",
          template: true,
          sourceTemplate: baselineTemplate,
          pgCtl: undefined,
        }),
      );
      console.log(`suite-timing: snapshot-template ${(performance.now() - started).toFixed(0)}ms`);
    }
    for (const batch of suiteBatches(files, browserFiles, postgresFiles)) {
      if (!batch.length) continue;
      let cursor = 0;
      const workers = Math.min(batch.length, Number(process.env.CARR_CI_NODE_JOBS || 4));
      if (!Number.isInteger(workers) || workers < 1) throw new Error("invalid CARR_CI_NODE_JOBS");
      await Promise.all(
        Array.from({ length: workers }, async () => {
          while (cursor < batch.length) {
            const file = batch[cursor++];
            if (file === undefined) break;
            let output = "";
            const started = performance.now();
            const code = await new Promise<number>((resolve, reject) => {
              const child = spawn(process.execPath, ["--test", "--test-reporter=tap", path.join(ROOT, "test", file)], {
                cwd: ROOT,
                stdio: ["ignore", "pipe", "pipe"],
                env: {
                  ...suiteEnv,
                  ...(template &&
                    baselineTemplate && {
                      CARR_TEST_PG_SNAPSHOT: template.snapshot,
                      CARR_TEST_PG_SNAPSHOT_KEY: template.key,
                      CARR_TEST_PG_SNAPSHOT_BASE: baselineTemplate.snapshot,
                      CARR_TEST_PG_SNAPSHOT_BASE_KEY: baselineTemplate.key,
                    }),
                },
              });
              for (const [stream, destination] of [
                [child.stdout, process.stdout],
                [child.stderr, process.stderr],
              ] as const) {
                stream.on("data", (chunk: Buffer) => {
                  output += chunk;
                  destination.write(chunk);
                });
              }
              child.once("error", reject);
              child.once("close", (exitCode) => resolve(exitCode ?? 1));
            });
            console.log(`suite-timing: ${file} ${(performance.now() - started).toFixed(0)}ms`);
            const summary = output
              .split("\n")
              .filter(
                (line) =>
                  /^(?:#|ℹ) (?:tests|pass|fail|cancelled|skipped|todo) \d+\s*$/.test(line) ||
                  /^(?:# )?CARR_TEST_FILE [01]\s*$/.test(line),
              )
              .join("\n");
            console.log(`suite-result: ${JSON.stringify({ file, code, summary })}`);
            if (code !== 0) {
              process.exitCode = 1;
              failures.push({ file, output });
            }
          }
        }),
      );
    }
  } finally {
    try {
      await template?.close();
    } finally {
      await baselineTemplate?.close();
    }
  }
  for (const { file, output } of failures) console.error(`FAILED SUITE ${file}\n${output}`);
  if (failures.length) console.error(`Failed suites: ${failures.map(({ file }) => file).join(", ")}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
