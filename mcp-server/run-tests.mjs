import { readdir, mkdir, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const BROWSER_SHIM = "workspace-command-center-browser.test.mjs";
const LAUNCH_REGRESSIONS = "chrome-launch.test.mjs";
const PRIVATE_POSTGRES_SUITES = new Set([
  "a02-rule-enforcement-postgres.test.mjs", "confirm-merge-schema.test.mjs",
  "journey-one-clock-input-store.v5.test.mjs", "journey-one-clock-store.v5.test.mjs",
  "lease-radar-postgres.test.mjs", "local-deals-ci-fixture.test.mjs", "local-deals-store.test.mjs", "whats-new-store.test.mjs",
]);

export function suiteBatches(files) {
  const suites = files.filter((file) => /\.test\.(?:js|mjs)$/.test(file)).sort();
  if (!suites.includes(BROWSER_SHIM)) throw new Error("Chrome browser shim is missing from the Node suites");
  const browsers = suites.filter((file) => file !== BROWSER_SHIM && /-browser\.test\.(?:js|mjs)$/.test(file));
  const postgres = suites.filter((file) => PRIVATE_POSTGRES_SUITES.has(file));
  return [[BROWSER_SHIM], suites.filter((file) => file === LAUNCH_REGRESSIONS),
    ...browsers.map((file) => [file]),
    ...postgres.map((file) => [file]),
    suites.filter((file) => ![BROWSER_SHIM, LAUNCH_REGRESSIONS, ...browsers, ...postgres].includes(file))];
}

async function main() {
  const repo = path.dirname(ROOT);
  const python = process.env.CARR_CI_PYTHON || 'python3';
  const runner = path.join(repo, 'ops/ci-quarantine.py');
  const logs = path.join(repo, 'out/ci-node', String(process.pid));
  await mkdir(logs, { recursive: true });
  const identity = path.join(logs, 'source-identity.json');
  const snapshot = await new Promise((resolve, reject) => {
    let output = '';
    const child = spawn(python, [runner, 'snapshot'], { cwd: repo, stdio: ['ignore', 'pipe', 'inherit'] });
    child.stdout.on('data', (chunk) => { output += chunk; });
    child.once('error', reject);
    child.once('close', (code) => code === 0 ? resolve(output) : reject(new Error('source identity unreadable')));
  });
  await writeFile(identity, snapshot);
  // Chrome's cold first launch must not compete with the bulk MCP test pool.
  // Keep the browser shim intact (its inventory test enforces all imports),
  // Run the timing-sensitive launch fixtures alone too, then run the rest
  // Private PostgreSQL clusters also run alone: parallel postmasters can
  // exhaust the host's shared-memory IDs even with unique database paths.
  // Keep the serial batches; the bulk pool gives each suite its own result.
  for (const batch of suiteBatches(await readdir(path.join(ROOT, "test")))) {
    if (!batch.length) continue;
    let cursor = 0;
    const workers = Math.min(batch.length, Number(process.env.CARR_CI_NODE_JOBS || 4));
    if (!Number.isInteger(workers) || workers < 1) throw new Error('invalid CARR_CI_NODE_JOBS');
    await Promise.all(Array.from({ length: workers }, async () => {
      while (cursor < batch.length) {
        const file = batch[cursor++];
        const code = await new Promise((resolve, reject) => {
          const child = spawn(python, [runner, 'run', '--test', `mcp-server/test/${file}`,
            '--identity-file', identity, '--log', path.join(logs, `${file}.log`), '--print-log', '--',
            process.execPath, '--test', path.join(ROOT, 'test', file)],
            { cwd: ROOT, stdio: 'inherit' });
          child.once('error', reject);
          child.once('close', (exitCode) => resolve(exitCode ?? 1));
        });
        if (code !== 0) process.exitCode = 1;
      }
    }));
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
