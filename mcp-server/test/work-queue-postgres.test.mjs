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
  if (!(await c.query("select 1 from schema_migrations where filename='1024_work_request_job_start.sql'")).rows.length)
    await c.query(readFileSync(new URL('../../migrations/1024_work_request_job_start.sql', import.meta.url), 'utf8'));
  if (!(await c.query("select 1 from schema_migrations where filename='1025_work_request_job_finished.sql'")).rows.length)
    await c.query(readFileSync(new URL('../../migrations/1025_work_request_job_finished.sql', import.meta.url), 'utf8'));
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
    await client.query(`set local role ${name === 'work-request-card' ? 'carr_reader' : 'carr_writer'}`);
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

test('a runner adopts an unbound repository and records its start once without minting another card', async () => {
  const work_request = await request('WR-000017');
  const repository = 'DoctorCRE/carr-system';
  await command('dispatch-job', job(work_request));
  const start = { work_request, slice: 1, job: 1, repository, runner_job_id: 'codex-job-synthetic', idempotency_key: randomUUID() };
  const first = await command('dispatch-job', start);
  assert.equal(first.card_id, 'WR17.S1.J1');
  assert.equal(first.runner_job_id, 'codex-job-synthetic');
  assert.deepEqual(await command('dispatch-job', start), { ...first, replayed: true });
  const queued = (await command('work-queue')).jobs.filter(row => row.work_request === work_request);
  assert.equal(queued.length, 1);
  assert.equal(queued[0].repository, repository);
  assert.equal(queued[0].runner_job_id, 'codex-job-synthetic');
  assert.ok(queued[0].started_at);
  await assert.rejects(command('dispatch-job', { ...start, runner_job_id: 'another-runner', idempotency_key: randomUUID() }),
    failure => { assert.equal(failure.message, 'job_already_started'); assert.equal(publicError(failure).error, 'job_already_started'); return true; });
});

test('a runner can only claim a job bound to its repository', async () => {
  const work_request = await request('WR-000019');
  const repository = 'jbookout/doctorcre-app';
  await command('dispatch-job', job(work_request, { repository }));
  const start = { work_request, job: 1, repository, runner_job_id: 'bound-runner', idempotency_key: randomUUID() };
  await assert.rejects(command('dispatch-job', { ...start, repository: 'DoctorCRE/carr-system' }),
    failure => { assert.equal(failure.message, 'job_repository_mismatch'); assert.equal(publicError(failure).error, 'job_repository_mismatch'); return true; });
  let queued = (await command('work-queue')).jobs.find(row => row.work_request === work_request);
  assert.equal(queued.repository, repository);
  assert.equal(queued.runner_job_id, null);
  assert.equal((await command('dispatch-job', start)).card_id, 'WR19.S1.J1');
  queued = (await command('work-queue')).jobs.find(row => row.work_request === work_request);
  assert.equal(queued.runner_job_id, 'bound-runner');
});

test('concurrent runners cannot start the same queued job twice', async () => {
  const work_request = await request('WR-000018');
  const repository = 'DoctorCRE/carr-system';
  await command('dispatch-job', job(work_request, { repository }));
  const clients = await Promise.all(Array.from({ length: 2 }, async () => { const client = new pg.Client(connection); await client.connect(); return client; }));
  try {
    const results = await Promise.allSettled(clients.map((client, index) => command('dispatch-job',
      { work_request, job: 1, repository, runner_job_id: `runner-${index}`, idempotency_key: randomUUID() }, client)));
    assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
    assert.equal(results.find(result => result.status === 'rejected').reason.message, 'job_already_started');
    assert.equal((await command('work-queue')).jobs.find(row => row.work_request === work_request).runner_job_id,
      results.find(result => result.status === 'fulfilled').value.runner_job_id);
  } finally { await Promise.all(clients.map(client => client.end())); }
});

test('a plan can append slices while preserving earlier IDs and jobs, and repeat without change', async () => {
  const work_request = await request('WR-000062');
  const plan = slices => command('plan-work-request-slices', { work_request, slices, idempotency_key: randomUUID() });
  const first = await plan(['First slice', 'Second slice']);
  const dispatched = await command('dispatch-job', job(work_request, { slice: 2 }));
  const appended = await plan(['First slice', 'Second slice', 'Third slice', 'Fourth slice']);
  assert.deepEqual(appended.slices, [
    { slice_id: 'WR62.S1', title: 'First slice' },
    { slice_id: 'WR62.S2', title: 'Second slice' },
    { slice_id: 'WR62.S3', title: 'Third slice' },
    { slice_id: 'WR62.S4', title: 'Fourth slice' },
  ]);
  assert.deepEqual(appended.slices.slice(0, 2), first.slices);
  assert.deepEqual(await plan(['First slice', 'Second slice', 'Third slice', 'Fourth slice']), appended);
  assert.deepEqual((await command('work-queue')).jobs.filter(row => row.work_request === work_request).map(row => row.card_id), [dispatched.card_id]);
  assert.equal((await command('dispatch-job', job(work_request, { slice: 4 }))).card_id, 'WR62.S4.J1');
});

