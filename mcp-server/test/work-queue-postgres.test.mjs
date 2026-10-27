// Exercise queue commands through the real transaction envelope and PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { publicError } from '../src/public-errors.ts';
import { queueCardId } from '../src/work-queue.js';
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
    const name = `work_queue_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${name}" template template0`);
    database = name;
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'work-queue-', pgCtl: `${bin}/pg_ctl` });
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
  if (!(await c.query("select 1 from schema_migrations where filename='1017_work_request_queue.sql'")).rows.length)
    await c.query(readFileSync(new URL('../../migrations/1017_work_request_queue.sql', import.meta.url), 'utf8'));
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

async function command(name, args = {}, client = c) {
  await client.query('begin');
  try {
    await client.query('set local role carr_writer');
    const result = await executeRegisteredTool(client, actor, name, args);
    await client.query('commit');
    return result;
  } catch (error) { await client.query('rollback'); throw error; }
}
async function request(ref = 'WR-000014') {
  await c.query(`insert into ops.work_request(ref,title,requester_actor,owner_actor,organization_tenant_id,program_key,program_ordinal)
    values ($1,'Synthetic queue work','joe','joe','carr-internal','carr-ai-engineering-suite-v1',$2) on conflict(ref) do nothing`, [ref, Number(ref.slice(3))]);
  return ref;
}
const job = (work_request, extra = {}) => ({ idempotency_key: randomUUID(), work_request,
  executor: 'codex', model: 'synthetic-model', effort: 'high', card_title: 'Queue work',
  description: 'Implement the synthetic queue behavior.', ...extra });

test('dispatch mints a stable ID under implicit S1 and retries without another job', async () => {
  const work_request = await request();
  const args = job(work_request);
  const first = await command('dispatch-job', args);
  assert.equal(first.card_id, 'WR14.S1.J1');
  assert.equal((await command('dispatch-job', args)).card_id, first.card_id);
  const queue = await command('work-queue', {});
  assert.deepEqual(queue.jobs.map(row => row.card_id), ['WR14.S1.J1']);
  assert.equal(queue.jobs[0].card_title, 'Queue work');
  assert.equal(queue.jobs[0].work_request_title, 'Synthetic queue work');
  assert.deepEqual([queue.jobs[0].stage, queue.jobs[0].stale, queue.jobs[0].prs], ['queued', false, []]);
  assert.deepEqual(queue.github.map(source => source.reason), ['no_github_reader', 'no_github_reader', 'no_github_reader']);
});

test('ordered plans are repeatable and allocate independent J numbers per slice', async () => {
  const work_request = await request('WR-000015');
  const args = { work_request, idempotency_key: randomUUID(), slices: ['First slice', 'Second slice'] };
  assert.deepEqual((await command('plan-work-request-slices', args)).slices.map(row => row.slice_id), ['WR15.S1', 'WR15.S2']);
  await command('plan-work-request-slices', { ...args, idempotency_key: randomUUID() });
  assert.equal((await command('dispatch-job', job(work_request, { slice: 2 }))).card_id, 'WR15.S2.J1');
  assert.equal((await command('dispatch-job', job(work_request))).card_id, 'WR15.S1.J1');
  await assert.rejects(command('plan-work-request-slices', { ...args, idempotency_key: randomUUID(), slices: ['Changed slice'] }), { message: 'slice_plan_conflict' });
});

test('priority changes move only the selected job and replay safely', async () => {
  const args = { work_request: 'WR-000015', slice: 1, job: 1, position: 1, idempotency_key: randomUUID() };
  await command('prioritize-job', args);
  await command('prioritize-job', args);
  assert.deepEqual((await command('work-queue')).jobs.map(row => row.card_id), ['WR15.S1.J1', 'WR14.S1.J1', 'WR15.S2.J1']);
  await command('prioritize-job', { ...args, position: 3, idempotency_key: randomUUID() });
  assert.deepEqual((await command('work-queue')).jobs.map(row => row.card_id), ['WR14.S1.J1', 'WR15.S2.J1', 'WR15.S1.J1']);
});

