// Run MCP test suites in isolated batches.
import { readdir, readFile } from "node:fs/promises";
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
  const files = await readdir(path.join(ROOT, "test"));
  const browserFiles = [];
  const postgresFiles = [];
  for (const file of files.filter(file => /\.test\.(?:js|mjs)$/.test(file))) {
    const source = await readFile(path.join(ROOT, "test", file), "utf8");
    if (source.includes("chrome-launch.mjs")) browserFiles.push(file);
    if (/disposable-postgres\.mjs|postgres-database\.mjs|initdb/.test(source)) postgresFiles.push(file);
  }
  const failures = [];
  for (const batch of suiteBatches(files, browserFiles, postgresFiles)) {
    if (!batch.length) continue;
    let cursor = 0;
    const workers = Math.min(batch.length, Number(process.env.CARR_CI_NODE_JOBS || 4));
    if (!Number.isInteger(workers) || workers < 1) throw new Error('invalid CARR_CI_NODE_JOBS');
    await Promise.all(Array.from({ length: workers }, async () => {
      while (cursor < batch.length) {
        const file = batch[cursor++];
        let output = '';
        const code = await new Promise((resolve, reject) => {
          const child = spawn(process.execPath, ['--test', path.join(ROOT, 'test', file)],
            { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
          for (const [stream, destination] of [[child.stdout, process.stdout], [child.stderr, process.stderr]]) {
            stream.on('data', chunk => { output += chunk; destination.write(chunk); });
          }
          child.once('error', reject);
          child.once('close', (exitCode) => resolve(exitCode ?? 1));
        });
        if (code !== 0) { process.exitCode = 1; failures.push({ file, output }); }
      }
    }));
  }
  for (const { file, output } of failures) console.error(`FAILED SUITE ${file}\n${output}`);
  if (failures.length) console.error(`Failed suites: ${failures.map(({ file }) => file).join(", ")}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