test('renaming, reordering, or removing a slice refuses the plan and returns recorded titles', async () => {
  const work_request = await request('WR-000063');
  const slices = ['First slice', 'Second slice'];
  const plan = titles => command('plan-work-request-slices', { work_request, slices: titles, idempotency_key: randomUUID() });
  const recorded = await plan(slices);
  for (const changed of [['Renamed slice', 'Second slice'], ['Second slice', 'First slice'], ['First slice'], []]) {
    await assert.rejects(plan(changed), failure => {
      assert.equal(failure.message, 'slice_plan_conflict');
      const refusal = publicError(failure);
      assert.equal(refusal.error, 'slice_plan_conflict');
      assert.deepEqual(refusal.recorded_titles, ['First slice', 'Second slice']);
      return true;
    });
    assert.deepEqual(await plan(slices), recorded);
  }
});

test('the Work Request card reads recorded slice titles and S numbers, including appended slices', async () => {
  const work_request = await request('WR-000064');
  const card = () => command('work-request-card', { work_request });
  assert.deepEqual((await card()).slices, []);
  const plan = slices => command('plan-work-request-slices', { work_request, slices, idempotency_key: randomUUID() });
  await assert.rejects(plan([]), { message: 'invalid_slice_plan' });
  await plan(['Original slice']);
  assert.deepEqual((await card()).slices, [{ slice_id: 'WR64.S1', title: 'Original slice' }]);
  await plan(['Original slice', 'Appended slice']);
  assert.deepEqual((await card()).slices, [
    { slice_id: 'WR64.S1', title: 'Original slice' },
    { slice_id: 'WR64.S2', title: 'Appended slice' },
  ]);
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

test('the read saves a finish once on the side connection, and later reads keep it after the PR leaves the window', async () => {
  const work_request = await request('WR-000061');
  const done = (await command('dispatch-job', job(work_request))).card_id;
  await c.query("update ops.work_request_job set created_at=now()-interval '20 days' where work_request_id=(select id from ops.work_request where ref=$1)", [work_request]);
  const pending = (await command('dispatch-job', job(work_request))).card_id;
  const bounds = [], writes = [];
  const merged = { repo: 'DoctorCRE/carr-system', number: 77, url: 'u77', title: 'PR 77', draft: false, state: 'merged', updated_at: '2026-10-27T12:00:00Z', cards: [done] };
  const reader = sources => ({ query: (text, params) => c.query(text, params),
    pullRequests: async (repos, since) => { bounds.push(since); return repos.map(repo => ({ repo, state: 'ok', prs: repo === merged.repo ? sources : [] })); },
    sideWrite: (text, params) => { writes.push(params[2]); return c.query(text, params); } });
  const first = await command('work-queue', {}, reader([merged]));
  assert.equal(first.jobs.find(row => row.card_id === done).stage, 'merged');
  assert.equal(writes.length, 1, 'one single-row write for the newly finished job');
  await command('work-queue', {}, reader([merged]));
  const later = await command('work-queue', {}, reader([]));
  assert.equal(writes.length, 1, 'a saved finish is never written again');
  const saved = later.jobs.find(row => row.card_id === done);
  assert.deepEqual([saved.stage, saved.stale, saved.prs.map(pr => pr.card_id)], ['merged', false, [`${done}.carr#77`]]);
  const open = later.jobs.find(row => row.card_id === pending);
  assert.equal(open.stage, 'queued');
  assert.equal(bounds[0], first.jobs.find(row => row.card_id === done).created_at, 'before the finish is saved, the old job bounds the window');
  const unfinished = later.jobs.filter(row => row.card_id !== done).map(row => row.created_at).sort();
  assert.equal(bounds.at(-1), unfinished[0], 'once saved, the window starts at the oldest unfinished job');
  assert.ok(bounds.at(-1) > bounds[0] && bounds.at(-1) <= open.created_at);
});


test('launcher registers an exact card and start atomically, with registered slices only', async () => {
  const work_request = await request('WR-000025');
  const args = job(work_request, { slice: 2, job: 7, runner_job_id: 'codex-launcher', repository: 'DoctorCRE/carr-system' });
  await assert.rejects(command('dispatch-job', args), { message: 'slice_not_found' });
  await command('plan-work-request-slices', { work_request, slices: ['First slice', 'Second slice'], idempotency_key: randomUUID() });
  const first = await command('dispatch-job', args);
  assert.equal(first.card_id, 'WR25.S2.J7');
  assert.equal(first.runner_job_id, 'codex-launcher');
  assert.deepEqual(await command('dispatch-job', args), { ...first, replayed: true });
  const rows = (await command('work-queue')).jobs.filter(row => row.work_request === work_request);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].model, 'synthetic-model');
  assert.ok(rows[0].started_at);
});


test('launcher starts a queued card with actual execution settings and preserves its copy', async () => {
  const work_request = await request('WR-000026');
  const repository = 'DoctorCRE/carr-system';
  await command('dispatch-job', job(work_request, { repository }));
  await command('dispatch-job', job(work_request, { repository, job: 1, runner_job_id: 'queued-launcher',
    model: 'selected-model', effort: 'medium', card_title: 'Codex job' }));
  const row = (await command('work-queue')).jobs.find(row => row.work_request === work_request);
  assert.equal(row.model, 'selected-model');
  assert.equal(row.effort, 'medium');
  assert.equal(row.card_title, 'Queue work');
});
