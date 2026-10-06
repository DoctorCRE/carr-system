import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';
import { acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, existsSync } from 'node:fs';
import path from 'node:path';
import pg from 'pg';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { connectionRouteForTool } from '../src/mcp.js';

let bin;
for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
  try { bin = execFileSync(config, ['--bindir'], { encoding: 'utf8' }).trim(); } catch { continue; }
  if (existsSync(path.join(bin, 'postgres'))) break;
  bin = null;
}
const uuid = n => `aa000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const actor = { id: uuid(1), slug: 'joe', human: true };
const tool = TOOLS['read-doc-activity'];

test('activity feed executes store predicates, cursor serialization and selected database role',
  { skip: !bin && 'local PostgreSQL binaries unavailable' }, async t => {
  const dir = mkdtempSync('/tmp/doc-activity-');
  let running = false, c;
  const releaseBudget = await acquirePostgresFixtureGroup();
  try {
    execFileSync(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale'], { stdio: 'pipe' });
    execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h ''`, '-w', 'start'], { stdio: 'pipe' });
    running = true;
    c = new pg.Client({ host: dir, user: 'fixture', database: 'postgres' });
    await c.connect();
    await c.query('create role carr_writer; create role carr_reader; create role carr_jobs; create role carr_authority; create role carr_exporter; create role dot_reader;');
    const schema = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
    for (const name of ['actor', 'party', 'client', 'lead', 'vendor', 'deal', 'event']) {
      const table = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\n\\);`))?.[0];
      assert.ok(table, name);
      await c.query(table);
      const grants = schema.split('\n').filter(line => line.startsWith('grant ') && line.includes(`on table public.${name} to `));
      assert.ok(grants.length, `committed grants for ${name}`);
      for (const grant of grants) await c.query(grant);
    }
    await restoreEventIdentity(c, schema);
    await c.query("insert into actor(id,slug,display_name,kind,active) values ($1,'joe','Partner A','human',true),($2,'dell','Partner B','human',true)", [uuid(1), uuid(2)]);
    const seed = async (n, options = {}) => {
      const o = { at: '2026-10-01T15:00:00.123456Z', scope: 'none', tenant: 'carr-internal', cause: 'automation_job', partner: 'joe', type: 'deal', field: 'phase', old: { phase: 'research' }, next: { phase: 'legal' }, ...options };
      await c.query(`insert into event(id,actor_id,verb,subject_type,subject_id,field,old_value,new_value,recorded_at,occurred_at,cause,organization_tenant_id,personal_scope,sponsoring_human_slug)
        values ($1,$2,'patch-deal-field',$3,$4,$5,$6,$7,$8,$8,$9,$10,$11,$12)`,
        [uuid(n), uuid(o.partner === 'dell' ? 2 : 1), o.type, uuid(o.subject ?? n + 1000), o.field, o.old, o.next, o.at, o.cause, o.tenant, o.scope, o.partner]);
    };
    await t.test('SQL filters shared, own, other and unknown scopes; cause wins over human actor kind', async () => {
      for (const [n, options] of [[10, {}], [11, { scope: 'joe-personal' }], [12, { scope: 'dell-personal' }],
        [13, { scope: null }], [14, { tenant: 'other' }], [15, { cause: 'human_stated' }],
        [16, { partner: 'dell', type: 'draft', field: null }]]) await seed(n, options);
      for (const [i, cause] of ['learning_job', 'system', 'ingest_email', 'ingest_calendar', 'ingest_webhook', 'import_salesforce'].entries()) await seed(20+i, { cause });
      const read = args => tool.handler(c, actor, args);
      assert.deepEqual((await read({})).entries.map(e => e.id), [25,24,23,22,21,20,16,11,10].map(uuid));
      assert.deepEqual((await read({ partner: 'dell', record_type: 'draft' })).entries.map(e => e.id), [uuid(16)]);
      assert.deepEqual((await read({})).record_types, ['deal', 'draft']);
      assert.equal((await read({ since: '2026-10-02T00:00:00Z' })).entries.length, 0);
      assert.equal((await read({ until: '2026-10-01T15:00:00.123456Z' })).entries.length, 0);
    });
    await t.test('invalid JSON and timestamps refuse without querying the store', async () => {
      const neverQuery = { query() { assert.fail('invalid input reached PostgreSQL'); } };
      for (const args of [{ record_type: ['deal'] }, { record_type: null }, { limit: null },
        { cursor: { at: '2026-10-01T00:00:00Z', id: [uuid(30)] } },
        { since: '2026-02-30T00:00:00Z' }, { since: '2026-10-01T00:00:00' },
        { since: '2026-10-01T00:00:00-00:00' }]) {
        await assert.rejects(tool.handler(neverQuery, actor, args), /doc_activity_input_invalid/);
      }
      await seed(29, { at: '2026-10-01T15:00:00.123800Z' });
      assert.deepEqual((await tool.handler(c, actor, {
        since: '2026-10-01T15:00:00.123100Z', until: '2026-10-01T15:00:00.123900Z',
      })).entries.map(e => e.id), [29,25,24,23,22,21,20,16,11,10].map(uuid));
    });
    await t.test('microsecond and equal timestamp pages survive JSON round trips without omission', async () => {
      await c.query('truncate event');
      for (const [n, at] of [[30, '.123900'], [31, '.123800'], [32, '.123800'], [33, '.122000']]) await seed(n, { at: `2026-10-01T15:00:00${at}Z` });
      const ids = [], stamps = [];
      let cursor;
      do {
        const r = JSON.parse(JSON.stringify(await tool.handler(c, actor, { limit: 1, ...(cursor ? { cursor } : {}) })));
        ids.push(...r.entries.map(e => e.id)); stamps.push(...r.entries.map(e => e.at)); cursor = r.next_cursor;
      } while (cursor);
      assert.deepEqual(ids, [30,32,31,33].map(uuid));
      assert.match(stamps[0], /\.123900Z$/);
    });
    await t.test('SQL latest-field selection includes later human changes and safe structured inverses', async () => {
      await c.query('truncate event');
      await seed(40, { subject: 500, field: 'operating_state', old: { operating_state: { state: 'active', reason: null, note: null } }, next: { operating_state: { state: 'parked', reason: 'client_paused', note: 'Synthetic' } } });
      await seed(41, { subject: 500, field: 'operating_state', cause: 'human_stated', at: '2026-10-01T15:00:00.123457Z' });
      await seed(42, { field: 'attention', old: { attention: 'true' } });
      await seed(43, { old: { phase: { __sensitive_ref: 'hidden' } } });
      const r = await tool.handler(c, actor, {});
      const parked = r.entries.find(e => e.id === uuid(40));
      assert.equal(parked.undo.state, 'superseded');
      assert.deepEqual(parked.before, { state: 'active', reason: null, note: null });
      assert.equal(r.entries.find(e => e.id === uuid(42)).undo.state, 'unavailable');
      assert.ok(!JSON.stringify(r).includes('hidden'));
    });
    await t.test('registered role route reads committed event grants without broad reader access', async () => {
      await c.query('set role carr_reader');
      await assert.rejects(c.query('select * from event'), e => e.code === '42501');
      await c.query('reset role');
      assert.equal(connectionRouteForTool(tool), 'writer_read_only');
      await c.query('set role carr_writer');
      await c.query('begin read only');
      try { assert.equal((await executeRegisteredTool(c, actor, 'read-doc-activity', {})).ok, true); }
      finally { await c.query('rollback'); await c.query('reset role'); }
    });
    await t.test('empty store and failures at every query stage stay truthful', async () => {
      await c.query('truncate event');
      const r = await tool.handler(c, actor, {});
      assert.deepEqual(r.entries, []); assert.deepEqual(r.record_types, []); assert.equal(r.next_cursor, null);
      for (const stage of [':types', ':entries', ':clock']) {
        await assert.rejects(tool.handler({ query(sql, args) {
          if (sql.includes(stage)) throw new Error(`failure ${stage}`);
          return c.query(sql, args);
        } }, actor, {}), new RegExp(`failure ${stage}`));
      }
    });
  } finally {
    try {
      if (c) await c.end();
      if (running) execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-m', 'immediate', '-w', 'stop'], { stdio: 'pipe' });
    } finally {
      await releaseBudget();
    }
  }
});
