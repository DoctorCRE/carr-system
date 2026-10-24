const DOCTRINE_EMBEDDING_MODEL = '@cf/baai/bge-small-en-v1.5';
const EMBEDDING_VERSION = `${DOCTRINE_EMBEDDING_MODEL}:cls:chunks-v1`;
const DIMENSIONS = 384;
const TIMEOUT_MS = 2000;

// Conservative UTF-8 bounds stay inside the 512-token context, including special tokens.
function embeddingChunks(text) {
  const characters = Array.from(text);
  const sizes = characters.map(character => new TextEncoder().encode(character).length);
  const chunks = [];
  for (let start = 0; start < characters.length;) {
    let end = start, bytes = 0;
    while (end < characters.length && bytes + sizes[end] <= 480) bytes += sizes[end++];
    chunks.push(characters.slice(start, end).join(''));
    if (end === characters.length) break;
    let next = end, overlap = 0;
    while (next > start + 1 && overlap + sizes[next - 1] <= 96) overlap += sizes[--next];
    start = next;
  }
  return chunks.length ? chunks : [''];
}

async function doctrineEmbeddings(ai, texts) {
  if (!ai?.run) throw new Error('binding_unavailable');
  let timeout;
  try {
    const result = await Promise.race([
      ai.run(DOCTRINE_EMBEDDING_MODEL, { text: texts, pooling: 'cls' }),
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

async function embedChunks(ai, texts) {
  const vectors = [];
  for (let offset = 0; offset < texts.length; offset += 16)
    vectors.push(...await doctrineEmbeddings(ai, texts.slice(offset, offset + 16)));
  return vectors;
}

export async function searchDoctrineWithEmbeddings(c, args, actorId) {
  if (!args.q.trim()) return { semantic: { status: 'unavailable', model: DOCTRINE_EMBEDDING_MODEL,
    pooling: 'cls', reason: 'empty_query' } };
  let vectors;
  try {
    vectors = await embedChunks(c.ai, embeddingChunks(args.q));
  } catch {
    return { semantic: { status: 'unavailable', model: DOCTRINE_EMBEDDING_MODEL, pooling: 'cls',
      reason: c.ai?.run ? 'embedding_failed' : 'binding_unavailable' } };
  }
  if (c.inTransaction) await c.query('savepoint doctrine_embedding_search');
  try {
    const found = await c.query(
      `select search_doctrine_semantic($1,$2::uuid,$3::text[],$4,$5,$6::vector[]) as result`,
      [args.q, actorId, args.content_classes || null, args.limit || 20,
        args.policy_id || null, vectors.map(vector => JSON.stringify(vector))]);
    const { hits, visible, embedded } = found.rows[0].result;
    if (c.inTransaction) await c.query('release savepoint doctrine_embedding_search');
    return { rows: hits, semantic: { model: DOCTRINE_EMBEDDING_MODEL, pooling: 'cls',
      status: embedded === visible ? 'ready' : embedded ? 'partial' : 'not_backfilled',
      visible_sections: visible, embedded_sections: embedded } };
  } catch {
    if (c.inTransaction) {
      await c.query('rollback to savepoint doctrine_embedding_search');
      await c.query('release savepoint doctrine_embedding_search');
    }
    return { semantic: { status: 'unavailable', model: DOCTRINE_EMBEDDING_MODEL, pooling: 'cls',
      reason: 'retrieval_failed' } };
  }
}

export async function embedDoctrineRevisions(c, revisions) {
  if (!revisions.length) return { status: 'ready', embedded: 0 };
  const chunks = revisions.flatMap(revision => embeddingChunks(revision.plain_text)
    .map((text, index) => ({ revisionId: revision.revision_id, index, text })));
  let vectors;
  try {
    vectors = await embedChunks(c.ai, chunks.map(chunk => chunk.text));
  } catch {
    return { status: 'unavailable', embedded: 0,
      reason: c.ai?.run ? 'embedding_failed' : 'binding_unavailable' };
  }
  await c.query('savepoint doctrine_embedding_write');
  try {
    const embedded = new Set();
    for (let i = 0; i < chunks.length; i++) {
      const result = await c.query(
        `insert into doctrine_embedding(revision_id,model,embedding,chunk_index)
         select r.id,$2,$3::vector,$4 from doctrine_revision r
           join doctrine_section s on s.current_revision_id=r.id and s.id=r.section_id
          where r.id=$1::uuid and s.status='active'
         on conflict (revision_id,chunk_index) do nothing returning revision_id`,
        [chunks[i].revisionId, EMBEDDING_VERSION, JSON.stringify(vectors[i]), chunks[i].index]);
      if (result.rows.length) embedded.add(chunks[i].revisionId);
    }
    await c.query('release savepoint doctrine_embedding_write');
    return { status: 'ready', embedded: embedded.size };
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
       left join (select distinct revision_id from doctrine_embedding where model=$3) e on e.revision_id=r.id
      where e.revision_id is null order by s.id limit $2`, [actorId, limit, EMBEDDING_VERSION]);
  const semantic = await embedDoctrineRevisions(c, pending.rows);
  const remaining = await c.query(
    `select count(*)::integer as remaining
       from current_retrievable_doctrine($1::uuid,null) c
       join doctrine_section s on s.id=c.section_id
       join doctrine_revision r on r.id=s.current_revision_id and r.section_id=s.id
       left join (select distinct revision_id from doctrine_embedding where model=$2) e on e.revision_id=r.id
      where e.revision_id is null`, [actorId, EMBEDDING_VERSION]);
  return { ok: true, embedded: semantic.embedded, remaining: remaining.rows[0].remaining,
    semantic: { ...semantic, model: DOCTRINE_EMBEDDING_MODEL, pooling: 'cls' } };
}
