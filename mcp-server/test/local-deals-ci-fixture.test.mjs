import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createServer } from 'node:net';
import { promisify } from 'node:util';
import path from 'node:path';
import pg from 'pg';

let bin;
for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
  try {
    const candidate = execFileSync(config, ['--bindir'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    if (existsSync(path.join(candidate, 'postgres'))) { bin = candidate; break; }
  } catch { /* Try the supported installations. */ }
}

test('Local Deals CI fixture provisions missing roles on a fresh cluster and preserves existing roles', {
  skip: !bin && 'PostgreSQL unavailable',
}, async () => {
  const releaseBudget = await acquirePostgresFixtureGroup();
  let postgresFixture, dir;
  let admin;
  try {
    const socket = createServer();
    await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
    const port = socket.address().port;
    await new Promise(resolve => socket.close(resolve));
    postgresFixture = await acquireDisposablePostgres({ prefix: 'local-deals-ci-', pgCtl: path.join(bin, 'pg_ctl'), dataName: '.' });
    dir = postgresFixture.root;
    await postgresFixture.run(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await postgresFixture.run(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h 127.0.0.1 -p ${port}`, '-w', 'start']);
    const dsn = `postgresql://fixture@127.0.0.1:${port}/postgres`;
    admin = new pg.Client({ connectionString: dsn });
    await admin.connect();
    const roles = () => admin.query("select rolname, rolcanlogin, rolcreatedb, rolsuper from pg_roles where rolname in ('carr_reader','carr_writer') order by rolname");
    assert.deepEqual((await roles()).rows, [], 'unit CI starts without application roles');
    const env = { ...process.env, CARR_CI_DATABASE_URL: dsn };
    delete env.NODE_TEST_CONTEXT;
    const run = async () => {
      try {
        await promisify(execFile)(process.execPath, ['--test', new URL('./local-deals-store.test.mjs', import.meta.url).pathname], {
          env, maxBuffer: 1024 * 1024,
        });
      } catch (error) {
        assert.fail(`${error.message}\n${error.stdout}\n${error.stderr}`);
      }
      assert.equal((await admin.query("select to_regclass('public.deal') existing")).rows[0].existing, null, 'fixture never writes the shared database');
      assert.deepEqual((await admin.query("select datname from pg_database where datname like 'local_deals_%'")).rows, [], 'isolated fixture database is removed');
    };
    await run();
    assert.deepEqual((await roles()).rows.map(row => row.rolname), ['carr_reader', 'carr_writer']);
    await admin.query('alter role carr_reader createdb');
    const before = (await roles()).rows;
    await run();
    assert.deepEqual((await roles()).rows, before, 'existing role attributes are unchanged');
  } finally {
    try {
      try { if (admin) await admin.end(); }
      finally { await postgresFixture?.close(); }
    } finally { await releaseBudget(); }
  }
});
