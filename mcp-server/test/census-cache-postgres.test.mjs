// Exercise queue commands through the real transaction envelope and PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { readSystemWorkCensus } from '../src/system-work-census.v5.js';
import { executeRegisteredTool } from '../src/tools.js';

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
    const name = `census_cache_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${name}" template template0`);
    database = name;
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'census-cache-', pgCtl: `${bin}/pg_ctl` });
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
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

const MIGRATION = readFileSync(new URL('../../migrations/1034_system_work_census_cache.sql', import.meta.url), 'utf8');
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
const publish = (kind, digest, payload, idempotency_key = randomUUID()) =>
  command('publish-census-cache-page', { idempotency_key, kind, digest, payload });
const cacheRows = async () => (await c.query('select kind,digest,payload from public.system_work_census_cache order by kind,digest')).rows;

test('the migration moves only hash-named version 1 pages and leaves every real board', async () => {
  // The schema snapshot may already carry the table once it is refreshed; start from the pre-migration shape either way.
  await c.query('drop table if exists public.system_work_census_cache');
  await seedBoard('carr-v5', 7, { schema: 'carr-progress-board.v4', tasks: {} });
  await seedBoard('needs-joe-local', 3);
  await seedBoard(`carr-v5-external-${hex('a')}`, 1, { items: [{ id: 'a' }] });
  await seedBoard(`carr-v5-snapshot-${hex('b')}`, 1, { schema: 'carr-progress-board-page.v1', content: 'x' });
  await seedBoard(`carr-v5-external-${hex('c')}`, 2, { items: [] }); // a version other than 1 is not an immutable cache page
  await seedBoard('carr-v5-external-not-a-digest', 1);
  await c.query("update board_snapshot set updated_at = now() - interval '30 days' where board_id like 'carr-v5-%'");
  await c.query("update board_snapshot set snapshot_json = $1::jsonb where board_id = 'carr-v5'",
    [JSON.stringify({ external_inventory: { pages: [{ board_id: `carr-v5-external-${hex('a')}`, version: 1, count: 1 }] } })]);
  await c.query(MIGRATION);
  assert.deepEqual((await cacheRows()).map(r => [r.kind, r.digest]), [['external', hex('a')], ['snapshot', hex('b')]]);
  assert.deepEqual((await cacheRows())[0].payload, { items: [{ id: 'a' }] });
  const left = (await c.query('select board_id from board_snapshot order by board_id')).rows.map(r => r.board_id);
  assert.deepEqual(left, ['carr-v5', `carr-v5-external-${hex('c')}`, 'carr-v5-external-not-a-digest', 'needs-joe-local']);
  // The page the live manifest names starts fresh; the unreferenced one keeps its 30-day age and is first in line to trim.
  const ages = Object.fromEntries((await c.query("select digest, touched_at > now() - interval '1 minute' as fresh from public.system_work_census_cache")).rows.map(r => [r.digest, r.fresh]));
  assert.deepEqual(ages, { [hex('a')]: true, [hex('b')]: false });
});

test('a page is stored once per address, touched on repeat, and a different payload under one digest is refused', async () => {
  const digest = hex('d');
  const first = await publish('external', digest, { items: [{ id: 1 }] });
  assert.deepEqual([first.ok, first.kind, first.digest], [true, 'external', digest]);
  await c.query("update public.system_work_census_cache set touched_at = now() - interval '5 days' where digest = $1", [digest]);
  await publish('external', digest, { items: [{ id: 1 }] });
  assert.equal((await c.query("select count(*)::int n from public.system_work_census_cache where digest=$1 and touched_at > now() - interval '1 minute'", [digest])).rows[0].n, 1);
  await assert.rejects(publish('external', digest, { items: [{ id: 2 }] }), { message: 'census_cache_digest_conflict' });
  await publish('snapshot', digest, { schema: 'carr-progress-board-page.v1', content: 'y' }); // kind is part of the address
  assert.equal((await c.query('select count(*)::int n from public.system_work_census_cache where digest=$1', [digest])).rows[0].n, 2);
});

