import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';

const bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin', '/usr/lib/postgresql/16/bin']
  .find(candidate => existsSync(path.join(candidate, 'initdb')));
const helper = new URL('./helpers/disposable-postgres.mjs', import.meta.url).href;
async function waitFor(predicate) {
  const deadline = Date.now() + 15000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, 'fixture teardown/startup deadline exceeded');
    await delay(25);
  }
}

for (const mode of ['SIGINT', 'SIGTERM', 'exit', 'SIGKILL-during-start']) {
  test(`Node ${mode} leaves no postmaster or temporary cluster`, { skip: !bin && 'PostgreSQL unavailable' }, async () => {
    const release = await acquirePostgresFixtureGroup();
    let child, root;
    try {
      child = spawn(process.execPath, ['--input-type=module', '-e', `
        import { acquireDisposablePostgres } from ${JSON.stringify(helper)};
        import path from 'node:path';
        const bin = ${JSON.stringify(bin)};
        const fixture = await acquireDisposablePostgres({prefix:'node-pg-life-',pgCtl:path.join(bin,'pg_ctl')});
        const data = path.join(fixture.root,'data');
        await fixture.run(path.join(bin,'initdb'), ['-D',data,'-U','fixture','--auth=trust','--no-locale']);
        const start = [path.join(bin,'pg_ctl'),'-D',data,'-l',path.join(fixture.root,'server.log'),'-o',\`-k \${fixture.root} -h ''\`,'-w','start'];
        if (${JSON.stringify(mode)} === 'SIGKILL-during-start') {
          console.log(fixture.root);
          await fixture.run('python3',['-c','import pathlib,subprocess,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(0.5); sys.exit(subprocess.run(sys.argv[2:]).returncode)',path.join(fixture.root,'launching'),...start]);
        } else {
          await fixture.run(start[0],start.slice(1));
          console.log(fixture.root);
        }
        if (${JSON.stringify(mode)} === 'exit') process.exit(0);
        setInterval(()=>{},1000);
      `], { stdio: ['ignore', 'pipe', 'pipe'] });
      let diagnostics = '';
      child.stderr.on('data', chunk => { diagnostics += chunk; });
      const exited = new Promise(resolve => child.once('exit', resolve));
      const lines = createInterface({ input: child.stdout });
      root = await Promise.race([
        new Promise(resolve => lines.once('line', resolve)),
        exited.then(() => { throw new Error(`fixture child exited before startup: ${diagnostics}`); }),
      ]);
      lines.close();
      if (mode === 'SIGKILL-during-start') {
        await waitFor(() => existsSync(path.join(root, 'launching')));
        child.kill('SIGKILL');
      } else if (mode === 'SIGTERM' || mode === 'SIGINT') {
        const pid = Number(readFileSync(path.join(root, 'data', 'postmaster.pid'), 'utf8').split('\n')[0]);
        child.kill(mode);
        await waitFor(() => {
          try { process.kill(pid, 0); return false; } catch (error) { return error.code === 'ESRCH'; }
        });
      }
      await exited;
      await waitFor(() => !existsSync(root));
      const processes = execFileSync('ps', ['-axo', 'command'], { encoding: 'utf8' }).split('\n');
      assert.equal(processes.some(command => command.includes('postgres -D ') && command.includes(path.join(root, 'data'))), false,
        'no postmaster may survive removal of its owned data directory');
    } finally {
      child?.kill('SIGTERM');
      if (root) await waitFor(() => !existsSync(root));
      await release();
    }
  });
}
