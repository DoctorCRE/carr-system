import test from 'node:test';
import assert from 'node:assert/strict';
import { doctrineRoutingFixture } from './helpers/doctrine-routing.mjs';

const sectionId = '10000000-0000-4000-8000-000000000001';
const conceptId = '10000000-0000-4000-8000-000000000002';
const phraseId = '10000000-0000-4000-8000-000000000003';
const mappingId = '10000000-0000-4000-8000-000000000004';

async function withConcept(run) {
  const fixture = await doctrineRoutingFixture();
  try {
    await fixture.insert({ id: sectionId, doc_slug: 'opaque-guide', section_key: 'procedure',
      title: 'Procedure', body: 'An ordered operational checklist.' });
    await fixture.client.query("insert into retrieval_concept values ($1,'approved')", [conceptId]);
    await fixture.client.query(`insert into retrieval_phrase values ($1,$2,0.9,
      'database service unavailable troubleshooting steps','fts',0.35,'approved')`, [phraseId, conceptId]);
    await fixture.client.query("insert into doctrine_concept_mapping values ($1,$2,$3,1,'approved')",
      [mappingId, conceptId, sectionId]);
    await run(fixture);
  } finally { await fixture.close(); }
}

test('approved situation phrase still routes inside a longer natural question', async () => {
  await withConcept(async fixture => {
    const hits = await fixture.search('How do I follow database service unavailable troubleshooting steps?');
    assert.equal(hits[0]?.section_id, sectionId, 'extra question words must not disable approved concept routing');
    assert.equal(hits[0].lexical_score, 0);
    assert.ok(hits[0].concept_score > 0);
    assert.deepEqual(hits[0].provenance.phrase_ids, [phraseId]);
  });
});

test('shorter existing concept queries retain their score and provenance', async () => {
  await withConcept(async fixture => {
    const hits = await fixture.search('database service unavailable troubleshooting');
    assert.equal(hits[0]?.section_id, sectionId);
    assert.ok(Math.abs(hits[0].concept_score - 0.09) < 0.000001);
    assert.deepEqual(hits[0].provenance.mapping_ids, [mappingId]);
  });
});

test('ordinary hyphenated question words preserve complete approved phrase matches', async t => {
  await withConcept(async fixture => {
    for (const query of [
      'How do I follow database service unavailable troubleshooting steps for a read-only replica?',
      'How do I follow database service unavailable troubleshooting steps step-by-step?',
    ]) {
      await t.test(query, async () => {
        const hits = await fixture.search(query);
        assert.equal(hits[0]?.section_id, sectionId, query);
        assert.equal(hits[0].lexical_score, 0);
        assert.ok(Math.abs(hits[0].concept_score - 0.09) < 0.000001);
        assert.deepEqual(hits[0].provenance.phrase_ids, [phraseId]);
      });
    }
  });
});

test('long questions cannot match an approved phrase through partial word overlap', async () => {
  await withConcept(async fixture => {
    assert.deepEqual(await fixture.search('How do I follow database service unavailable payment steps?'), []);
  });
});

test('websearch exclusions and quotes do not enter the additional containment path', async () => {
  await withConcept(async fixture => {
    for (const query of [
      'How do I follow -database service unavailable troubleshooting steps?',
      'How do I follow database service unavailable troubleshooting steps -billing?',
      'How do I follow -database service unavailable troubleshooting steps step-by-step?',
      'How do I follow "database service unavailable troubleshooting steps"?',
      'How do I follow database service unavailable troubleshooting steps OR billing?',
    ]) assert.deepEqual(await fixture.search(query), [], query);
  });
});

test('exact and trigram phrases retain their own matching behavior', async () => {
  await withConcept(async fixture => {
    await fixture.client.query("update retrieval_phrase set match_mode='exact'");
    assert.deepEqual(await fixture.search('How do I follow database service unavailable troubleshooting steps?'), []);
    assert.equal((await fixture.search('database service unavailable troubleshooting steps'))[0]?.concept_score, 0.9);
    await fixture.client.query("update retrieval_phrase set match_mode='trgm', min_similarity=1");
    assert.deepEqual(await fixture.search('How do I follow database service unavailable troubleshooting steps?'), []);
    assert.equal((await fixture.search('database service unavailable troubleshooting steps'))[0]?.concept_score, 0.9);
  });
});

test('concept question matches remain restricted to approved current visible sections', async () => {
  await withConcept(async fixture => {
    const query = 'How do I follow database service unavailable troubleshooting steps?';
    for (const table of ['retrieval_phrase', 'retrieval_concept', 'doctrine_concept_mapping']) {
      await fixture.client.query(`update ${table} set status='retired'`);
      assert.deepEqual(await fixture.search(query), [], table);
      await fixture.client.query(`update ${table} set status='approved'`);
    }
    await fixture.client.query("update doctrine_section set status='retired'");
    assert.deepEqual(await fixture.search(query), []);
    await fixture.client.query("update doctrine_section set status='active'");
    const owner = '10000000-0000-4000-8000-000000000005';
    await fixture.client.query("update doctrine_document set visibility='personal', owner_actor_id=$1", [owner]);
    assert.deepEqual(await fixture.search(query), []);
    assert.equal((await fixture.search(query, { actor: owner }))[0]?.section_id, sectionId);
    assert.deepEqual(await fixture.search(query, { actor: owner, classes: ['reference'] }), []);
  });
});
