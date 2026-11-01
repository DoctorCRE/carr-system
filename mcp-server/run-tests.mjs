// Run MCP test suites in isolated batches.
import { readFile } from "node:fs/promises";
import { discoverSuites } from './suite-discovery.mjs';
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const LAUNCH_REGRESSIONS = "chrome-launch.test.mjs";

export function suiteBatches(files, browserFiles = [], postgresFiles = []) {
  const suites = files.filter((file) => /\.test\.(?:js|mjs)$/.test(file)).sort();
  const browsers = suites.filter((file) => file !== LAUNCH_REGRESSIONS
    && (browserFiles.includes(file) || /-browser\.test\.(?:js|mjs)$/.test(file)));
  const postgres = suites.filter((file) => (postgresFiles.includes(file) || /-postgres\.test\.(?:js|mjs)$/.test(file)) && !browsers.includes(file));
  return [suites.filter((file) => file === LAUNCH_REGRESSIONS),
    ...browsers.map((file) => [file]),
    ...postgres.map((file) => [file]),
    suites.filter((file) => ![LAUNCH_REGRESSIONS, ...browsers, ...postgres].includes(file))];
}

async function main() {
  // Chrome's cold first launch must not compete with the bulk MCP test pool.
  // Keep the browser shim intact (its inventory test enforces all imports),
  // Run the timing-sensitive launch fixtures alone too, then run the rest
  // Private PostgreSQL clusters also run alone: parallel postmasters can
  // exhaust the host's shared-memory IDs even with unique database paths.
  // Keep the serial batches; the bulk pool gives each suite its own result.
  const inventory = JSON.parse(await readFile(path.join(ROOT, 'suite-inventory.json'), 'utf8'));
  const discovered = await discoverSuites(path.join(ROOT, 'test'), inventory);
  const selected = process.env.CARR_CI_UNIT_SUITES === undefined
    ? undefined : JSON.parse(process.env.CARR_CI_UNIT_SUITES);
  if (selected !== undefined && (!Array.isArray(selected) || !selected.length
      || new Set(selected).size !== selected.length || selected.some(file => !discovered.includes(file))))
    throw new Error('unit shard has empty, duplicate or missing suites');
  const files = selected || discovered;
  const browserFiles = [];
  const postgresFiles = [];
  for (const file of files.filter(file => /\.test\.(?:js|mjs)$/.test(file))) {
    const source = await readFile(path.join(ROOT, "test", file), "utf8");
    if (source.includes("chrome-launch.mjs")) browserFiles.push(file);
    if (/disposable-postgres\.mjs|postgres-database\.mjs|initdb/.test(source)) postgresFiles.push(file);
  }
  const { CARR_CI_UNIT_SUITES: selection, ...suiteEnv } = process.env;
  const failures = [];
  let template;
  let baselineTemplate;
  try {
    if (postgresFiles.length) {
      const started = performance.now();
      const { acquireDisposablePostgres } = await import('./test/helpers/disposable-postgres.mjs');
      baselineTemplate = await acquireDisposablePostgres({ prefix: 'mcp-snapshot-', template: true, baseline: true });
      template = await acquireDisposablePostgres({ prefix: 'mcp-snapshot-', template: true, sourceTemplate: baselineTemplate });
      console.log(`suite-timing: snapshot-template ${(performance.now() - started).toFixed(0)}ms`);
    }
    for (const batch of suiteBatches(files, browserFiles, postgresFiles)) {
      if (!batch.length) continue;
      let cursor = 0;
      const workers = Math.min(batch.length, Number(process.env.CARR_CI_NODE_JOBS || 4));
      if (!Number.isInteger(workers) || workers < 1) throw new Error('invalid CARR_CI_NODE_JOBS');
      await Promise.all(Array.from({ length: workers }, async () => {
        while (cursor < batch.length) {
          const file = batch[cursor++];
          let output = '';
          const started = performance.now();
          const code = await new Promise((resolve, reject) => {
            const child = spawn(process.execPath, ['--test', '--test-reporter=tap', path.join(ROOT, 'test', file)],
              { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'],
                env: { ...suiteEnv, ...(template && {
                  CARR_TEST_PG_SNAPSHOT: template.snapshot, CARR_TEST_PG_SNAPSHOT_KEY: template.key,
                  CARR_TEST_PG_SNAPSHOT_BASE: baselineTemplate.snapshot, CARR_TEST_PG_SNAPSHOT_BASE_KEY: baselineTemplate.key,
                }) } });
            for (const [stream, destination] of [[child.stdout, process.stdout], [child.stderr, process.stderr]]) {
              stream.on('data', chunk => { output += chunk; destination.write(chunk); });
            }
            child.once('error', reject);
            child.once('close', (exitCode) => resolve(exitCode ?? 1));
          });
          console.log(`suite-timing: ${file} ${(performance.now() - started).toFixed(0)}ms`);
          const summary = output.split('\n').filter(line => /^(?:#|ℹ) (?:tests|pass|fail|cancelled|skipped|todo) \d+\s*$/.test(line)
            || /^(?:# )?CARR_TEST_FILE [01]\s*$/.test(line)).join('\n');
          console.log(`suite-result: ${JSON.stringify({ file, code, summary })}`);
          if (code !== 0) { process.exitCode = 1; failures.push({ file, output }); }
        }
      }));
    }
  } finally { try { await template?.close(); } finally { await baselineTemplate?.close(); } }
  for (const { file, output } of failures) console.error(`FAILED SUITE ${file}\n${output}`);
  if (failures.length) console.error(`Failed suites: ${failures.map(({ file }) => file).join(", ")}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
