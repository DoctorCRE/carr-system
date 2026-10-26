import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS } from '../src/tools.js';
import { doctrineEmbeddingDatabase } from './helpers/doctrine-embedding-postgres.mjs';

const actor = { slug: 'codex', human: false, via: 'agent-token' };
const vector = (x, y) => [x, y, ...Array(382).fill(0)];
const model = '@cf/baai/bge-small-en-v1.5:cls:chunks-v1';

async function section(c, suffix, title, body, embedding) {
  const id = part => `30000000-0000-4000-8000-${String(suffix * 3 + part).padStart(12, '0')}`;
  await c.query(`insert into doctrine_document(id,slug,visibility) values($1,$2,'shared')`, [id(0), `topic-${suffix}`]);
  await c.query(`insert into doctrine_revision(id,section_id,plain_text) values($1,$2,$3)`, [id(2), id(1), body]);
  await c.query(`insert into doctrine_section(id,document_id,section_key,title,status,current_revision_id)
    values($1,$2,$3,$4,'active',$5)`, [id(1), id(0), `topic-${suffix}`, title, id(2)]);
  await c.query(`insert into doctrine_embedding(revision_id,model,embedding) values($1,$2,$3::vector)`,
    [id(2), model, JSON.stringify(embedding)]);
  return id(1);
}

test('territory definition outranks incidental mentions with and without semantic retrieval', async () => {
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const query = fixture.client.query.bind(fixture.client);
    const target = await section(fixture.client, 1, 'Our Territory', 'Eastport, Westhaven and Northfield.', vector(.1, .9));
    await section(fixture.client, 0, 'Visual style notes', 'territory '.repeat(40), vector(1, 0));
    for (const ai of [undefined, { run: async () => ({ data: [vector(1, 0)] }) },
      { run: async () => { throw new Error('offline'); } }]) {
      const out = await TOOLS['search-doctrine'].handler({ query, ai }, actor, { q: 'territory', limit: 1 });
      assert.equal(out.hits[0]?.section_id, target);
      assert.equal(out.hits[0].title, 'Our Territory');
      assert.equal(out.hits[0].provenance.title_topic_match, true);
      const normalized = await TOOLS['search-doctrine'].handler({ query, ai }, actor, { q: '  OUR  territories  ', limit: 1 });
      assert.equal(normalized.hits[0]?.section_id, target);
    }
  } finally { await fixture.close(); }
});

test('title-topic priority is general, current and restricted by the existing visibility filters', async () => {
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const query = fixture.client.query.bind(fixture.client);
    const target = await section(fixture.client, 1, 'Our Fees', 'The rate is stated in each agreement.', vector(.1, .9));
    const incidental = await section(fixture.client, 0, 'Marketing examples', 'fees '.repeat(40), vector(1, 0));
    const c = { query, ai: { run: async () => ({ data: [vector(1, 0)] }) } };
    const search = args => TOOLS['search-doctrine'].handler(c, actor, { q: 'fees', limit: 1, ...args });
    assert.equal((await search()).hits[0].section_id, target);
    for (const q of ['"fees"', 'fees OR marketing', 'fees -agreement']) {
      const operator = await search({ q });
      assert.ok(operator.hits.every(hit => !hit.provenance.title_topic_match));
    }
    const quoted = await search({ q: '"fees"' });
    assert.equal(quoted.hits[0].section_id, incidental);
    assert.equal(quoted.hits[0].provenance.title_topic_match, false);
    const multiword = await search({ q: 'fees marketing' });
    assert.ok(multiword.hits.every(hit => !hit.provenance.title_topic_match));
    await query(`update doctrine_section set title='Our Lease Fees' where id=$1`, [target]);
    assert.equal((await search({ q: 'lease fees' })).hits[0].section_id, target);
    assert.equal((await search({ q: 'lease OR fees' })).hits[0].provenance.title_topic_match, false);
    await query(`update doctrine_section set section_key='agreement-pricing' where id=$1`, [target]);
    assert.equal((await search({ q: 'lease fees' })).hits[0].section_id, target);
    await query(`update doctrine_section set title='Agreement rates' where id=$1`, [target]);
    assert.equal((await search()).hits[0].section_id, incidental);
    await query(`update doctrine_section set title='Our Fees',status='retired' where id=$1`, [target]);
    assert.equal((await search()).hits[0].section_id, incidental);
    await query(`update doctrine_section set status='active' where id=$1`, [target]);
    await query(`update doctrine_document set visibility='personal' where id=(select document_id from doctrine_section where id=$1)`, [target]);
    assert.equal((await search()).hits[0].section_id, incidental);
    await query(`update doctrine_document set visibility='shared',content_class='sop' where id=(select document_id from doctrine_section where id=$1)`, [target]);
    assert.equal((await search({ content_classes: ['reference'] })).hits[0].section_id, incidental);
    assert.equal((await search({ content_classes: ['sop'] })).hits[0].section_id, target);
  } finally { await fixture.close(); }
});

test('equal title topics retain deterministic score and identity ordering', async () => {
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const query = fixture.client.query.bind(fixture.client);
    const later = await section(fixture.client, 1, 'Our Territory', 'Eastport.', vector(1, 0));
    const earlier = await section(fixture.client, 0, 'Our Territory', 'Westhaven.', vector(1, 0));
    for (const ai of [undefined, { run: async () => ({ data: [vector(1, 0)] }) }]) {
      const out = await TOOLS['search-doctrine'].handler({ query, ai }, actor, { q: 'territory' });
      assert.deepEqual(out.hits.map(hit => hit.section_id), [earlier, later]);
      assert.ok(out.hits.every(hit => hit.provenance.title_topic_match));
      assert.equal(out.hits[0].lexical_score, out.hits[1].lexical_score);
    }
  } finally { await fixture.close(); }
});