test('refusals preserve the queue and name the rejected operation', async () => {
  const before = (await command('work-queue')).jobs;
  const plan = { work_request: 'WR-000014', idempotency_key: randomUUID() };
  assert.equal((await command('plan-work-request-slices', plan)).slices[0].slice_id, 'WR14.S1');
  for (const [name, args, error] of [
    ['dispatch-job', job('WR-999999'), 'work_request_not_found'],
    ['dispatch-job', job('WR-000014', { slice: 9 }), 'slice_not_found'],
    ['dispatch-job', job('WR-000014', { slice: 2 ** 31 }), 'invalid_work_queue_fields'],
    ['prioritize-job', { ...plan, job: 2 ** 31, position: 1 }, 'invalid_work_queue_fields'],
    ['plan-work-request-slices', { ...plan, slices: null }, 'invalid_slice_plan'],
    ['dispatch-job', job('WR-000014', { card_title: 'Too many card words' }), 'invalid_card_title'],
    ['dispatch-job', job('WR-000014', { description: 'First sentence. Second sentence.' }), 'invalid_job_description'],
    ['prioritize-job', { ...plan, job: 99, position: 1 }, 'job_not_found'],
    ['prioritize-job', { ...plan, job: 1, position: 99 }, 'priority_out_of_range'],
  ]) await assert.rejects(command(name, { ...args, idempotency_key: randomUUID() }), failure => { assert.equal(failure.message, error); assert.equal(publicError(failure).error, error); return true; });
  const args = job('WR-000014');
  await command('dispatch-job', args);
  await assert.rejects(command('dispatch-job', { ...args, model: 'another-model' }), { message: 'key_reuse' });
  assert.deepEqual((await command('work-queue')).jobs.slice(0, before.length), before);
});

test('concurrent dispatch and same-key retries mint once, and rollback leaves no partial job', async () => {
  const work_request = await request('WR-000016');
  const clients = await Promise.all(Array.from({ length: 3 }, async () => { const client = new pg.Client(connection); await client.connect(); return client; }));
  try {
    const args = job(work_request);
    const results = await Promise.all([command('dispatch-job', args, clients[0]), command('dispatch-job', args, clients[1]), command('dispatch-job', job(work_request), clients[2])]);
    assert.equal(results[0].card_id, results[1].card_id);
    assert.deepEqual(new Set(results.map(row => row.card_id)), new Set(['WR16.S1.J1', 'WR16.S1.J2']));
    const retry = job(work_request);
    await c.query('begin');
    await executeRegisteredTool(c, actor, 'dispatch-job', retry);
    await c.query('rollback');
    assert.equal((await command('dispatch-job', retry)).card_id, 'WR16.S1.J3');
    assert.equal((await command('work-queue')).jobs.filter(row => row.work_request === work_request).length, 3);
  } finally { await Promise.all(clients.map(client => client.end())); }
});

test('the Worker and board share exact IDs without numeric precision loss', async () => {
  for (const ref of ['WR-000014', 'WR-9007199254740993']) {
    const expected = ref === 'WR-000014' ? 'WR14.S2.J1' : 'WR9007199254740993.S2.J1';
    assert.equal(queueCardId(ref, 2, 1), expected);
    const result = await promisify(execFile)('python3', ['-c', 'from lib.progress_hierarchy import card_address; import sys; print(card_address(sys.argv[1],2,1)); print(card_address(sys.argv[1],2,1,"DoctorCRE/carr-system",1960))', ref], { cwd: new URL('../../', import.meta.url) });
    assert.deepEqual(result.stdout.trim().split('\n'), [expected, expected + '.carr#1960']);
  }
});

test('queue grants preserve the current catalog seal and reject wider grants', async () => {
  const current = async () => (await c.query('select ops.scac_mutation_catalog_v118_current() as current')).rows[0].current;
  assert.equal(await current(), true);
  await c.query('begin');
  try { await c.query('grant delete on ops.work_request_job to carr_writer'); assert.equal(await current(), false); }
  finally { await c.query('rollback'); }
  assert.equal(await current(), true);
});
