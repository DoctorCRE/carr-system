// Pin the live stage derivation: one GitHub search per repository per minute, Card lines map PRs to jobs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createPullRequestSource, isolatePullRequests, queueView, jobStage, PULL_REQUEST_TTL_MS, STALE_AFTER_MS } from '../src/work-queue.js';

const REPOS = ['DoctorCRE/carr-system', 'jbookout/doctorcre-app'];
const item = (number, body, extra = {}) => ({ number, body, title: `PR ${number}`, html_url: `https://github.com/x/y/pull/${number}`,
  state: 'open', draft: false, updated_at: '2026-10-27T12:00:00Z', pull_request: { merged_at: null }, ...extra });
function github(itemsByRepo, status = 200) {
  const calls = [];
  const fetchImpl = async url => {
    const repo = new URL(url).searchParams.get('q').match(/^repo:(\S+)/)[1];
    calls.push(repo);
    const items = itemsByRepo[repo] ?? [];
    return new Response(JSON.stringify({ total_count: items.length, items }), { status });
  };
  return { calls, fetchImpl };
}

test('many concurrent readers share one search per repository until the 60-second cache expires', async () => {
  let clock = 0;
  const { calls, fetchImpl } = github({});
  const read = createPullRequestSource({ token: 'synthetic', fetchImpl, now: () => clock });
  await Promise.all(Array.from({ length: 25 }, () => read(REPOS)));
  assert.deepEqual(calls, REPOS);
  clock = PULL_REQUEST_TTL_MS - 1;
  await read(REPOS);
  assert.equal(calls.length, 2, 'still cached just inside the window');
  clock = PULL_REQUEST_TTL_MS;
  await read(REPOS);
  assert.equal(calls.length, 4, 'one fresh search per repository after the window');
});

test('a failed search is cached for the same window, so an outage never multiplies calls', async () => {
  let clock = 0;
  const { calls, fetchImpl } = github({}, 403);
  const read = createPullRequestSource({ token: 'synthetic', fetchImpl, now: () => clock });
  const [first] = await read(REPOS.slice(0, 1));
  assert.deepEqual([first.state, first.reason], ['unavailable', 'github_http_403']);
  await read(REPOS.slice(0, 1));
  assert.equal(calls.length, 1);
});

test('without a credential nothing is fetched and every repository reports unavailable', async () => {
  const { calls, fetchImpl } = github({});
  const sources = await createPullRequestSource({ token: undefined, fetchImpl })(REPOS);
  assert.equal(calls.length, 0);
  assert.deepEqual(sources.map(source => source.reason), ['credential_not_configured', 'credential_not_configured']);
});

test('Card lines map open, draft, merged and closed PRs to their jobs', async () => {
  const { fetchImpl } = github({
    'DoctorCRE/carr-system': [item(10, 'Card: WR557.S1.J2\n\n## Why', { draft: true }), item(11, 'No card line here'), item(12, 'Mentions Card: WR557.S1.J9 inline only')],
    'jbookout/doctorcre-app': [item(20, 'Card: WR557.S4.J1\r\nCard: WR557.S4.J2', { state: 'closed', pull_request: { merged_at: '2026-10-27T13:00:00Z' } })],
  });
  const sources = await createPullRequestSource({ token: 'synthetic', fetchImpl })(REPOS);
  assert.deepEqual(sources.flatMap(source => source.prs.map(pr => [pr.number, pr.state, pr.draft, pr.cards])),
    [[10, 'open', true, ['WR557.S1.J2']], [20, 'merged', false, ['WR557.S4.J1', 'WR557.S4.J2']]]);
});

test('stage is derived from the least advanced open PR, then merged, then closed', () => {
  const pr = (state, draft = false) => ({ state, draft });
  assert.equal(jobStage([]), 'queued');
  assert.equal(jobStage([pr('open', true)]), 'build');
  assert.equal(jobStage([pr('open', true), pr('open')]), 'review');
  assert.equal(jobStage([pr('merged'), pr('open', true)]), 'build');
  assert.equal(jobStage([pr('merged'), pr('closed')]), 'merged');
  assert.equal(jobStage([pr('closed')]), 'closed');
});