test('replaying one idempotency key does not write again', async () => {
  const key = randomUUID(), digest = hex('e');
  await publish('external', digest, { items: [] }, key);
  await c.query("update public.system_work_census_cache set touched_at = now() - interval '5 days' where digest = $1", [digest]);
  await publish('external', digest, { items: [] }, key);
  assert.equal((await c.query("select count(*)::int n from public.system_work_census_cache where digest=$1 and touched_at < now() - interval '4 days'", [digest])).rows[0].n, 1);
});

test('a write trims pages untouched for 14 days and keeps newer ones', async () => {
  const [old, young] = [hex('1'), hex('2')];
  await publish('external', old, { items: [] });
  await publish('external', young, { items: [] });
  await c.query("update public.system_work_census_cache set touched_at = now() - interval '15 days' where digest = $1", [old]);
  await c.query("update public.system_work_census_cache set touched_at = now() - interval '13 days' where digest = $1", [young]);
  const result = await publish('external', hex('3'), { items: [] });
  assert.ok(result.trimmed >= 1);
  const left = (await cacheRows()).map(r => r.digest);
  assert.equal(left.includes(old), false);
  assert.equal(left.includes(young), true);
});

test('trimming is bounded per write', async () => {
  await c.query('truncate public.system_work_census_cache');
  await c.query(`insert into public.system_work_census_cache (organization_tenant_id,sponsoring_human_slug,kind,digest,payload,touched_at)
    select 'carr-internal','joe','external',lpad(to_hex(g),64,'0'),'{}'::jsonb,now() - interval '20 days' from generate_series(1,620) g`);
  assert.equal((await publish('external', hex('f'), { items: [] })).trimmed, 500);
  assert.equal((await c.query('select count(*)::int n from public.system_work_census_cache')).rows[0].n, 121);
});

test('refusals name the problem and store nothing', async () => {
  const before = (await cacheRows()).length;
  for (const [kind, digest, payload, error] of [
    ['other', hex('a'), {}, 'value_not_in_declared_vocabulary'], ['external', 'ABC', {}, 'census_cache_page_invalid'],
    ['external', hex('a'), [], 'census_cache_page_invalid'], ['external', hex('a'), { text: 'x'.repeat(262144) }, 'census_cache_page_invalid']])
    await assert.rejects(publish(kind, digest, payload), { message: error });
  assert.equal((await cacheRows()).length, before);
});

test('read returns the stored page and refuses a missing one; a read-only role cannot write', async () => {
  const digest = hex('9');
  await publish('snapshot', digest, { schema: 'carr-progress-board-page.v1', content: 'z' });
  assert.deepEqual((await command('read-census-cache-page', { kind: 'snapshot', digest }, 'carr_reader')).payload,
    { schema: 'carr-progress-board-page.v1', content: 'z' });
  await assert.rejects(command('read-census-cache-page', { kind: 'external', digest: hex('8') }, 'carr_reader'), { message: 'census_cache_page_not_found' });
  await assert.rejects(publish('external', hex('7'), {}, randomUUID()).then(() =>
    command('publish-census-cache-page', { idempotency_key: randomUUID(), kind: 'external', digest: hex('6'), payload: {} }, 'carr_reader')));
  assert.equal((await cacheRows()).some(r => r.digest === hex('6')), false);
});

test('the census reads the manifest pages from the cache table end to end', async () => {
  await c.query('truncate public.system_work_census_cache');
  const digest = hex('4');
  const items = [{ id: 'repo:pr:1', kind: 'pull_request', title: 'Synthetic', completed: false, cancelled: false, opened_at: '2026-10-01T00:00:00Z',
    last_activity_at: '2026-10-02T00:00:00Z', identity: {}, link: null }];
  await publish('external', digest, { items });
  const manifest = { schema: 'system-work-external.v2', observed_at: OBSERVED_AT, complete: true, completed_pr_history: true,
    item_count: 1, pages: [{ board_id: `carr-v5-external-${digest}`, version: 1, count: 1 }] };
  await c.query("delete from board_snapshot where board_id='carr-v5'");
  await seedBoard('carr-v5', 1, { external_inventory: manifest });
  await c.query('begin');
  try {
    await c.query('set local role carr_reader');
    const result = await readSystemWorkCensus({ client: c, actor, kinds: 'pull_request', now: () => new Date(OBSERVED_AT) });
    assert.equal(result.items.length, 1);
    assert.equal(result.coverage.find(x => x.kind === 'pull_request').state, 'complete');
  } finally { await c.query('rollback'); }
});
