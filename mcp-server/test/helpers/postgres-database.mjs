import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createServer } from 'node:net';
import path from 'node:path';
import pg from 'pg';
import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './disposable-postgres.mjs';

export async function postgresDatabase(prefix) {
  let bin;
  for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
    try {
      const candidate = execFileSync(config, ['--bindir'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
      if (existsSync(path.join(candidate, 'postgres'))) { bin = candidate; break; }
    } catch { /* Try the supported installations. */ }
  }
  if (!bin) throw new Error('Routing and runtime proofs require local PostgreSQL');
  const release = await acquirePostgresFixtureGroup();
  let fixture, client;
  const close = async () => {
    try { await client?.end(); }
    finally { try { await fixture?.close(); } finally { await release(); } }
  };
  try {
    const socket = createServer();
    await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
    const port = socket.address().port;
    await new Promise(resolve => socket.close(resolve));
    fixture = await acquireDisposablePostgres({ prefix: prefix, pgCtl: path.join(bin, 'pg_ctl') });
    const data = path.join(fixture.root, 'data');
    await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'),
      '-o', `-k ${fixture.root} -h 127.0.0.1 -p ${port}`, '-w', 'start']);
    client = new pg.Client({ connectionString: `postgresql://fixture@127.0.0.1:${port}/postgres` });
    await client.connect();
    return { client, close };
  } catch (error) { await close(); throw error; }
}