test('only building jobs go stale after 24 hours; a queued backlog job waits its turn and is never stale', () => {
  const at = Date.parse('2026-10-28T12:00:00Z');
  const old = new Date(at - STALE_AFTER_MS).toISOString(), fresh = new Date(at - STALE_AFTER_MS + 1).toISOString();
  const row = (j, created_at = old) => ({ work_request_id: 'x', work_request: 'WR-000557', work_request_title: 'Queue', slice_ordinal: 4, j_number: j, created_at });
  const pr = (number, cards, extra) => ({ repo: 'jbookout/doctorcre-app', number, cards, updated_at: old, draft: false, state: 'open', ...extra });
  const view = queueView([row(1), row(2, fresh), row(3), row(4), row(5), row(6)], [{ repo: 'jbookout/doctorcre-app', state: 'ok', prs: [
    pr(30, ['WR557.S4.J3'], { draft: true }), pr(31, ['WR557.S4.J4'], { draft: true, updated_at: fresh }),
    pr(32, ['WR557.S4.J5']), pr(33, ['WR557.S4.J6'], { state: 'merged' })] }], at);
  assert.deepEqual(view.jobs.map(job => [job.card_id, job.stage, job.stale]), [
    ['WR557.S4.J1', 'queued', false], ['WR557.S4.J2', 'queued', false], ['WR557.S4.J3', 'build', true],
    ['WR557.S4.J4', 'build', false], ['WR557.S4.J5', 'review', false], ['WR557.S4.J6', 'merged', false]]);
  assert.equal(view.jobs[4].prs[0].card_id, 'WR557.S4.J5.app#32');
  assert.equal(view.jobs[0].work_request_title, 'Queue');
  assert.equal('work_request_id' in view.jobs[0], false);
  assert.deepEqual(view.github, [{ repo: 'jbookout/doctorcre-app', state: 'ok' }]);
});

test('the shared isolate source without a credential reports unavailable instead of failing the read', async () => {
  const sources = await isolatePullRequests(undefined)(REPOS);
  assert.deepEqual(sources.map(source => source.state), ['unavailable', 'unavailable']);
  assert.equal(isolatePullRequests(undefined), isolatePullRequests(undefined), 'one source per isolate and credential');
});

test('while any repository is unreadable nothing is stale, because a missing PR is not evidence of a stall', () => {
  const at = Date.parse('2026-10-28T12:00:00Z'), old = new Date(at - 3 * STALE_AFTER_MS).toISOString();
  const row = j => ({ work_request_id: 'x', work_request: 'WR-000557', work_request_title: 'Queue', slice_ordinal: 1, j_number: j, created_at: old });
  const draft = { repo: 'DoctorCRE/carr-system', number: 7, cards: ['WR557.S1.J2'], updated_at: old, draft: true, state: 'open' };
  for (const down of [{ state: 'unavailable', reason: 'github_http_403', prs: [] }, { state: 'unavailable', reason: 'credential_not_configured', prs: [] }]) {
    const view = queueView([row(1), row(2)], [{ repo: 'DoctorCRE/carr-system', state: 'ok', prs: [draft] }, { repo: 'jbookout/doctorcre-app', ...down }], at);
    assert.deepEqual(view.jobs.map(job => [job.card_id, job.stage, job.stale]), [['WR557.S1.J1', 'queued', false], ['WR557.S1.J2', 'build', false]]);
  }
});

test('a truncated search never makes an old job stale, even a building one', () => {
  const at = Date.parse('2026-10-28T12:00:00Z'), old = new Date(at - 30 * STALE_AFTER_MS).toISOString();
  const view = queueView([{ work_request_id: 'x', work_request: 'WR-000557', work_request_title: 'Queue', slice_ordinal: 1, j_number: 1, created_at: old }],
    [{ repo: 'DoctorCRE/carr-system', state: 'ok', truncated: true, prs: [] }], at);
  assert.deepEqual(view.jobs.map(job => [job.stage, job.stale]), [['queued', false]]);
});

