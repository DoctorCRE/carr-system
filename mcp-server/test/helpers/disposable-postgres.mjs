import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

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
  const child = spawn('python3', ['-u', fileURLToPath(new URL('../../../lib/disposable_pg_fixture.py', import.meta.url)), ...args],
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
export async function acquireDisposablePostgres({ prefix, pgCtl, dataName = 'data' }) {
  const fixture = await supervisor(['--supervise', '--prefix', prefix, '--pg-ctl', pgCtl, '--data-name', dataName], line => {
    if (!line.startsWith('/')) throw new Error('invalid PostgreSQL fixture root response');
    return line;
  });
  return { root: fixture.value, close: fixture.close, run: fixture.run };
}
