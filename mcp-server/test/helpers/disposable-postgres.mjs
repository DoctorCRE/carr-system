// Manage disposable PostgreSQL fixtures and their concurrency limits.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';

const supervisors = new Set();
const endSupervisors = () => { for (const child of supervisors) child.stdin.end(); };
const interrupt = () => process.exit(130);
const terminate = () => process.exit(143);
function register(child) {
  if (supervisors.size === 0) {
    process.on('exit', endSupervisors);
    process.on('SIGINT', interrupt);
    process.on('SIGTERM', terminate);
  }
  supervisors.add(child);
}
function unregister(child) {
  supervisors.delete(child);
  if (supervisors.size === 0) {
    process.off('exit', endSupervisors);
    process.off('SIGINT', interrupt);
    process.off('SIGTERM', terminate);
  }
}

async function supervisor(args, response) {
  const frozenPython = fileURLToPath(new URL('../../../.venv/bin/python', import.meta.url));
  const python = process.env.CARR_CI_PYTHON || (existsSync(frozenPython) ? frozenPython : 'python3');
  const child = spawn(python, ['-u', fileURLToPath(new URL('../../../lib/disposable_pg_fixture.py', import.meta.url)), ...args],
    { stdio: ['pipe', 'pipe', 'pipe'] });
  register(child);
  // An early supervisor failure can close stdin before teardown writes to it.
  child.stdin.on('error', () => {});
  const lines = createInterface({ input: child.stdout });
  let diagnostic = '';
  child.stderr.on('data', data => { diagnostic = (diagnostic + data).slice(-4000); });
  const completed = new Promise(resolve => {
    child.once('error', error => resolve({ error }));
    child.once('close', (code, signal) => resolve({ code, signal }));
  });
  let value;
  try {
    value = await Promise.race([
      new Promise((resolve, reject) => {
        lines.once('line', line => {
          try { resolve(response(line)); } catch (error) { reject(error); }
        });
      }),
      completed.then(result => { throw result.error ?? new Error(`PostgreSQL fixture supervisor failed: ${diagnostic}`); }),
    ]);
  } catch (error) {
    child.stdin.end();
    await completed;
    unregister(child);
    throw error;
  } finally {
    if (!args.includes('--supervise')) lines.close();
  }
  let closed;
  const run = async (command, args = []) => {
    if (closed) throw new Error('PostgreSQL fixture is closed');
    const reply = new Promise((resolve, reject) => {
      const receive = line => {
        try { resolve(JSON.parse(line)); } catch (error) { reject(error); }
      };
      lines.once('line', receive);
      completed.then(result => {
        lines.off('line', receive);
        reject(result.error ?? new Error(`PostgreSQL fixture supervisor exited during command: ${diagnostic}`));
      });
    });
    child.stdin.write(JSON.stringify({ command: [command, ...args] }) + '\n');
    const result = await reply;
    if (result.returncode !== 0) throw new Error(`PostgreSQL fixture command failed (${result.returncode}): ${result.stderr}`);
    return result.stdout;
  };
  return { value, run, close: () => closed ??= (async () => {
    child.stdin.end();
    const { code, signal, error } = await completed;
    unregister(child);
    lines.close();
    if (error) throw error;
    if (code !== 0) throw new Error(`PostgreSQL fixture supervisor exited (${signal ?? code}): ${diagnostic}`);
  })() };
}

// Use the same kernel-owned lease as Python proofs, across all worktrees.
export async function acquirePostgresFixtureGroup() {
  const lease = await supervisor([], line => {
    if (line !== 'ready') throw new Error('invalid PostgreSQL fixture lease response');
  });
  return lease.close;
}

// The supervisor owns the cluster before initdb/start and survives abrupt Node exit.
export async function acquireDisposablePostgres({ prefix, pgCtl, dataName = 'data', snapshot = false, template = false, baseline = false, sourceTemplate, service = false }) {
  const args = ['--supervise', '--prefix', prefix, '--data-name', dataName, '--directory', tmpdir()];
  if (service) {
    if (!process.env.CARR_CI_DATABASE_URL) throw new Error('a provided CI service URL is required');
    args.push('--service-database');
  }
  if (pgCtl) args.push('--pg-ctl', pgCtl);
  if (snapshot || template) {
    args.push(template ? '--snapshot-template' : '--snapshot');
    if (baseline) args.push('--snapshot-baseline');
    const source = sourceTemplate?.snapshot || (!template && process.env[baseline ? 'CARR_TEST_PG_SNAPSHOT_BASE' : 'CARR_TEST_PG_SNAPSHOT']);
    const key = sourceTemplate?.key || process.env[baseline ? 'CARR_TEST_PG_SNAPSHOT_BASE_KEY' : 'CARR_TEST_PG_SNAPSHOT_KEY'];
    if (source && key) args.push('--snapshot-copy', source, '--snapshot-key', key);
  }
  const fixture = await supervisor(args, line => {
    if (snapshot || template || service) return JSON.parse(line);
    if (!line.startsWith('/')) throw new Error('invalid PostgreSQL fixture root response');
    return line;
  });
  const metadata = typeof fixture.value === 'string' ? { root: fixture.value } : fixture.value;
  return { ...metadata, close: fixture.close, run: fixture.run };
}
