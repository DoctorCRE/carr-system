// Verify snapshot fixtures use isolated database clones on the provided CI service.
import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import './helpers/postgres-connection-observer.mjs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, writeFile, rm, readFile, readdir } from 'node:fs/promises';
import { unlinkSync, existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
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

const observer = new URL('./helpers/postgres-connection-observer.mjs', import.meta.url).href;
async function childSuite(file, directory, env, probe = {}) {
  const record = path.join(directory, randomUUID() + '.jsonl');
  const childEnv = { ...env, ...probe, WR623_PG_RECORD: record,
    NODE_OPTIONS: `${env.NODE_OPTIONS || ''} --import=${observer}` };
  delete childEnv.NODE_TEST_CONTEXT;
  let result, error;
  try {
    result = await promisify(execFile)(process.execPath, ['--test', fileURLToPath(new URL(file, import.meta.url))],
      { env: childEnv, timeout: 240000, maxBuffer: 8 * 1024 * 1024 });
  } catch (caught) { error = caught; }
  const connections = (await readFile(record, 'utf8').catch(caught => {
    if (caught.code === 'ENOENT') return ''; throw caught;
  })).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
  return { result, error, connections };
}
function assertChildPassed(child, file) {
  if (child.error) assert.fail(`${file}\n${child.error.stdout}\n${child.error.stderr}`);
  assert.match(child.result.stdout, /pass/, `${file} must execute its tests`);
  assert.ok(child.connections.length, `${file} must expose its real SQL connections`);
}
const clusterState = async client => ({
  roles: (await client.query('select rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,rolconfig,rolvaliduntil from pg_roles order by rolname')).rows,
  databases: (await client.query('select datname,datdba,datacl from pg_database order by datname')).rows,
  memberships: (await client.query('select roleid,member,grantor,admin_option,inherit_option,set_option from pg_auth_members order by roleid,member,grantor')).rows,
});

test('all direct snapshot callers use distinct databases on their actual CI-service connections', async () => {
  const suites = [];
  for (const file of await readdir(new URL('./', import.meta.url))) {
    if (file === 'snapshot-service-postgres.test.mjs' || !/\.test\.(?:js|mjs)$/.test(file)) continue;
    const source = await readFile(new URL(file, import.meta.url), 'utf8');
    const match = source.match(/acquireDisposablePostgres\(\{\s*prefix:\s*'([^']+)'[^}]*\bsnapshot:\s*true/);
    if (match) suites.push({ file, prefix: match[1] });
  }
  assert.ok(suites.length >= 17, 'retain the original snapshot callers in the connection proof');
  const service = await postgresDatabase('callers-ci-');
  const directory = await mkdtemp(path.join(tmpdir(), 'snapshot-callers-'));
  const savedUrl = process.env.CARR_CI_DATABASE_URL;
  let baseline, current, before;
  try {
    await service.client.query('create database carr_ci');
    before = await clusterState(service.client);
    process.env.CARR_CI_DATABASE_URL = `postgresql://fixture@127.0.0.1:${service.client.connectionParameters.port}/carr_ci`;
    baseline = await acquireDisposablePostgres({ prefix: 'mcp-snapshot-', template: true, baseline: true });
    current = await acquireDisposablePostgres({ prefix: 'mcp-snapshot-', template: true, sourceTemplate: baseline });
    const env = { ...process.env, CARR_TEST_PG_SNAPSHOT: current.snapshot, CARR_TEST_PG_SNAPSHOT_KEY: current.key,
      CARR_TEST_PG_SNAPSHOT_BASE: baseline.snapshot, CARR_TEST_PG_SNAPSHOT_BASE_KEY: baseline.key };
    const databases = new Set();
    for (const { file, prefix } of suites.sort((a, b) => a.file.localeCompare(b.file))) {
      const child = await childSuite(file, directory, env, { WR623_EXPECT_PREFIX: prefix });
      assertChildPassed(child, file);
      const names = new Set(child.connections.map(connection => connection.database));
      assert.equal(names.size, 1, `${file} must keep all clients in its own database`);
      const [name] = names;
      assert.ok(!databases.has(name), `${file} reused another suite's database`);
      databases.add(name);
      console.log(`snapshot-connection-proof: ${file} ${name} ${child.connections.length} clients`);
    }
    assert.equal(databases.size, suites.length);
    const bypass = await childSuite('deal-items-postgres.test.mjs', directory, env,
      { WR623_EXPECT_PREFIX: 'deal-items-', WR623_REDIRECT_TO_SHARED: '1' });
    assert.equal(bypass.error?.code, 1, 'bypassing the fixture must fail the actual-connection guard');
    assert.match(bypass.error.stdout + bypass.error.stderr, /child suite connected to the shared service database/);
    assert.equal(bypass.connections[0].database, 'carr_ci');
  } finally {
    try { await current?.close(); } finally {
      try { await baseline?.close(); } finally {
        if (savedUrl === undefined) delete process.env.CARR_CI_DATABASE_URL; else process.env.CARR_CI_DATABASE_URL = savedUrl;
        try { if (before) assert.deepEqual(await clusterState(service.client), before, 'child suites must leave cluster roles, settings, grants and database owners/ACLs unchanged'); }
        finally { try { await service.close(); } finally { await rm(directory, { recursive: true, force: true }); } }
      }
    }
  }
});

test('the real child suite receives a branch migration, and rejecting stale pickup fails its SQL assertion', async () => {
  const migration = new URL(`../../migrations/9999_wr623_${randomUUID().replaceAll('-', '')}.sql`, import.meta.url);
  const directory = await mkdtemp(path.join(tmpdir(), 'snapshot-migration-'));
  const removeScratch = () => { try { unlinkSync(migration); } catch (error) { if (error.code !== 'ENOENT') throw error; } };
  let template;
  try {
    const env = { ...process.env };
    if (!env.CARR_TEST_PG_SNAPSHOT_KEY) {
      template = await acquireDisposablePostgres({ prefix: 'mcp-snapshot-', template: true });
      env.CARR_TEST_PG_SNAPSHOT = template.snapshot;
      env.CARR_TEST_PG_SNAPSHOT_KEY = template.key;
    }
    await writeFile(migration, 'ALTER TABLE public.actor ADD COLUMN wr623_snapshot_probe text;\n', { flag: 'wx' });
    process.on('exit', removeScratch);
    const frozen = fileURLToPath(new URL('../../.venv/bin/python', import.meta.url));
    const { stdout } = await promisify(execFile)(existsSync(frozen) ? frozen : 'python3',
      ['-c', 'from lib.postgres_snapshot_fixture import snapshot_key; print(snapshot_key("db/schema.sql"))'],
      { cwd: fileURLToPath(new URL('../../', import.meta.url)) });
    const stale = await childSuite('deal-items-postgres.test.mjs', directory, env,
      { WR623_PROBE_COLUMN: '1', WR623_STALE_SNAPSHOT_KEY: stdout.trim() });
    assert.equal(stale.error?.code, 1, 'disabled migration pickup must fail the child suite');
    assert.match(stale.error.stdout + stale.error.stderr, /scratch migration missing from child suite database/);
    assert.ok(stale.connections.length);
    assert.ok(stale.connections.every(connection => connection.column === false));
    const current = await childSuite('deal-items-postgres.test.mjs', directory, env, { WR623_PROBE_COLUMN: '1' });
    assertChildPassed(current, 'deal-items-postgres.test.mjs');
    assert.ok(current.connections.every(connection => connection.column === true), 'the child suite itself must query the new column');
    console.log(`scratch-migration-proof: stale child rejected; migrated child ${current.connections[0].database} verified`);
  } finally {
    await rm(migration, { force: true });
    process.off('exit', removeScratch);
    try { await template?.close(); } finally { await rm(directory, { recursive: true, force: true }); }
  }
});
