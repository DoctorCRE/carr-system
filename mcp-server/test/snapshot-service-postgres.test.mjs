// Verify snapshot fixtures use isolated database clones on the provided CI service.
import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';

test('CI-service snapshots clone named databases on the provided service and clean up role state', async () => {
  const service = await postgresDatabase('snap-ci-');
  const savedUrl = process.env.CARR_CI_DATABASE_URL;
  const savedKey = process.env.CARR_TEST_PG_SNAPSHOT_KEY;
  const savedSnapshot = process.env.CARR_TEST_PG_SNAPSHOT;
  let template, first, second, client;
  const base = `postgresql://fixture@127.0.0.1:${service.client.connectionParameters.port}/postgres`;
  try {
    process.env.CARR_CI_DATABASE_URL = base;
    delete process.env.CARR_TEST_PG_SNAPSHOT;
    template = await acquireDisposablePostgres({ prefix: 'mcp-snapshot-', template: true });
    process.env.CARR_TEST_PG_SNAPSHOT = template.snapshot;
    process.env.CARR_TEST_PG_SNAPSHOT_KEY = template.key;
    await service.client.query('alter role carr_reader set search_path to public,pg_catalog; alter role carr_reader set default_transaction_read_only=on');
    const roles = () => service.client.query("select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls,rolconnlimit,rolconfig from pg_roles where rolname='carr_reader'");
    const before = (await roles()).rows;
    first = await acquireDisposablePostgres({ prefix: 'deal-items-', snapshot: true });
    await assert.rejects(first.run('unsupported'), /SQL connections only/);
    client = new pg.Client(first.connection);
    await client.connect();
    const current = (await client.query('select current_database() name,inet_server_port() port')).rows[0];
    assert.match(current.name, /^deal_items_[a-f0-9]{32}$/);
    assert.notEqual(current.name, 'postgres');
    assert.equal(current.port, service.client.connectionParameters.port);
    assert.ok((await client.query("select to_regclass('public.actor') relation")).rows[0].relation);
    assert.equal((await service.client.query("select to_regclass('public.actor') relation")).rows[0].relation, null);
    await client.query('create table snapshot_private_marker(id integer); alter role carr_reader login superuser createdb createrole noinherit replication bypassrls connection limit 3; alter role carr_reader set statement_timeout=9000; create role snapshot_suite_member; grant carr_reader to snapshot_suite_member');
    await client.end(); client = undefined;
    await first.close();
    assert.deepEqual((await roles()).rows, before);
    assert.deepEqual((await service.client.query("select rolname from pg_roles where rolname='snapshot_suite_member'")).rows, []);
    second = await acquireDisposablePostgres({ prefix: 'deal-items-', snapshot: true });
    client = new pg.Client(second.connection);
    await client.connect();
    assert.notEqual((await client.query('select current_database() name')).rows[0].name, current.name);
    assert.equal((await client.query("select to_regclass('snapshot_private_marker') relation")).rows[0].relation, null);
  } finally {
    const failures = [];
    for (const close of [() => client?.end(), () => second?.close(), () => first?.close(), () => template?.close()]) {
      try { await close(); } catch (error) { failures.push(error); }
    }
    if (savedUrl === undefined) delete process.env.CARR_CI_DATABASE_URL; else process.env.CARR_CI_DATABASE_URL = savedUrl;
    if (savedKey === undefined) delete process.env.CARR_TEST_PG_SNAPSHOT_KEY; else process.env.CARR_TEST_PG_SNAPSHOT_KEY = savedKey;
    if (savedSnapshot === undefined) delete process.env.CARR_TEST_PG_SNAPSHOT; else process.env.CARR_TEST_PG_SNAPSHOT = savedSnapshot;
    try {
      assert.deepEqual((await service.client.query("select datname from pg_database where datname ~ '^(deal_items|mcp_snapshot)_'")).rows, []);
      assert.equal(failures.length, 0, failures.map(error => error.message).join('\n'));
    } finally { await service.close(); }
  }
});

test('a branch migration invalidates the snapshot used by a real PostgreSQL suite', async () => {
  const { writeFile, rm } = await import('node:fs/promises');
  const { unlinkSync } = await import('node:fs');
  const { randomUUID } = await import('node:crypto');
  const { execFile } = await import('node:child_process');
  const { promisify } = await import('node:util');
  const { fileURLToPath } = await import('node:url');
  const migration = new URL(`../../migrations/9999_wr623_${randomUUID().replaceAll('-', '')}.sql`, import.meta.url);
  const saved = Object.fromEntries(['CARR_TEST_PG_SNAPSHOT', 'CARR_TEST_PG_SNAPSHOT_KEY'].map(key => [key, process.env[key]]));
  const removeScratch = () => { try { unlinkSync(migration); } catch (error) { if (error.code !== 'ENOENT') throw error; } };
  process.on('exit', removeScratch);
  let template, fixture, client;
  try {
    if (!process.env.CARR_TEST_PG_SNAPSHOT_KEY) {
      template = await acquireDisposablePostgres({ prefix: 'mcp-snapshot-', template: true });
      process.env.CARR_TEST_PG_SNAPSHOT = template.snapshot;
      process.env.CARR_TEST_PG_SNAPSHOT_KEY = template.key;
    }
    await writeFile(migration, 'ALTER TABLE public.actor ADD COLUMN wr623_snapshot_probe text;\n', { flag: 'wx' });
    const childEnv = { ...process.env };
    delete childEnv.NODE_TEST_CONTEXT;
    const result = await promisify(execFile)(process.execPath,
      ['--test', fileURLToPath(new URL('./deal-items-postgres.test.mjs', import.meta.url))],
      { env: childEnv, timeout: 240000, maxBuffer: 4 * 1024 * 1024 });
    assert.match(result.stdout, /pass/);
    fixture = await acquireDisposablePostgres({ prefix: 'migration-probe-', snapshot: true });
    assert.notEqual(fixture.key, process.env.CARR_TEST_PG_SNAPSHOT_KEY, 'the scratch migration must invalidate the prepared snapshot');
    client = new pg.Client(fixture.connection);
    await client.connect();
    assert.equal((await client.query("select count(*)::int count from information_schema.columns where table_schema='public' and table_name='actor' and column_name='wr623_snapshot_probe'")).rows[0].count, 1);
  } finally {
    // Remove the actual migration even if the child proof or SQL assertion fails.
    await rm(migration, { force: true });
    process.off('exit', removeScratch);
    try { await client?.end(); } finally {
      try { await fixture?.close(); } finally {
        try { await template?.close(); } finally {
          for (const [key, value] of Object.entries(saved)) {
            if (value === undefined) delete process.env[key]; else process.env[key] = value;
          }
        }
      }
    }
  }
});
