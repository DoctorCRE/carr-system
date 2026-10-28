import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { canonicalJson } from '../src/artifact-trust.js';
import { readSystemWorkCensus } from '../src/system-work-census.v5.js';
import { executeRegisteredTool } from '../src/tools.js';
import { SCAC_MUTATION_CURRENT_DB_GRANTS } from '../src/scac-mutation-registry.current.generated.js';

let fixture, admin, database, c, actor, connection;
before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
    assert.equal(url.search, '', 'fixture connection must not override its loopback host');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    const name = `census_inventory_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${name}" template template0`);
    database = name;
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'census-inventory-', pgCtl: `${bin}/pg_ctl` });
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
    connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  }
  c = new pg.Client(connection);
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname];
  if (ciDsn) await promisify(execFile)('psql', [connection.connectionString, ...restore], { maxBuffer: 2 * 1024 * 1024 });
  else await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
  const migrations = readdirSync(new URL('../../migrations/', import.meta.url))
    .filter(name => /^\d{4}_.*\.sql$/.test(name) && name < MIGRATION_NAME).sort();
  const dsn = connection.connectionString ?? `postgresql://carr_ci@localhost/carr_ci?host=${encodeURIComponent(fixture.root)}`;
  const python = new URL('../../.venv/bin/python', import.meta.url).pathname;
  await promisify(execFile)(existsSync(python) ? python : 'python3',
    [new URL('../../tools/migrate.py', import.meta.url).pathname, '--apply', '--yes', '--through', migrations.at(-1)],
    { env: { ...process.env, DATABASE_URL: dsn }, maxBuffer: 4 * 1024 * 1024 });
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

const MIGRATION_NAME = '1052_system_work_census_inventory.sql';
const MIGRATION = readFileSync(new URL(`../../migrations/${MIGRATION_NAME}`, import.meta.url), 'utf8');
const OBSERVED_AT = '2026-10-28T12:00:00.000Z';
const hex = char => char.repeat(64);
const seedBoard = (boardId, version = 1, json = { items: [] }) => c.query(
  `insert into board_snapshot (organization_tenant_id,sponsoring_human_slug,board_id,version,snapshot_json,updated_by_actor_id)
   values ('carr-internal','joe',$1,$2,$3::jsonb,$4)`, [boardId, version, JSON.stringify(json), actor.id]);

async function command(name, args = {}, role = 'carr_writer') {
  await c.query('begin');
  try {
    await c.query(`set local role ${role}`);
    const result = await executeRegisteredTool(c, actor, name, args);
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}
const publish = (payload, idempotency_key = randomUUID()) => command('publish-census-inventory', { idempotency_key, payload });
const rows = async () => (await c.query('select organization_tenant_id,sponsoring_human_slug,payload from public.system_work_census_inventory')).rows;
const digestOf = payload => createHash('sha256').update(canonicalJson(payload)).digest('hex');
const item = id => ({ id, kind: 'pull_request', title: 'Synthetic', completed: false, cancelled: false,
  opened_at: '2026-10-01T00:00:00Z', last_activity_at: '2026-10-02T00:00:00Z', identity: {}, link: null });

test('the migration removes every hash page, leaves real boards, and registers the writer grants', async () => {
  await seedBoard('carr-v5', 7, { schema: 'carr-progress-board.v4', tasks: {} });
  await seedBoard('needs-joe-local', 3);
  await seedBoard(`carr-v5-external-${hex('a')}`);
  await seedBoard(`carr-v5-snapshot-${hex('b')}`);
  await seedBoard('carr-v5-external-not-a-digest');
  await c.query(MIGRATION);
  const left = (await c.query('select board_id from board_snapshot order by board_id')).rows.map(r => r.board_id);
  assert.deepEqual(left, ['carr-v5', 'carr-v5-external-not-a-digest', 'needs-joe-local']);
  assert.deepEqual(await rows(), []);
  const grants = (await c.query('select ops.scac_current_grant_contract() as contract')).rows[0].contract.relation_dml
    .filter(g => g.relation === 'public.system_work_census_inventory').map(g => g.privilege).sort();
  assert.deepEqual(grants, ['insert', 'update']);
  assert.equal((await c.query('select ops.scac_mutation_catalog_v118_current() as valid')).rows[0].valid, true);
  assert.deepEqual((await c.query('select ops.scac_current_grant_contract() as contract')).rows[0].contract,
    SCAC_MUTATION_CURRENT_DB_GRANTS);
  if (process.env.CENSUS_INVENTORY_CONTRACT_OUT)
    writeFileSync(process.env.CENSUS_INVENTORY_CONTRACT_OUT, JSON.stringify((await c.query('select ops.scac_current_grant_contract() as contract')).rows[0].contract));
});

test('publishing replaces the one row whole, and a replay does not write again', async () => {
  const key = randomUUID();
  assert.equal((await publish({ items: [item('a'), item('b')] }, key)).item_count, 2);
  await publish({ items: [item('c')] });
  assert.deepEqual((await rows()).map(r => r.payload.items.map(i => i.id)), [['c']]);
  await publish({ items: [item('a'), item('b')] }, key);
  assert.deepEqual((await rows()).map(r => r.payload.items.map(i => i.id)), [['c']]);
});

test('refusals name the problem and store nothing', async () => {
  const before = JSON.stringify(await rows());
  for (const payload of [{}, { items: 'x' }, { items: [], text: 'x'.repeat(8_000_001) }])
    await assert.rejects(publish(payload), { message: 'census_inventory_invalid' });
  await assert.rejects(command('publish-census-inventory', { idempotency_key: randomUUID(), payload: { items: [] } }, 'carr_reader'));
  assert.equal(JSON.stringify(await rows()), before);
});

async function censusWith(manifest, now = OBSERVED_AT) {
  await c.query("delete from board_snapshot where board_id='carr-v5'");
  await seedBoard('carr-v5', 1, { external_inventory: manifest });
  await c.query('begin');
  try {
    await c.query('set local role carr_reader');
    return await readSystemWorkCensus({ client: c, actor, kinds: 'pull_request', now: () => new Date(now) });
  } finally { await c.query('rollback'); }
}

test('the census reads the inventory row when the manifest digest and count match', async () => {
  const payload = { items: [item('repo:pr:1')] };
  await publish(payload);
  const manifest = { schema: 'system-work-external.v3', observed_at: OBSERVED_AT, complete: true, completed_pr_history: true,
    item_count: 1, digest: digestOf(payload) };
  const result = await censusWith(manifest);
  assert.equal(result.items.length, 1);
  assert.equal(result.coverage.find(x => x.kind === 'pull_request').state, 'complete');
});

test('a manifest the row does not match, or a leftover v2 manifest, is never complete', async () => {
  const payload = { items: [item('repo:pr:1')] };
  await publish(payload);
  const base = { observed_at: OBSERVED_AT, complete: true, completed_pr_history: true, item_count: 1 };
  for (const manifest of [{ ...base, schema: 'system-work-external.v3', digest: hex('0') },
    { ...base, schema: 'system-work-external.v3', digest: digestOf(payload), item_count: 2 },
    { ...base, schema: 'system-work-external.v2', pages: [{ board_id: `carr-v5-external-${hex('a')}`, version: 1, count: 1 }] }]) {
    const result = await censusWith(manifest);
    assert.equal(result.census_complete, false);
    assert.notEqual(result.coverage.find(x => x.kind === 'pull_request').state, 'complete');
  }
});
