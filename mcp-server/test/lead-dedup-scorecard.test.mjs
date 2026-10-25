import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const run = () => JSON.parse(execFileSync(process.execPath,
  ['evals/lead-dedup/scorecard.mjs'], { cwd: root, encoding: 'utf8' }));

test('scorecard exposes false merges and splits from the current lead workflow', () => {
  const report = run();
  assert.equal(report.work_request, 'WR-AI-008');
  assert.equal(report.adjudication, 'pending-human-review');
  assert.deepEqual(report.counts, {
    pairs: 10, duplicate: 6, distinct: 4,
    true_merge: 4, true_split: 2, false_merge: 2, false_split: 2,
  });
  assert.deepEqual(report.rates, { false_merge: 0.5, false_split: 1 / 3 });
  assert.deepEqual(report.cases.filter(row => !row.correct).map(row => row.id), [
    'name-only', 'location-prefix-collision', 'location-spacing', 'location-practice-fallback',
  ]);
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
