import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import pg from 'pg';
import { TOOLS } from '../src/tools.js';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';

const MIGRATION = new URL('../../migrations/1056_drop_unused_indexes.sql', import.meta.url);

const DROPPED = [
  'public.event_decision_text_trgm',
  'public.retrieval_query_log_miss_idx',
  'public.building_addr_trgm',
  'ops.release_correlation_idx', 'public.session_work_kind_idx',
  'public.session_work_open_loop_idx',
];

const KEPT = [
  'public.session_work_last_seen_idx',
  'public.doctrine_section_title_search_idx', 'public.doctrine_document_title_search_idx',
  'public.retrieval_phrase_trgm_idx', 'public.retrieval_phrase_fts_idx',
  'public.doctrine_edge_source_idx', 'public.party_link_from_idx', 'public.deal_conflict_open_idx',
  'ops.release_authority_candidate_sha_uniq', 'ops.release_sha_idx', 'public.loop_item_block_idx',
  'public.loop_item_block_id_render_seq_key', 'public.event_correlation_idx',
  'public.memory_item_search_idx', 'public.source_capture_session_trgm',
  'public.doctrine_edge_target_idx', 'public.doctrine_edge_live_uq', 'public.party_link_via_idx',
  'public.party_link_from_to_kind_uidx', 'public.critical_date_open_due_idx',
  'public.party_link_deal_referrer_idx',
];

let fixture, c;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'unused-index-', snapshot: true });
  const connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  c = new pg.Client(connection);
  await c.connect();
});
after(async () => {
  try { await c?.end(); } finally { await fixture?.close(); }
});

const present = async names =>
  (await c.query('select n from unnest($1::text[]) n where to_regclass(n) is not null', [names])).rows.map(r => r.n);

test('migration 1056 drops exactly the reviewed indexes inside one transaction', async () => {
  assert.deepEqual(await present(DROPPED), DROPPED, 'snapshot must still carry every index the migration drops');
  const constraints = await c.query(`select oid, conindid from pg_constraint order by oid`);
  const candidates = await c.query(`select i.indexrelid::regclass::text as name
    from pg_index i where i.indexrelid = any($1::regclass[])
      and (i.indisunique or i.indisprimary or i.indisexclusion
        or exists (select 1 from pg_constraint c where c.conindid = i.indexrelid)
        or exists (select 1 from pg_constraint c where c.contype = 'f'
          and c.conrelid = i.indrelid and c.conkey[1] = i.indkey[0]))`, [DROPPED]);
  assert.deepEqual(candidates.rows, [], 'drop candidates must not support uniqueness or foreign keys');
  const indexes = await c.query('select indexrelid::regclass::text as name from pg_index order by indexrelid');
  await c.query('begin');
  await c.query(readFileSync(MIGRATION, 'utf8'));
  await c.query('commit');
  assert.deepEqual(await present(DROPPED), []);
  const remaining = new Set((await c.query('select indexrelid::regclass::text as name from pg_index')).rows.map(r => r.name));
  assert.deepEqual(indexes.rows.filter(r => !remaining.has(r.name)).map(r => r.name).sort(),
    DROPPED.map(n => n.replace(/^public\./, '')).sort());
  assert.deepEqual(await present(KEPT), KEPT);
  assert.deepEqual((await c.query('select oid, conindid from pg_constraint order by oid')).rows, constraints.rows);
  await c.query('begin');
  await c.query(readFileSync(MIGRATION, 'utf8'));
  await c.query('rollback');
});

test('search and lookup paths that touched the dropped columns still run', async () => {
  await c.query("select * from public.search_doctrine_situations('lease renewal notice', null, null, 20, null, true)");
  const result = await TOOLS['find-precedent'].handler(
    { query: c.query.bind(c) }, {}, { query: 'markdown database', limit: 5 });
  assert.equal(result.ok, true);
  await c.query("select id from session_work order by last_seen desc");
  await c.query("select id from party_link where from_party = gen_random_uuid()");
});
