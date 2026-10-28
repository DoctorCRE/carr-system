// Prove search_doctrine_semantic reads the cosine index and keeps the old ranking on a fixed embedding set.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readMigration } from './migration-source.testhelper.mjs';

const MODEL = '@cf/baai/bge-small-en-v1.5:cls:chunks-v1';
const ACTOR = '10000000-0000-4000-8000-000000000004';

function prng(seed) {
  let x = seed >>> 0 || 1;
  return () => { x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0; return x / 2 ** 32 - 0.5; };
}
const unitVector = (rand, center = null, spread = 1) => {
  const v = Array.from({ length: 384 }, (_, i) => (center ? center[i] : 0) + spread * rand());
  const norm = Math.hypot(...v);
  return v.map(x => x / norm);
};

// Sections cluster around eight centres; chunk counts are skewed like the real corpus.
async function seed(c, sections, chunkCounts, rand) {
  const centers = Array.from({ length: 8 }, () => unitVector(rand));
  const chunkSets = [];
  for (let n = 0; n < sections; n++) {
    const center = centers[n % 8];
    const doc = await c.query(`insert into doctrine_document(id,slug,visibility,content_class,owner_actor_id)
      values (gen_random_uuid(),$1,$2,'reference',$3) returning id`, [`fixture-${n}`, n % 11 === 0 ? 'personal' : 'shared', ACTOR]);
    const rev = await c.query(`insert into doctrine_revision(id,section_id,plain_text) values (gen_random_uuid(),gen_random_uuid(),'sundial') returning id,section_id`);
    await c.query(`insert into doctrine_section(id,document_id,section_key,title,status,current_revision_id)
      values ($1,$2,$3,'',$4,$5)`, [rev.rows[0].section_id, doc.rows[0].id, `fixture-${n}`, n % 13 === 0 ? 'retired' : 'active', rev.rows[0].id]);
    const count = chunkCounts[n % chunkCounts.length];
    for (let i = 0; i < count; i++)
      chunkSets.push([rev.rows[0].id, i, JSON.stringify(unitVector(rand, center, 0.9))]);
  }
  for (let i = 0; i < chunkSets.length; i += 500) {
    const part = chunkSets.slice(i, i + 500);
    await c.query(`insert into doctrine_embedding(revision_id,chunk_index,model,embedding)
      select (x->>0)::uuid,(x->>1)::int,$1,(x->>2)::vector from jsonb_array_elements($2::jsonb) x`,
    [MODEL, JSON.stringify(part)]);
  }
  return chunkSets.length;
}

async function withOldBody(c) {
  const original = readMigration('0978_doctrine_title_topic_ranking.sql');
  const start = original.indexOf('create or replace function public.search_doctrine_semantic(');
  await c.query(original.slice(start).replace('public.search_doctrine_semantic(', 'public.search_doctrine_semantic_old('));
}

const call = (c, fn, vectors, limit = 20) => c.query(
  `select ${fn}($1,$2::uuid,$3::text[],$4,$5,$6::vector[]) as result`,
  ['sundial', ACTOR, ['reference'], limit, null, vectors.map(v => JSON.stringify(v))]).then(r => r.rows[0].result);
const ids = result => result.hits.map(hit => hit.section_id);

test('new body returns the old results while every chunk fits the index candidate window', async () => {
  const { doctrineEmbeddingDatabase } = await import('./helpers/doctrine-embedding-postgres.mjs');
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const c = fixture.client, rand = prng(7);
    const chunks = await seed(c, 120, [1, 2, 3, 4, 6], rand);
    assert.ok(chunks < 1000, 'fixture stays inside the 1000-chunk window');
    await withOldBody(c);
    for (const queryCount of [1, 2, 3]) for (let trial = 0; trial < 4; trial++) {
      const vectors = Array.from({ length: queryCount }, () => unitVector(rand));
      const before = await call(c, 'search_doctrine_semantic_old', vectors);
      const after = await call(c, 'search_doctrine_semantic', vectors);
      assert.ok(before.hits.length >= 10, 'the comparison has real hits');
      assert.deepEqual(ids(after), ids(before));
      assert.equal(after.visible, before.visible);
      assert.equal(after.embedded, before.embedded);
      assert.deepEqual(after.hits.map(h => h.provenance.semantic_similarity),
        before.hits.map(h => h.provenance.semantic_similarity));
    }
  } finally { await fixture.close(); }
});

test('beyond the window the top ranks stay the same and the plan reads doctrine_embedding_cosine_idx', async () => {
  const { doctrineEmbeddingDatabase } = await import('./helpers/doctrine-embedding-postgres.mjs');
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const c = fixture.client, rand = prng(11);
    const chunks = await seed(c, 300, [1, 2, 3, 4, 6, 12], rand);
    assert.ok(chunks > 1000, 'fixture is larger than one candidate window');
    await c.query('analyze doctrine_embedding');
    await withOldBody(c);
    let overlap = 0, top3 = 0, trials = 0;
    for (let trial = 0; trial < 10; trial++) {
      const vectors = [unitVector(rand)];
      const before = await call(c, 'search_doctrine_semantic_old', vectors);
      const after = await call(c, 'search_doctrine_semantic', vectors);
      const old = ids(before).slice(0, 10), now = new Set(ids(after).slice(0, 10));
      overlap += old.filter(id => now.has(id)).length / 10;
      top3 += old.slice(0, 3).every((id, i) => ids(after)[i] === id) ? 1 : 0;
      trials++;
    }
    assert.ok(overlap / trials >= 0.9, `top-10 overlap ${overlap / trials}`);
    assert.ok(top3 / trials >= 0.9, `top-3 identical in ${top3}/${trials}`);
    await c.query('set enable_seqscan = off'); // the fixture is small enough that the planner prefers a scan; production's 29k rows do not
    const plan = await c.query(`explain (format json)
      select e.revision_id from doctrine_embedding e where e.model=$1
       order by e.embedding operator(public.<=>) $2::vector limit 1000`, [MODEL, JSON.stringify(unitVector(rand))]);
    assert.ok(JSON.stringify(plan.rows[0]['QUERY PLAN']).includes('"Index Name":"doctrine_embedding_cosine_idx"'), 'ordered nearest-neighbour shape uses the cosine index');
  } finally { await fixture.close(); }
});
