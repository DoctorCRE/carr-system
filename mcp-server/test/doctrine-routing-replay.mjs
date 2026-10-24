import { readFileSync, writeFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { doctrineRoutingFixture } from './helpers/doctrine-routing.mjs';

const [directory, output, changesFile] = process.argv.slice(2);
if (!directory || !output) throw new Error('usage: node doctrine-routing-replay.mjs <API evidence directory> <output.json> [curation.json]');
const read = name => JSON.parse(readFileSync(`${directory}/${name}`, 'utf8'));
const cases = read('baseline.json');
const sections = read('sections.json');
const documents = new Map(read('documents.json').map(document => [document.slug, document]));
const changes = changesFile ? JSON.parse(readFileSync(changesFile, 'utf8')) : {};
const fixture = await doctrineRoutingFixture();
try {
  await fixture.client.query('begin');
  for (const section of sections) {
    const body = section.body?.text ?? JSON.stringify(section.body);
    const change = changes[section.id];
    await fixture.insert({ ...section, title: change?.title || section.title,
      body: body + (change?.append_text || '') }, documents.get(section.doc_slug));
  }
  await fixture.client.query('commit');
  const results = [];
  for (const row of cases) {
    const strict = await fixture.search(row.q);
    const live = await fixture.search(row.q, { fallback: true });
    results.push({ case: row.case, q: row.q, gold: row.gold,
      strict: strict.map(hit => hit.section_id), live: live.map(hit => hit.section_id),
      fallback: live.length > 0 && live.every(hit => hit.provenance.fallback === true) });
  }
  writeFileSync(output, JSON.stringify(results, null, 2) + '\n');
  if (changesFile) {
    assert.equal(results.filter(row => row.gold.includes(row.live[0])).length, 10,
      'all ten frozen questions must select an exact current gold target');
  }
  if (!changesFile) {
    for (let i = 0; i < cases.length; i++) {
      assert.deepEqual(results[i].live, cases[i].response.hits.map(hit => hit.section_id),
        `local baseline must reproduce live ordering for ${cases[i].case}`);
    }
  }
  for (const row of results) {
    console.log(row.case, row.fallback ? 'fallback' : 'strict', row.gold.map(id => {
      const rank = row.live.indexOf(id) + 1; return rank || null;
    }));
  }
} finally { await fixture.close(); }
