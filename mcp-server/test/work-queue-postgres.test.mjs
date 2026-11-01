// Exercise queue commands through the real transaction envelope and PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { publicError } from '../src/public-errors.ts';
import { queueCardId } from '../src/work-queue.js';
import { mcpInventory, assertCurrentContracts } from '../../ops/scac-mutation-inventory.mjs';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';

let fixture, c, actor, connection;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'work-queue-', snapshot: true });
  connection = fixture.connection;
  c = new pg.Client(connection);
  await c.connect();
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => {
  try { await c?.end(); } finally { await fixture?.close(); }
});

async function command(name, args = {}, client = c, role = name === 'work-request-card' ? 'carr_reader' : 'carr_writer') {
  if (name === 'close-work-request-slice' && !Object.hasOwn(args, 'base_version'))
    args = { ...args, base_version: (await command('work-request-card', { work_request: args.work_request })).slices.find(row => row.slice_id === queueCardId(args.work_request, args.slice)).version };
  await client.query('begin');
  try {
    await client.query(`set local role ${role}`);
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
let titleNumber = 0;
const job = (work_request, extra = {}) => ({ idempotency_key: randomUUID(), work_request,
  job_type: 'bug-fix', executor: 'codex', model: 'synthetic-model', effort: 'high', card_title: `Build work ${String.fromCharCode(97 + Math.floor(titleNumber / 26))}${String.fromCharCode(97 + titleNumber++ % 26)}`,
  description: 'Build the synthetic queue behavior.', ...extra });

test('job version constraint is valid after migrations', async () => {
  const { rows } = await c.query("select convalidated from pg_constraint where conrelid='ops.work_request_job'::regclass and conname='work_request_job_version_positive'");
  assert.deepEqual(rows, [{ convalidated: true }]);
});

test('dispatch mints a stable ID under implicit S1 and retries without another job', async () => {
  const work_request = await request();
  const args = job(work_request);
  const first = await command('dispatch-job', args);
  assert.equal(first.card_id, 'WR14.S1.J1');
  assert.equal((await command('dispatch-job', args)).card_id, first.card_id);
  const queue = await command('work-queue', {});
  assert.deepEqual(queue.jobs.map(row => row.card_id), ['WR14.S1.J1']);
  assert.equal(queue.jobs[0].card_title, 'Build work aa');
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
    ['dispatch-job', job('WR-000014', { description: 'Build cards\nwith tests.' }), 'invalid_job_description'],
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
  const start = { work_request, slice: 1, job: 1, repository, runner_job_id: 't3-synthetic', idempotency_key: randomUUID() };
  const first = await command('dispatch-job', start);
  assert.equal(first.card_id, 'WR17.S1.J1');
  assert.equal(first.runner_job_id, 't3-synthetic');
  assert.deepEqual(await command('dispatch-job', start), { ...first, replayed: true });
  const queued = (await command('work-queue')).jobs.filter(row => row.work_request === work_request);
  assert.equal(queued.length, 1);
  assert.equal(queued[0].repository, repository);
  assert.equal(queued[0].runner_job_id, 't3-synthetic');
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
  assert.deepEqual((await card()).slices, [{ slice_id: 'WR64.S1', title: 'Original slice', version: 1, outcome: 'open' }]);
  await plan(['Original slice', 'Appended slice']);
  assert.deepEqual((await card()).slices, [
    { slice_id: 'WR64.S1', title: 'Original slice', version: 1, outcome: 'open' },
    { slice_id: 'WR64.S2', title: 'Appended slice', version: 1, outcome: 'open' },
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
  const installed = (await c.query('select ops.scac_current_grant_contract() as contract')).rows[0].contract;
  assert.deepEqual(installed.relation_dml.filter(row => row.relation === 'ops.work_request_job')
    .map(row => [row.grantee, row.privilege, row.grantable]), [['carr_writer', 'insert', false], ['carr_writer', 'update', false]]);
  assert.equal(assertCurrentContracts(TOOLS), true);
  const census = mcpInventory(TOOLS).filter(row => row.operation === 'retitle-job');
  assert.equal(census.length, 1);
  assert.equal(census[0].write, true);
  await c.query('begin');
  try { await c.query('grant delete on ops.work_request_job to carr_writer'); assert.equal(await current(), false); }
  finally { await c.query('rollback'); }
  assert.equal(await current(), true);
});

test('queue reads never persist observed completion', async () => {
  const work_request = await request('WR-000061');
  const card = (await command('dispatch-job', job(work_request, { executor: 'claude' }))).card_id;
  const queries = [];
  const merged = { repo: 'DoctorCRE/carr-system', number: 77, url: 'u77', title: 'PR 77', draft: false, state: 'merged', updated_at: '2026-10-27T12:00:00Z', cards: [card] };
  const reader = { query: (text, params) => { queries.push(text); return c.query(text, params); },
    pullRequests: async repos => repos.map(repo => ({ repo, state: 'ok', prs: repo === merged.repo ? [merged] : [] })),
    sideWrite: () => { throw new Error('queue read attempted a write'); } };
  const result = await command('work-queue', {}, reader);
  assert.equal(result.jobs.find(row => row.card_id === card).stage, 'merged');
  assert.ok(queries.every(sql => !/\b(insert|update|delete)\b/i.test(sql)));
  const stored = (await c.query('select finished from ops.work_request_job where work_request_id=(select id from ops.work_request where ref=$1)', [work_request])).rows[0];
  assert.equal(stored.finished, null);
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
  const minted = job(work_request, { repository, card_title: 'Build queued cards' });
  await command('dispatch-job', minted);
  await command('dispatch-job', job(work_request, { repository, job: 1, runner_job_id: 'queued-launcher',
    model: 'selected-model', effort: 'medium', card_title: 'Review queued cards' }));
  const row = (await command('work-queue')).jobs.find(row => row.work_request === work_request);
  assert.equal(row.model, 'selected-model');
  assert.equal(row.effort, 'medium');
  assert.equal(row.card_title, minted.card_title);
});

test('launcher starts an existing legacy card with its stored copy', async () => {
  const work_request = await request('WR-000096');
  const repository = 'DoctorCRE/carr-system';
  await command('dispatch-job', job(work_request, { repository }));
  await c.query(`update ops.work_request_job set card_title='Codex job', description='Execute the supplied job brief.'
    where work_request_id=(select id from ops.work_request where ref=$1)`, [work_request]);
  const queued = (await command('work-queue')).jobs.find(row => row.work_request === work_request);
  await command('dispatch-job', job(work_request, { repository, job: 1, runner_job_id: 'legacy-runner',
    card_title: queued.card_title, description: queued.description }));
  assert.equal((await command('work-queue')).jobs.find(row => row.work_request === work_request).runner_job_id, 'legacy-runner');
});


test('enqueue refuses missing and unknown workflow types without minting a job', async () => {
  const work_request = await request('WR-000027');
  for (const job_type of [undefined, 'unknown-workflow']) {
    const args = job(work_request, { job_type });
    if (job_type === undefined) delete args.job_type;
    await assert.rejects(command('dispatch-job', args), { message: job_type === undefined ? 'invalid_job_type' : 'value_not_in_declared_vocabulary' });
  }
  assert.equal((await command('work-queue')).jobs.filter(row => row.work_request === work_request).length, 0);
  await command('dispatch-job', job(work_request));
  assert.equal((await command('work-queue')).jobs.find(row => row.work_request === work_request).job_type, 'bug-fix');
});

test('a runner cannot change the queued workflow type', async () => {
  const work_request = await request('WR-000028');
  const repository = 'DoctorCRE/carr-system';
  await command('dispatch-job', job(work_request, { repository }));
  const start = { work_request, slice: 1, job: 1, runner_job_id: 'typed-runner', repository, idempotency_key: randomUUID() };
  await assert.rejects(command('dispatch-job', { ...start, job_type: 'review' }), { message: 'job_type_mismatch' });
  await command('dispatch-job', { ...start, job_type: 'bug-fix' });
  assert.equal((await command('work-queue')).jobs.find(row => row.work_request === work_request).job_type, 'bug-fix');
});

test('a typed runner starts an untyped job and stores its workflow type exactly once', async () => {
  const work_request = await request('WR-000029');
  const repository = 'DoctorCRE/carr-system';
  await command('dispatch-job', job(work_request, { repository }));
  await c.query('update ops.work_request_job set job_type=null where work_request_id=(select id from ops.work_request where ref=$1)', [work_request]);
  const start = { work_request, job: 1, repository, job_type: 'bug-fix', runner_job_id: 'legacy-runner', idempotency_key: randomUUID() };
  assert.equal((await command('dispatch-job', start)).card_id, 'WR29.S1.J1');
  const queued = (await command('work-queue')).jobs.filter(row => row.work_request === work_request);
  assert.equal(queued.length, 1);
  assert.equal(queued[0].job_type, 'bug-fix');
  assert.equal(queued[0].runner_job_id, 'legacy-runner');
  assert.ok(queued[0].started_at);
  await assert.rejects(command('dispatch-job', { ...start, runner_job_id: 'second-runner', idempotency_key: randomUUID() }),
    { message: 'job_already_started' });

});

test('slice retirement requires a reason and replays one outcome event', async () => {
  const work_request = await request('WR-000070');
  await command('plan-work-request-slices', { work_request, slices: ['Build slice', 'Replacement slice'], idempotency_key: randomUUID() });
  const close = { work_request, slice: 1, outcome: 'retired', idempotency_key: randomUUID() };
  await assert.rejects(command('close-work-request-slice', close), { message: 'slice_reason_required' });
  const args = { ...close, base_version: 1, reason: 'The replacement covers this work.', superseded_by: 2 };
  const first = await command('close-work-request-slice', args);
  assert.equal(first.outcome, 'retired');
  assert.equal(first.superseded_by, 'WR70.S2');
  assert.equal(first.outcome_by, actor.slug);
  assert.ok(first.outcome_at);
  assert.deepEqual(await command('close-work-request-slice', args), { ...first, replayed: true });
  assert.equal((await c.query('select count(*) from event where idempotency_key=$1', [args.idempotency_key])).rows[0].count, '1');
  const card = () => command('work-request-card', { work_request });
  assert.equal((await card()).slices[0].outcome, 'retired');

});

test('done requires merged change evidence and preserves it in the card and queue', async () => {
  const work_request = await request('WR-000071');
  await command('plan-work-request-slices', { work_request, idempotency_key: randomUUID() });
  await command('dispatch-job', job(work_request));
  const close = extra => command('close-work-request-slice', { work_request, slice: 1, outcome: 'done', idempotency_key: randomUUID(), ...extra });
  for (const change_refs of [undefined, [], [null], [{ pr_url: 'https://example.com/pull/1', merge_commit_sha: 'a'.repeat(40) }],
    [{ pr_url: 'https://github.com/DoctorCRE/carr-system/pull/1', merge_commit_sha: 'abc' }]]) {
    await assert.rejects(close({ change_refs }), { message: 'slice_merged_change_required' });
  }
  await assert.rejects(close({ outcome: 'retired', reason: 'Replacement scope.', superseded_by: 1 }), { message: 'invalid_slice_successor' });
  await assert.rejects(close({ outcome: 'retired', reason: 'Replacement scope.', superseded_by: 99 }), { message: 'slice_not_found' });
  await assert.rejects(close({ slice: 99, base_version: 1 }), { message: 'slice_not_found' });
  await assert.rejects(close({ outcome: 'open' }), { message: 'value_not_in_declared_vocabulary' });
  const change_refs = [{ pr_url: 'https://github.com/DoctorCRE/carr-system/pull/2674', merge_commit_sha: 'a'.repeat(40) }];
  const { ok, ...done } = await close({ change_refs });
  assert.equal(done.outcome, 'done');
  assert.deepEqual(done.change_refs, change_refs);
  assert.deepEqual((await command('work-request-card', { work_request })).slices[0], { title: 'Synthetic queue work', ...done });
  assert.deepEqual((await command('work-queue')).slices.find(row => row.slice_id === done.slice_id), { title: 'Synthetic queue work', work_request, ...done });

});

test('done requires explicit change evidence even when a merged job finish is saved', async () => {
  const work_request = await request('WR-000072');
  await command('dispatch-job', job(work_request));
  const close = extra => command('close-work-request-slice', { work_request, slice: 1, outcome: 'done', idempotency_key: randomUUID(), ...extra });
  const pr_url = 'https://github.com/DoctorCRE/carr-system/pull/2674';
  const save = async pr => c.query(`update ops.work_request_job set finished=$2::jsonb
    where work_request_id=(select id from ops.work_request where ref=$1)`, [work_request, JSON.stringify({ stage: 'merged', prs: [pr] })]);
  await save({ state: 'merged', url: pr_url });
  await assert.rejects(close(), { message: 'slice_merged_change_required' });
  assert.equal((await command('work-request-card', { work_request })).slices[0].outcome, 'open');
  const merge_commit_sha = 'c'.repeat(40);
  await save({ state: 'merged', url: pr_url, merge_commit_sha });
  await assert.rejects(close(), { message: 'slice_merged_change_required' });
  const done = await close({ change_refs: [{ pr_url, merge_commit_sha }] });
  assert.equal(done.outcome_by, actor.slug);
  assert.deepEqual(done.change_refs, [{ pr_url, merge_commit_sha }]);
  assert.deepEqual((await command('work-request-card', { work_request })).slices[0].change_refs, done.change_refs);
});

test('a recorded outcome refuses another outcome and preserves merged PR evidence', async () => {
  const work_request = await request('WR-000074');
  await command('plan-work-request-slices', { work_request, idempotency_key: randomUUID() });
  const close = extra => command('close-work-request-slice', { work_request, slice: 1, idempotency_key: randomUUID(), ...extra });
  const change_refs = [{ pr_url: 'https://github.com/DoctorCRE/carr-system/pull/2674', merge_commit_sha: 'b'.repeat(40) }];
  await close({ outcome: 'done', change_refs });
  for (const outcome of ['retired', 'done']) {
    await assert.rejects(close({ outcome, reason: 'Changed scope.', ...(outcome === 'done' ? { change_refs } : {}) }),
      { message: 'slice_outcome_already_recorded' });
    assert.deepEqual((await command('work-request-card', { work_request })).slices[0].change_refs, change_refs);
  }
  await command('plan-work-request-slices', { work_request, slices: ['Synthetic queue work', 'Retired scope'], idempotency_key: randomUUID() });
  await close({ slice: 2, outcome: 'retired', reason: 'Obsolete scope.' });
  await assert.rejects(close({ slice: 2, outcome: 'retired', reason: 'Another reason.' }), { message: 'slice_outcome_already_recorded' });
});

test('slice outcomes require a fresh slice version as base_version', async () => {
  const work_request = await request('WR-000075');
  await command('plan-work-request-slices', { work_request, slices: ['First scope', 'Second scope'], idempotency_key: randomUUID() });
  const card = () => command('work-request-card', { work_request });
  const { version: base_version } = (await card()).slices[0];
  const close = extra => command('close-work-request-slice', { work_request, slice: 1, outcome: 'retired', reason: 'Obsolete scope.', idempotency_key: randomUUID(), base_version, ...extra });
  await close({});
  assert.equal((await card()).slices[0].version, base_version + 1);
  await assert.rejects(close({}), { message: 'version_conflict' });
  assert.equal((await card()).slices[1].outcome, 'open');
  await assert.rejects(close({ base_version: (await card()).slices[0].version }), { message: 'slice_outcome_already_recorded' });
  await close({ slice: 2, base_version: (await card()).slices[1].version });
  assert.equal((await card()).slices[1].outcome, 'retired');
});

test('concurrent outcome writers with the same slice version cannot replace the winner', async () => {
  const work_request = await request('WR-000076');
  await command('plan-work-request-slices', { work_request, idempotency_key: randomUUID() });
  const { version: base_version } = (await command('work-request-card', { work_request })).slices[0];
  const clients = await Promise.all(Array.from({ length: 2 }, async () => { const client = new pg.Client(connection); await client.connect(); return client; }));
  try {
    const results = await Promise.allSettled(clients.map((client, i) => command('close-work-request-slice',
      { work_request, slice: 1, outcome: 'retired', reason: `Obsolete scope ${i}.`, base_version, idempotency_key: randomUUID() }, client)));
    assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
    assert.equal(results.find(result => result.status === 'rejected').reason.message, 'version_conflict');
    const winner = results.find(result => result.status === 'fulfilled').value;
    assert.equal((await command('work-request-card', { work_request })).slices[0].reason, winner.reason);
  } finally { await Promise.all(clients.map(client => client.end())); }
});

test('dispatch refusals provide usable copy suggestions and vocabulary', async () => {
  const work_request = await request('WR-000097');
  await command('dispatch-job', job(work_request, { card_title: 'Build readable cards' }));
  for (const card_title of ['Bad 123 title', 'Queue cards', 'Build readable cards']) {
    let suggestion;
    await assert.rejects(command('dispatch-job', job(work_request, { card_title })), failure => {
      const result = publicError(failure);
      assert.match(result.suggested_card_title, /^(Add|Build|Fix|Remove|Rename|Review|Audit|Plan|Revise|Migrate|Move|Record|Require|Gate|Adopt|Rebind|Repin|Close|Install|Retire) [A-Za-z']+( [A-Za-z']+)?$/);
      suggestion = result.suggested_card_title;
      return true;
    });
    await command('dispatch-job', job(work_request, { card_title: suggestion }));
  }
  await assert.rejects(command('dispatch-job', job(work_request, { card_title: 'x'.repeat(81) })), failure => {
    assert.equal(failure.message, 'invalid_work_queue_fields');
    assert.ok(publicError(failure).suggested_card_title);
    return true;
  });
  await assert.rejects(command('dispatch-job', job(work_request, { description: 'Implement useful cards. Another sentence.' })), failure => {
    const result = publicError(failure);
    assert.match(result.suggested_description, /^Build .+[.]$/);
    return true;
  });
  await assert.rejects(command('dispatch-job', job(work_request, { job_type: 'unknown' })), failure => {
    const result = publicError(failure, { inputSchema: TOOLS['dispatch-job'].inputSchema });
    assert.ok(result.allowed_values.includes('bug-fix'));
    return true;
  });
});

test('mint refuses a title whose first word is outside the imperative vocabulary', async () => {
  const work_request = await request('WR-000091');
  await assert.rejects(command('dispatch-job', job(work_request, { card_title: 'Queue work' })), failure => {
    assert.equal(failure.message, 'card_title_verb');
    assert.match(publicError(failure).hint, /job-card-verbs[.]v1[.]json/);
    return true;
  });
});

test('titles are unique across slices of one Work Request and reusable in another', async () => {
  const work_request = await request('WR-000092');
  const other = await request('WR-000093');
  await command('dispatch-job', job(work_request, { card_title: 'Build readable cards', description: 'Build readable cards.' }));
  await command('plan-work-request-slices', { work_request, slices: ['Synthetic queue work', 'Other slice'], idempotency_key: randomUUID() });
  for (const card_title of ['Build readable cards', 'Build Readable Cards'])
    await assert.rejects(command('dispatch-job', job(work_request, { slice: 2, card_title, description: 'Build readable cards.' })), { message: 'card_title_duplicate' });
  await command('dispatch-job', job(other, { card_title: 'Build readable cards', description: 'Build readable cards.' }));
});

test('retitle changes only labels, replays safely, and enforces mint refusals', async () => {
  const work_request = await request('WR-000094');
  const minted = await command('dispatch-job', job(work_request, { card_title: 'Build rename proof', description: 'Build a rename proof.' }));
  const sibling = await command('dispatch-job', job(work_request, { card_title: 'Review rename proof', description: 'Review the rename proof.' }));
  const read = async () => (await command('work-queue')).jobs.filter(row => row.work_request === work_request);
  const before = await read();
  const stored = async () => (await c.query('select j.* from ops.work_request_job j join ops.work_request w on w.id=j.work_request_id where w.ref=$1 order by j.j_number', [work_request])).rows;
  const storedBefore = await stored();
  const args = { base_version: 1, card_id: minted.card_id, card_title: 'Require readable titles', description: 'Require readable titles for every card.', idempotency_key: randomUUID() };
  const result = await command('retitle-job', args);
  assert.equal(result.card_id, minted.card_id);
  assert.deepEqual(await command('retitle-job', args), { ...result, replayed: true });
  const after = await read();
  assert.deepEqual(await stored(), storedBefore.map(row => row.j_number === 1
    ? { ...row, card_title: args.card_title, description: args.description, version: row.version + 1 } : row));
  assert.deepEqual(after, before.map(row => row.card_id === minted.card_id
    ? { ...row, card_title: args.card_title, description: args.description, version: row.version + 1 } : row));
  for (const [changes, error] of [
    [{ card_title: 'Queue work' }, 'card_title_verb'],
    [{ card_title: 'Review rename proof' }, 'card_title_duplicate'],
    [{ card_title: 'Require far too many words' }, 'invalid_card_title'],
    [{ description: 'Build one. Review two.' }, 'invalid_job_description'],
    [{ description: 'Build cards\nwith tests.' }, 'invalid_job_description'],
    [{ description: 'Implement the change.' }, 'invalid_job_description'],
    [{ card_id: 'WR94.S1.J999' }, 'job_not_found'],
  ]) await assert.rejects(command('retitle-job', { ...args, base_version: 2, ...changes, idempotency_key: randomUUID() }), { message: error });
  assert.deepEqual(await read(), after);
  await command('retitle-job', { card_id: minted.card_id, card_title: args.card_title, base_version: 2, idempotency_key: randomUUID() });
  assert.equal((await read()).find(row => row.card_id === minted.card_id).version, 3);
  assert.equal(after.find(row => row.card_id === sibling.card_id).card_title, 'Review rename proof');
});

test('retitles reject a stale job version', async () => {
  const work_request = await request('WR-000097');
  const { card_id } = await command('dispatch-job', job(work_request));
  const base_version = (await command('work-queue')).jobs.find(row => row.card_id === card_id).version;
  const rename = card_title => command('retitle-job', { card_id, card_title, base_version, idempotency_key: randomUUID() });
  await rename('Fix first title');
  await assert.rejects(rename('Fix stale title'), error => {
    assert.equal(error.message, 'version_conflict');
    assert.equal(error.payload.current_version, base_version + 1);
    return true;
  });
});

test('concurrent retitles from one version preserve the winner', async () => {
  const work_request = await request('WR-000099');
  const { card_id } = await command('dispatch-job', job(work_request));
  const base_version = (await command('work-queue')).jobs.find(row => row.card_id === card_id).version;
  const clients = await Promise.all(Array.from({ length: 2 }, async () => { const client = new pg.Client(connection); await client.connect(); return client; }));
  try {
    const results = await Promise.allSettled(clients.map((client, i) => command('retitle-job',
      { card_id, card_title: i ? 'Fix second title' : 'Fix first title', base_version, idempotency_key: randomUUID() }, client)));
    assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
    const rejected = results.find(result => result.status === 'rejected').reason;
    assert.equal(rejected.message, 'version_conflict');
    assert.equal(rejected.payload.current_version, base_version + 1);
    assert.equal((await command('work-queue')).jobs.find(row => row.card_id === card_id).card_title,
      results.find(result => result.status === 'fulfilled').value.card_title);
  } finally { await Promise.all(clients.map(client => client.end())); }
});

test('retitle requires replacement for an invalid stored description', async () => {
  const work_request = await request('WR-000098');
  const { card_id } = await command('dispatch-job', job(work_request));
  await c.query(`update ops.work_request_job set description='Execute the supplied job brief.'
    where work_request_id=(select id from ops.work_request where ref=$1)`, [work_request]);
  await assert.rejects(command('retitle-job', { card_id, card_title: 'Fix legacy description', base_version: 1, idempotency_key: randomUUID() }), error => {
    assert.equal(error.message, 'invalid_job_description');
    assert.match(publicError(error).hint, /new description/i);
    return true;
  });
});

test('concurrent duplicate mints admit one card and descriptions use the same vocabulary', async () => {
  const work_request = await request('WR-000095');
  await assert.rejects(command('dispatch-job', job(work_request, { card_title: 'Build concurrent proof', description: 'Implement concurrent proof.' })), { message: 'invalid_job_description' });
  const clients = await Promise.all(Array.from({ length: 2 }, async () => { const client = new pg.Client(connection); await client.connect(); return client; }));
  try {
    const results = await Promise.allSettled(clients.map(client => command('dispatch-job', job(work_request, { card_title: 'Build concurrent proof', description: 'Build concurrent proof.' }), client)));
    assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
    assert.equal(results.find(result => result.status === 'rejected').reason.message, 'card_title_duplicate');
    assert.equal((await command('work-queue')).jobs.filter(row => row.work_request === work_request).length, 1);
  } finally { await Promise.all(clients.map(client => client.end())); }
});

test('launcher description fixtures are accepted at mint', async () => {
  const descriptions = JSON.parse(readFileSync(new URL('../../tools/fixtures/job-descriptions.json', import.meta.url), 'utf8'));
  const work_request = await request('WR-000101');
  for (const description of descriptions) {
    const result = await command('dispatch-job', job(work_request, { description }));
    assert.ok(result.card_id);
  }
});


test('dispatch reports every rejected field with its value and repair in one call', async () => {
  const work_request = await request('WR-000698');
  await command('dispatch-job', job(work_request, { card_title: 'Build dispatch errors' }));
  for (const job_type of ['perf-issue', 'chore']) {
    await assert.rejects(command('dispatch-job', job(work_request, {
      job_type, card_title: 'Build dispatch errors', description: 'Implement errors. Then retry.'
    })), error => {
      const fields = publicError(error).field_errors;
      assert.equal(fields.find(e => e.field === 'job_type').value, job_type);
      assert.ok(fields.find(e => e.field === 'job_type').allowed_values.includes('bug-fix'));
      assert.equal(fields.find(e => e.field === 'card_title').code, 'card_title_duplicate');
      assert.match(fields.find(e => e.field === 'description').pattern, /one sentence/);
      return true;
    });
  }
  for (const card_title of ['Rereview cards', 'Update cards', 'Resync cards', 'Too many words here']) {
    await assert.rejects(command('dispatch-job', job(work_request, { card_title, work_request: 'wrong', description: 'Implement errors.' })), error => {
      const fields = publicError(error).field_errors;
      assert.deepEqual(fields.map(e => e.field).sort(), ['card_title', 'description', 'work_request']);
      assert.equal(fields.find(e => e.field === 'card_title').value, card_title);
      assert.ok(fields.find(e => e.field === 'card_title').allowed_values.includes('Revise'));
      assert.match(fields.find(e => e.field === 'work_request').pattern, /WR-/);
      return true;
    });
  }
});

test('dispatch normalizes short work request references and publishes copy rules', async () => {
  const work_request = await request('WR-000675');
  for (const ref of ['WR675', 'WR-675']) {
    const result = await command('dispatch-job', job(ref));
    assert.match(result.card_id, /^WR675[.]S1[.]J/);
  }
  const schema = TOOLS['dispatch-job'].inputSchema.properties;
  assert.ok(schema.job_type.enum.includes('bug-fix'));
  assert.match(schema.card_title.description, /2-3/);
  assert.match(schema.card_title.description, /Revise/);
  assert.ok(new RegExp(schema.work_request.pattern).test(work_request));
});

test('missing dispatch job_type exposes its allowed values publicly', async () => {
  const { job_type, ...args } = job(await request());
  await assert.rejects(command('dispatch-job', args), error => {
    const reply = publicError(error, { inputSchema: TOOLS['dispatch-job'].inputSchema });
    assert.equal(reply.error, 'invalid_job_type');
    assert.deepEqual(reply.field_errors, [{ field: 'job_type', value: null,
      code: 'invalid_job_type', allowed_values: TOOLS['dispatch-job'].inputSchema.properties.job_type.enum }]);
    return true;
  });
});

test('missing Work Request preserves the other dispatch field errors', async () => {
  for (const extra of [{ card_title: 'Update cards' }, { job_type: 'chore' }]) {
    await assert.rejects(command('dispatch-job', job('WR-000999', extra)), error => {
      const reply = publicError(error, { inputSchema: TOOLS['dispatch-job'].inputSchema });
      assert.equal(reply.error, 'invalid_work_queue_fields');
      assert.equal(reply.field_errors.length, 2);
      assert.ok(reply.field_errors.some(e => e.field === Object.keys(extra)[0]));
      assert.deepEqual(reply.field_errors.find(e => e.field === 'work_request'), {
        field: 'work_request', value: 'WR-000999', code: 'work_request_not_found',
        pattern: 'an existing Work Request visible to this actor',
      });
      return true;
    });
  }
});

for (const [title, suggested] of [['Update cards', 'Revise cards'], ['Rereview cards', 'Review cards'], ['Resync cards', 'Fix cards']]) {
  test(`dispatch suggests ${suggested} for ${title}`, async () => {
    await assert.rejects(command('dispatch-job', job(await request(), { card_title: title })), error => {
      assert.equal(publicError(error).suggested_card_title, suggested);
      return true;
    });
  });
}
