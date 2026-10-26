import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { scorecard } from '../../evals/lead-dedup/scorecard.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const run = () => JSON.parse(execFileSync(process.execPath,
  ['evals/lead-dedup/scorecard.mjs'], { cwd: root, encoding: 'utf8' }));
const decide = async (...leads) => (await scorecard({ work_request: 'WR-AI-008',
  pairs: [{ id: 'probe', expected: 'distinct', leads }] })).cases[0].observed;

test('lead workflow matches every labelled pair in the scorecard', () => {
  const report = run();
  assert.equal(report.work_request, 'WR-AI-008');
  assert.equal(report.adjudication, 'pending-human-review');
  assert.deepEqual(report.counts, {
    pairs: 10, duplicate: 6, distinct: 4,
    true_merge: 6, true_split: 4, false_merge: 0, false_split: 0,
  });
  assert.deepEqual(report.rates, { false_merge: 0, false_split: 0 });
});

test('same name without a corroborating field stays distinct', async () => {
  assert.equal(await decide({ name: 'Avery Example' }, { name: 'Avery Example' }), 'distinct');
});

test('the whole location is compared, not a prefix', async () => {
  assert.equal(await decide(
    { name: 'Example Dental', location: '100 Example Medical Plaza Suite 100' },
    { name: 'Example Dental', location: '100 Example Medical Plaza Suite 200' }), 'distinct');
});

test('location whitespace and case do not split one identity', async () => {
  assert.equal(await decide(
    { name: 'Example Dental', location: ' Example  City' },
    { name: 'Example Dental', location: 'EXAMPLE CITY ' }), 'duplicate');
});

test('a shared practice corroborates when only one lead has a location', async () => {
  assert.equal(await decide(
    { name: 'Avery Example', practice: 'Example Dental', location: 'Example City' },
    { name: 'Avery Example', practice: 'Example Dental' }), 'duplicate');
});

test('a conflicting corroborating field keeps leads distinct', async () => {
  assert.equal(await decide(
    { name: 'Avery Example', practice: 'Example Dental', location: 'Example City' },
    { name: 'Avery Example', practice: 'Example Dental', location: 'Other Example City' }), 'distinct');
  assert.equal(await decide(
    { name: 'Avery Example', practice: 'Example Dental', location: 'Example City' },
    { name: 'Avery Example', practice: 'Other Dental', location: 'Example City' }), 'distinct');
});

test('scorecard preserves low-confidence presentation and merged source evidence', () => {
  const report = run();
  const merged = report.cases.find(row => row.id === 'corroborated-name').presented;
  assert.equal(merged.length, 1);
  assert.equal(merged[0].sourceUrl, 'https://example.invalid/corroborated-name/0');
  assert.equal(merged[0].signal,
    'Synthetic observation 0 | ALSO: Synthetic observation 1 (https://example.invalid/corroborated-name/1)');
  assert.equal(merged[0].confidence, 'HIGH');
  const distinct = report.cases.find(row => row.id === 'different-name').presented;
  assert.deepEqual(distinct.map(lead => lead.confidence), ['LOW', 'HIGH']);
  assert.deepEqual(distinct.map(lead => lead.tier), [
    'UNVERIFIED (present anyway, flag)', 'UNVERIFIED (present anyway, flag)',
  ]);
});