test('the search only spans PRs updated since the oldest visible job, since no earlier PR can carry its minted card ID', async () => {
  let clock = 0;
  const queries = [];
  const fetchImpl = async url => { queries.push(new URL(url).searchParams.get('q')); return new Response(JSON.stringify({ total_count: 0, items: [] })); };
  const read = createPullRequestSource({ token: 'synthetic', fetchImpl, now: () => clock });
  await read(REPOS.slice(0, 1), '2026-10-20T15:30:00.000Z');
  assert.deepEqual(queries, ['repo:DoctorCRE/carr-system is:pr in:body "Card: WR" updated:>=2026-10-20']);
  await read(REPOS.slice(0, 1), '2026-10-20T23:59:00.000Z');
  assert.equal(queries.length, 1, 'the same bound day shares the cached search');
  await read(REPOS.slice(0, 1), '2026-10-21T00:00:00.000Z');
  assert.deepEqual(queries.at(-1), 'repo:DoctorCRE/carr-system is:pr in:body "Card: WR" updated:>=2026-10-21',
    'a different bound is a different search, so a caller never gets a narrower window than it asked for');
  clock = PULL_REQUEST_TTL_MS;
  await read(REPOS.slice(0, 1));
  assert.deepEqual(queries.at(-1), 'repo:DoctorCRE/carr-system is:pr in:body "Card: WR"', 'an empty queue searches without a bound');
});

const at = Date.parse('2026-10-28T12:00:00Z');
const jobRow = (j, extra = {}) => ({ work_request_id: 'w1', work_request: 'WR-000557', work_request_title: 'Queue', slice_ordinal: 1, j_number: j,
  created_at: new Date(at - 10 * STALE_AFTER_MS).toISOString(), finished: null, ...extra });
const livePr = (number, cards, extra = {}) => ({ repo: 'DoctorCRE/carr-system', number, cards, url: `u${number}`, title: `PR ${number}`,
  updated_at: new Date(at - 5 * STALE_AFTER_MS).toISOString(), draft: false, state: 'open', ...extra });

test('a truncated window suppresses stale on a long-idle draft too', () => {
  const view = queueView([jobRow(1)], [{ repo: 'DoctorCRE/carr-system', state: 'ok', truncated: true, prs: [livePr(5, ['WR557.S1.J1'], { draft: true })] }], at);
  assert.deepEqual(view.jobs.map(job => [job.stage, job.stale]), [['build', false]]);
});

test('a job whose saved finish has left the search window keeps its merged stage and PR links', () => {
  const saved = { stage: 'merged', prs: [{ repo: 'DoctorCRE/carr-system', number: 9, state: 'merged', card_id: 'WR557.S1.J1.carr#9' }] };
  const view = queueView([jobRow(1, { finished: saved })], [{ repo: 'DoctorCRE/carr-system', state: 'ok', truncated: true, prs: [] }], at);
  assert.deepEqual(view.jobs.map(job => [job.card_id, job.stage, job.stale, job.prs.map(pr => pr.card_id)]), [['WR557.S1.J1', 'merged', false, ['WR557.S1.J1.carr#9']]]);
  assert.equal('finished' in view.jobs[0], false);
  assert.deepEqual(view.finishes, []);
});

test('a live PR outranks a saved finish, so a reopened job shows its new work', () => {
  const saved = { stage: 'closed', prs: [{ repo: 'DoctorCRE/carr-system', number: 9, state: 'closed', card_id: 'WR557.S1.J1.carr#9' }] };
  const view = queueView([jobRow(1, { finished: saved })], [{ repo: 'DoctorCRE/carr-system', state: 'ok', prs: [livePr(11, ['WR557.S1.J1'])] }], at);
  assert.deepEqual(view.jobs.map(job => [job.stage, job.prs.map(pr => pr.number)]), [['review', [11]]]);
  assert.deepEqual(view.finishes, [], 'a saved finish is never rewritten');
});

test('only a job first seen merged or closed is saved, once, as its finish', () => {
  const view = queueView([jobRow(1), jobRow(2), jobRow(3), jobRow(4, { finished: { stage: 'merged', prs: [] } })], [{ repo: 'DoctorCRE/carr-system', state: 'ok', prs: [
    livePr(21, ['WR557.S1.J1'], { state: 'merged' }), livePr(22, ['WR557.S1.J2'], { state: 'closed' }), livePr(23, ['WR557.S1.J3']), livePr(24, ['WR557.S1.J4'], { state: 'merged' })] }], at);
  assert.deepEqual(view.finishes.map(({ key, finished }) => [key, finished.stage, finished.prs.map(pr => pr.card_id)]), [
    [['w1', 1, 1], 'merged', ['WR557.S1.J1.carr#21']], [['w1', 1, 2], 'closed', ['WR557.S1.J2.carr#22']]]);
});
