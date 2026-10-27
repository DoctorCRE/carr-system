// Test lead deduplication against labeled identity matching cases.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
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

test('same-name leads at different locations keep their own verify result (WR-000513)', async () => {
  const source = readFileSync(new URL('workflows/lead-sweep.workflow.js', `file://${root}`), 'utf8');
  const leads = ['Example City', 'Other Example City'].map((location, index) => ({
    name: 'Example Dental', location, vertical: 'dental', confidence: 'LOW',
    signal: `Synthetic observation ${index}`, sourceUrl: `https://example.invalid/same-name/${index}`,
  }));
  const result = await runInNewContext(
    `(async () => { ${source.replace(/^export const meta =/, 'const meta =')} })()`, {
      args: { today: 'SYNTHETIC', lanes: ['one', 'two'], regions: ['fixture'], cap: 2 },
      phase() {}, log() {},
      parallel: jobs => Promise.all(jobs.map(job => job())),
      agent: async (_prompt, options) => options.phase === 'Sweep'
        ? { leads: [leads[Number(options.label.split(':')[1]) - 1]] }
        : { checks: [
          { item: 1, name: 'Example Dental', exists: true, note: 'first', corroboration: 'license' },
          { item: 2, name: 'Example Dental', exists: false, note: 'second' }] },
    }, { timeout: 1000 });
  assert.deepEqual(JSON.parse(JSON.stringify(result.leads.map(lead => [lead.location, lead.check?.note, lead.tier]))), [
    ['Example City', 'first', 'CORROBORATED'],
    ['Other Example City', 'second', 'UNVERIFIED (present anyway, flag)'],
  ]);
});

test('an ambiguous or mismatched verify claim is refused, never guessed (WR-000513)', async () => {
  const source = readFileSync(new URL('workflows/lead-sweep.workflow.js', `file://${root}`), 'utf8');
  const leads = ['Example City', 'Other Example City'].map((location, index) => ({
    name: index ? 'Other Dental' : 'Example Dental', location, vertical: 'dental', confidence: 'LOW',
    signal: `Synthetic observation ${index}`, sourceUrl: `https://example.invalid/refuse/${index}`,
  }));
  const result = await runInNewContext(
    `(async () => { ${source.replace(/^export const meta =/, 'const meta =')} })()`, {
      args: { today: 'SYNTHETIC', lanes: ['one', 'two'], regions: ['fixture'], cap: 2 },
      phase() {}, log() {},
      parallel: jobs => Promise.all(jobs.map(job => job())),
      agent: async (_prompt, options) => options.phase === 'Sweep'
        ? { leads: [leads[Number(options.label.split(':')[1]) - 1]] }
        : { checks: [
          { item: 1, name: 'Example Dental', exists: true, note: 'first', corroboration: 'license' },
          { item: 1, name: 'Example Dental', exists: false, note: 'conflicting repeat' },
          { item: 2, name: 'Example Dental', exists: true, note: 'echoed the wrong lead' }] },
    }, { timeout: 1000 });
  assert.deepEqual(JSON.parse(JSON.stringify(result.leads.map(lead => [lead.name, lead.check, lead.tier]))), [
    ['Example Dental', null, 'UNVERIFIED (present anyway, flag)'],
    ['Other Dental', null, 'UNVERIFIED (present anyway, flag)'],
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(result.leads.map(lead => lead.checkRefused))), [
    'verifier returned claim 1 more than once', 'verifier echoed "Example Dental" for claim 2',
  ]);
});
