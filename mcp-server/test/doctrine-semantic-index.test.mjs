// Retiring doctrine_embedding_cosine_idx must not change semantic search: the function scans exactly.
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

const call = (c, vectors, limit = 20) => c.query(
  `select search_doctrine_semantic($1,$2::uuid,$3::text[],$4,$5,$6::vector[]) as result`,
  ['sundial', ACTOR, ['reference'], limit, null, vectors.map(v => JSON.stringify(v))]).then(r => r.rows[0].result);
const indexCount = c => c.query(`select count(*)::int as n from pg_indexes where indexname='doctrine_embedding_cosine_idx'`).then(r => r.rows[0].n);

test('retiring the cosine index leaves semantic search results exactly equal', async () => {
  const { doctrineEmbeddingDatabase } = await import('./helpers/doctrine-embedding-postgres.mjs');
  const fixture = await doctrineEmbeddingDatabase();
  try {
    const c = fixture.client, rand = prng(7);
    const chunks = await seed(c, 300, [1, 2, 3, 4, 6, 12], rand);
    assert.ok(chunks > 1000, 'fixture is larger than an index candidate window would be');
    await c.query('analyze doctrine_embedding');
    assert.equal(await indexCount(c), 1, 'fixture starts with the index');
    const queries = [];
    for (const queryCount of [1, 2, 3]) for (let trial = 0; trial < 4; trial++)
      queries.push(Array.from({ length: queryCount }, () => unitVector(rand)));
    const before = [];
    for (const q of queries) before.push(await call(c, q));
    await c.query(readMigration('1035_retire_doctrine_embedding_cosine_idx.sql'));
    assert.equal(await indexCount(c), 0, 'migration removes the index');
    for (const [i, q] of queries.entries()) {
      const after = await call(c, q);
      assert.ok(after.hits.length >= 10, 'the comparison has real hits');
      assert.deepEqual(after, before[i]);
    }
  } finally { await fixture.close(); }
});
