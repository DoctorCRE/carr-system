// Prove migration 1039 drops only the reviewed never-scanned indexes and the search paths still run.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';

const MIGRATION = new URL('../../migrations/1039_drop_unused_indexes.sql', import.meta.url);

const DROPPED = [
  'public.event_decision_text_trgm', 'public.doctrine_section_title_search_idx',
  'public.doctrine_document_title_search_idx', 'public.retrieval_query_log_miss_idx',
  'public.retrieval_phrase_trgm_idx', 'public.retrieval_phrase_fts_idx', 'public.building_addr_trgm',
  'ops.release_correlation_idx', 'public.deal_conflict_open_idx', 'public.session_work_kind_idx',
  'public.session_work_last_seen_idx', 'public.session_work_open_loop_idx',
  'public.party_link_from_idx', 'public.doctrine_edge_source_idx',
];

// Reviewed and kept: constraints, uniqueness rules, index-shaped search or FK lookups, and indexes
// younger than the two-week evidence window.
const KEPT = [
  'ops.release_authority_candidate_sha_uniq', 'ops.release_sha_idx', 'public.loop_item_block_idx',
  'public.loop_item_block_id_render_seq_key', 'public.event_correlation_idx',
  'public.memory_item_search_idx', 'public.source_capture_session_trgm',
  'public.doctrine_edge_target_idx', 'public.doctrine_edge_live_uq', 'public.party_link_via_idx',
  'public.party_link_from_to_kind_uidx', 'public.critical_date_open_due_idx',
  'public.party_link_deal_referrer_idx',
];

let fixture, admin, database, c;
before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin, connection;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
    assert.equal(url.search, '', 'fixture connection must not override its loopback host');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    database = `unused_index_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${database}" template template0`);
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'unused-index-', pgCtl: `${bin}/pg_ctl` });
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
    connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  }
  c = new pg.Client(connection);
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname];
  if (ciDsn) await promisify(execFile)('psql', [connection.connectionString, ...restore], { maxBuffer: 2 * 1024 * 1024 });
  else await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

const present = async names =>
  (await c.query('select n from unnest($1::text[]) n where to_regclass(n) is not null', [names])).rows.map(r => r.n);

test('migration 1039 drops exactly the reviewed indexes inside one transaction', async () => {
  assert.deepEqual(await present(DROPPED), DROPPED, 'snapshot must still carry every index the migration drops');
  await c.query('begin');
  await c.query(readFileSync(MIGRATION, 'utf8'));
  await c.query('commit');
  assert.deepEqual(await present(DROPPED), []);
  assert.deepEqual(await present(KEPT), KEPT);
});

test('search and lookup paths that touched the dropped columns still run', async () => {
  await c.query("select * from public.search_doctrine_situations('lease renewal notice', null, null, 20, null, true)");
  await c.query(`select decision_id from v_precedent
                  where word_similarity('markdown database', haystack) >= 0.3 limit 5`);
  await c.query("select id from session_work order by last_seen desc");
  await c.query("select id from party_link where from_party = gen_random_uuid()");
});
