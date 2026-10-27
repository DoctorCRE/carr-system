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

test('only queued and building jobs go stale after 24 hours; review and merged never do', () => {
  const at = Date.parse('2026-10-28T12:00:00Z');
  const old = new Date(at - STALE_AFTER_MS).toISOString(), fresh = new Date(at - STALE_AFTER_MS + 1).toISOString();
  const row = (j, created_at = old) => ({ work_request_id: 'x', work_request: 'WR-000557', work_request_title: 'Queue', slice_ordinal: 4, j_number: j, created_at });
  const pr = (number, cards, extra) => ({ repo: 'jbookout/doctorcre-app', number, cards, updated_at: old, draft: false, state: 'open', ...extra });
  const view = queueView([row(1), row(2, fresh), row(3), row(4), row(5), row(6)], [{ repo: 'jbookout/doctorcre-app', state: 'ok', prs: [
    pr(30, ['WR557.S4.J3'], { draft: true }), pr(31, ['WR557.S4.J4'], { draft: true, updated_at: fresh }),
    pr(32, ['WR557.S4.J5']), pr(33, ['WR557.S4.J6'], { state: 'merged' })] }], at);
  assert.deepEqual(view.jobs.map(job => [job.card_id, job.stage, job.stale]), [
    ['WR557.S4.J1', 'queued', true], ['WR557.S4.J2', 'queued', false], ['WR557.S4.J3', 'build', true],
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
