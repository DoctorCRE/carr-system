import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { doctrineRoutingFixture } from './helpers/doctrine-routing.mjs';
import { searchDoctrineSituations } from '../src/situation-retrieval.js';

const cases = JSON.parse(readFileSync(new URL('./fixtures/doctrine-routing-cases.json', import.meta.url), 'utf8'));
const id = n => `90000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const actor = { slug: 'codex', human: false, via: 'agent-token', sponsoring_human_slug: null };

test('task phrases retrieve current procedures while strict and fallback retain distinct guarantees', async t => {
  const fixture = await doctrineRoutingFixture();
  try {
    for (const row of cases) {
      await fixture.insert({ id: row.gold, doc_slug: `procedure-${row.case.toLowerCase()}`,
        section_key: row.case.toLowerCase(), title: row.title, body: row.text });
    }
    const historyId = id(1);
    await fixture.insert({ id: historyId, doc_slug: 'historical-review-packet', section_key: 'history', title: 'Earlier review',
      body: cases.map(row => row.q.split(' ').join(' historical evidence '.repeat(50))).join('\n') });
    for (const row of cases) {
      await t.test(`${row.case} frozen question and curated alias select the current section`, async () => {
        for (const q of [row.q, row.alias]) {
          const result = await searchDoctrineSituations(fixture.client, actor, { q, limit: 10 });
          assert.equal(result.fallback, false);
          assert.equal(result.hits[0]?.section_id, row.gold, q);
          assert.ok(result.hits[0].provenance.complete);
          assert.notEqual(result.hits[0].section_id, historyId);
        }
      });
    }
    await t.test('a strict miss stays empty and fallback results are marked as leads', async () => {
      const q = 'database zzqxunmatched';
      assert.deepEqual(await fixture.search(q), []);
      const result = await searchDoctrineSituations(fixture.client, actor, { q, limit: 10 });
      assert.equal(result.fallback, true);
      assert.ok(result.hits.length > 0);
      assert.ok(result.hits.every(hit => hit.provenance.fallback === true));
      assert.deepEqual(await fixture.search('zzqxunmatchedonly', { fallback: true }), []);
    });
    await t.test('withdrawn sections and historical revisions cannot supply a current phrase', async () => {
      const withdrawn = id(2), supersededRevision = id(3);
      await fixture.insert({ id: withdrawn, doc_slug: 'withdrawn-procedure', section_key: 'retired',
        title: 'Withdrawn procedure', status: 'retired', body: 'withdrawn lifecycle uniqueprobe' });
      await fixture.insert({ id: supersededRevision, doc_slug: 'current-procedure', section_key: 'current',
        title: 'Current procedure', body: 'current lifecycle instructions' });
      await fixture.client.query('insert into doctrine_revision(section_id,plain_text) values ($1,$2)',
        [supersededRevision,'withdrawn lifecycle uniqueprobe']);
      assert.deepEqual(await fixture.search('withdrawn lifecycle uniqueprobe'), []);
      const live = await fixture.search('withdrawn lifecycle uniqueprobe', { fallback: true });
      assert.ok(live.every(hit => hit.section_id !== withdrawn));
      assert.ok(live.every(hit => hit.section_id !== supersededRevision || hit.provenance.fallback));
    });
    await t.test('retired curation and retired targets cannot contribute concepts', async () => {
      await fixture.client.query("insert into retrieval_concept values ($1,'approved'),($2,'retired')", [id(10), id(11)]);
      await fixture.client.query(`insert into retrieval_phrase values
        ($1,$2,1,'withdrawn uniqueprobe','exact',0.35,'approved'),
        ($3,$4,1,'withdrawn uniqueprobe','exact',0.35,'approved')`, [id(12),id(10),id(13),id(11)]);
      await fixture.client.query(`insert into doctrine_concept_mapping values
        ($1,$2,$3,1,'approved'),($4,$5,$6,1,'retired'),($7,$8,$6,1,'approved')`,
      [id(14),id(10),id(2),id(15),id(10),cases[0].gold,id(16),id(11)]);
      assert.deepEqual(await fixture.search('withdrawn uniqueprobe'), []);
      await fixture.client.query("insert into retrieval_phrase values ($1,$2,1,'retiredphrase uniqueprobe','exact',0.35,'retired')",
        [id(17),id(10)]);
      await fixture.client.query("insert into doctrine_concept_mapping values ($1,$2,$3,1,'approved')",
        [id(18),id(10),cases[0].gold]);
      assert.deepEqual(await fixture.search('retiredphrase uniqueprobe'), []);

    });
    await t.test('personal ownership and content-class scope apply in both lanes', async () => {
      for (const [n, owner] of [[20,id(30)], [21,id(31)]]) {
        await fixture.insert({ id: id(n), doc_slug: `personal-${n}`, section_key: 'private',
          title: 'Private routing', visibility: 'personal', content_class: 'reference', body: 'personalfixture uniquepersonal' },
        { owner_actor_id: owner });
      }
      for (const fallback of [false,true]) {
        assert.deepEqual(await fixture.search('personalfixture uniquepersonal', { fallback }), []);
        const rows = await fixture.search('personalfixture uniquepersonal', { actor:id(30), fallback });
        assert.deepEqual(rows.map(row => row.section_id), [id(20)]);
        assert.deepEqual(await fixture.search('personalfixture uniquepersonal',
          { actor:id(30), classes:['sop'], fallback }), []);
      }
      assert.deepEqual(await fixture.search(cases[0].q, { classes:['reference'] }), []);
    });
  } finally { await fixture.close(); }
});
