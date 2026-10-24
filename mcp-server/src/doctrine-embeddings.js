export const DOCTRINE_EMBEDDING_MODEL = '@cf/baai/bge-small-en-v1.5';
const DIMENSIONS = 384;
const TIMEOUT_MS = 2000;

// A byte ceiling keeps even fragmented text inside the model's 512-token context.
function embeddingInput(text) {
  return new TextDecoder().decode(new TextEncoder().encode(text).slice(0, 480), { stream: true });
}

export async function doctrineEmbeddings(ai, texts) {
  if (!ai?.run) throw new Error('binding_unavailable');
  let timeout;
  try {
    const result = await Promise.race([
      ai.run(DOCTRINE_EMBEDDING_MODEL, { text: texts.map(embeddingInput), pooling: 'cls' }),
      new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('embedding_timeout')), TIMEOUT_MS); }),
    ]);
    if (!Array.isArray(result?.data) || result.data.length !== texts.length ||
        result.data.some(vector => !Array.isArray(vector) || vector.length !== DIMENSIONS ||
          vector.some(value => !Number.isFinite(value)) || !vector.some(value => value !== 0)))
      throw new Error('invalid_embedding');
    return result.data;
  } finally {
    clearTimeout(timeout);
  }
}

export async function searchDoctrineWithEmbeddings(c, args, actorId) {
  if (!args.q.trim()) return { semantic: { status: 'unavailable', model: DOCTRINE_EMBEDDING_MODEL,
    pooling: 'cls', reason: 'empty_query' } };
  let vector;
  try {
    [vector] = await doctrineEmbeddings(c.ai, [args.q]);
  } catch {
    return { semantic: { status: 'unavailable', model: DOCTRINE_EMBEDDING_MODEL, pooling: 'cls',
      reason: c.ai?.run ? 'embedding_failed' : 'binding_unavailable' } };
  }
  try {
    const found = await c.query(
      `select search_doctrine_semantic($1,$2::uuid,$3::text[],$4,$5,$6::vector) as result`,
      [args.q, actorId, args.content_classes || null, args.limit || 20,
        args.policy_id || null, JSON.stringify(vector)]);
    const { hits, visible, embedded } = found.rows[0].result;
    return { rows: hits, semantic: { model: DOCTRINE_EMBEDDING_MODEL, pooling: 'cls',
      status: embedded === visible ? 'ready' : embedded ? 'partial' : 'not_backfilled',
      visible_sections: visible, embedded_sections: embedded } };
  } catch {
    return { semantic: { status: 'unavailable', model: DOCTRINE_EMBEDDING_MODEL, pooling: 'cls',
      reason: 'retrieval_failed' } };
  }
}

export async function embedDoctrineRevisions(c, revisions) {
  if (!revisions.length) return { status: 'ready', embedded: 0 };
  let vectors;
  try {
    vectors = [];
    for (let offset = 0; offset < revisions.length; offset += 16)
      vectors.push(...await doctrineEmbeddings(c.ai, revisions.slice(offset, offset + 16).map(revision => revision.plain_text)));
  } catch {
    return { status: 'unavailable', embedded: 0,
      reason: c.ai?.run ? 'embedding_failed' : 'binding_unavailable' };
  }
  await c.query('savepoint doctrine_embedding_write');
  try {
    let embedded = 0;
    for (let i = 0; i < revisions.length; i++) {
      const result = await c.query(
        `insert into doctrine_embedding(revision_id,model,embedding)
         select r.id,$2,$3::vector from doctrine_revision r
           join doctrine_section s on s.current_revision_id=r.id and s.id=r.section_id
          where r.id=$1::uuid and s.status='active'
         on conflict (revision_id) do nothing returning revision_id`,
        [revisions[i].revision_id, `${DOCTRINE_EMBEDDING_MODEL}:cls`, JSON.stringify(vectors[i])]);
      embedded += result.rows.length;
    }
    await c.query('release savepoint doctrine_embedding_write');
    return { status: 'ready', embedded };
  } catch {
    await c.query('rollback to savepoint doctrine_embedding_write');
    await c.query('release savepoint doctrine_embedding_write');
    return { status: 'unavailable', embedded: 0, reason: 'embedding_storage_failed' };
  }
}

export async function backfillDoctrineEmbeddings(c, actorId, limit) {
  const pending = await c.query(
    `select s.current_revision_id as revision_id,c.plain_text
       from current_retrievable_doctrine($1::uuid,null) c
       join doctrine_section s on s.id=c.section_id
       join doctrine_revision r on r.id=s.current_revision_id and r.section_id=s.id
       left join doctrine_embedding e on e.revision_id=r.id
      where e.revision_id is null order by s.id limit $2`, [actorId, limit]);
  const semantic = await embedDoctrineRevisions(c, pending.rows);
  const remaining = await c.query(
    `select count(*)::integer as remaining
       from current_retrievable_doctrine($1::uuid,null) c
       join doctrine_section s on s.id=c.section_id
       join doctrine_revision r on r.id=s.current_revision_id and r.section_id=s.id
       left join doctrine_embedding e on e.revision_id=r.id
      where e.revision_id is null`, [actorId]);
  return { ok: true, embedded: semantic.embedded, remaining: remaining.rows[0].remaining,
    semantic: { ...semantic, model: DOCTRINE_EMBEDDING_MODEL, pooling: 'cls' } };
}
