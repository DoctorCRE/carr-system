// Synthetic market history through the registered reader and disposable PostgreSQL.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { rebuildMarketTerms } from '../src/market-terms.ts';

const actor = { id: '00000000-0000-4000-8000-000000000001', slug: 'joe', human: true };
const migration = new URL('../../migrations/1087_market_terms.sql', import.meta.url);

test('market terms project confirmed history with explicit coverage', async t => {
  const db = await postgresDatabase('market-terms-');
  const c = db.client;
  const read = async (args = {}) => {
    await c.query('set role carr_reader');
    try { return await executeRegisteredTool(c, actor, 'read-market-terms', args); }
    finally { await c.query('reset role'); }
  };
  const term = async ({ entity = randomUUID(), value = '24', tenant = 'carr-internal',
    versions = ['synthetic-v1'], basis = 'human_review', start = '2024-01-01',
    end = null, kind = 'executed', classified = true, submarket = 'Synthetic North', use = 'medical' } = {}) => {
    const observations = versions.map((extractor_version, i) => ({
      id: `source-${i}`, fact_key: 'base_rent_per_sf', value, unit: 'USD/SF/year', expense_basis: 'nnn',
      source: { document_ref: randomUUID(), content_hash: String(i + 1).padStart(64, '0'),
        document_family_id: `family-${i}`, page_start: 1, page_end: 2, cited_text: 'Synthetic rent clause.' },
      method: 'text_layer', extractor_version, confidence_tier: 'auto_accept',
      extraction: { pass: `pass-${i}`, model: 'synthetic' },
    }));
    const recorded = (await c.query('select ops.record_fact_observations($1,$2,$3,$4,$5::jsonb) r',
      [tenant, actor.id, randomUUID(), entity, JSON.stringify(observations)])).rows[0].r;
    const version = (await c.query('select coalesce(max(version),0)::int v from ops.canonical_fact where organization_tenant_id=$1 and entity_ref=$2', [tenant, entity])).rows[0].v;
    const promoted = (await c.query("select ops.promote_fact($1,$2,$3,$4,'base_rent_per_sf',$5,$6::uuid[],$7) r",
      [tenant, actor.id, randomUUID(), entity, basis, recorded.observations.map(o => o.id), version])).rows[0].r;
    assert.equal(promoted.ok, true, JSON.stringify(promoted));
    const fact = promoted.canonical_fact;
    if (classified) await c.query(`insert into ops.market_term_context(canonical_fact_id,organization_tenant_id,
      submarket,use_type,term_kind,term_date,effective_from,effective_to,document_ref,reviewed_by_actor_id)
      values($1,$2,$3,$4,$5,$6,$6,$7,$8,$9)`,
    [fact.id, tenant, submarket, use, kind, start, end, observations[0].source.document_ref, actor.id]);
    return fact;
  };
  try {
    await c.query(await readFile(new URL('../../migrations/0001_init.sql', import.meta.url), 'utf8'));
    await c.query(`create schema ops; create role carr_reader; create role carr_writer;
      create role carr_authority; create role dot_reader;
      create function ops.scac_current_grant_contract() returns jsonb language sql as
      $$select '{"secdef_execute":[]}'::jsonb$$`);
    await c.query(await readFile(new URL('../../migrations/0983_fact_store.sql', import.meta.url), 'utf8'));
    await c.query(await readFile(new URL('../../migrations/1070_fact_review.sql', import.meta.url), 'utf8'));
    await c.query(await readFile(migration, 'utf8'));
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner')", [actor.id]);

    await t.test('empty results retain unknown archive coverage', async () => {
      const out = await read({ submarket: 'Synthetic North', use_type: 'medical' });
      assert.deepEqual(out.rows, []);
      assert.equal(out.empty, true);
      assert.equal(out.coverage.archive_documents, null);
      assert.equal(out.coverage.history_coverage, 'unknown');
      assert.equal(out.coverage.comparable_deals, 0);
      assert.equal(out.coverage.recorded_documents, 0);
      for (const args of [{ as_of: '2024-02-30' }, { as_of: 'invalid' }, { submarket: '' }, { use_type: null }])
        await assert.rejects(read(args), e => e.payload.error === 'validation_error');
    });

    await t.test('counter and executed terms carry evidence and separate comparable counts from source coverage', async () => {
      const counter = await term({ kind: 'counter' });
      const executed = await term();
      await term({ classified: false });
      const out = await read({ submarket: 'Synthetic North', use_type: 'medical' });
      assert.equal(out.rows.length, 2);
      assert.deepEqual(new Set(out.rows.map(r => r.term_kind)), new Set(['counter', 'executed']));
      const row = out.rows.find(r => r.canonical_fact_id === executed.id);
      assert.equal(row.value, '24');
      assert.equal(row.unit, 'USD/SF/year');
      assert.equal(row.expense_basis, 'nnn');
      assert.equal(row.term_date, '2024-01-01');
      assert.equal(row.confidence, 'human_review');
      assert.equal(row.sources.length, 1);
      assert.equal(row.sources[0].page_end, 2);
      assert.deepEqual(row.source_coverage, { supporting_observations: 1, supporting_documents: 1, unaffected_observations: 1 });
      assert.equal(out.coverage.comparable_deals, 2);
      assert.equal(out.coverage.canonical_receipts, 3);
      assert.equal(out.coverage.recorded_documents, 3);
      assert.equal(out.coverage.unclassified_receipts, 1);
      for (const args of [{ submarket: 'missing' }, { use_type: 'dental' }]) {
        const empty = await read(args);
        assert.equal(empty.empty, true);
        assert.deepEqual(empty.rows, []);
        assert.equal(empty.coverage.recorded_documents, 3);
        assert.equal(empty.coverage.comparable_deals, 0);
      }
      assert.ok(out.rows.some(r => r.canonical_fact_id === counter.id));
    });

    await t.test('tenant snapshots isolate rows, metadata, counts and quarantine in both directions', async () => {
      const tenant = 'synthetic-other-tenant';
      const before = await read();
      let other = rebuildMarketTerms((await c.query('select ops.market_terms_snapshot($1) snapshot', [tenant])).rows[0].snapshot);
      assert.deepEqual(other.rows, []);
      assert.equal(other.coverage.recorded_documents, 0);
      const fact = await term({ tenant, submarket: 'Synthetic South' });
      await c.query('set role carr_reader');
      try {
        other = rebuildMarketTerms((await c.query('select ops.market_terms_snapshot($1) snapshot', [tenant])).rows[0].snapshot);
      } finally { await c.query('reset role'); }
      assert.deepEqual(other.rows.map(r => r.canonical_fact_id), [fact.id]);
      assert.equal(other.coverage.recorded_documents, 1);
      assert.deepEqual(await read(), before);
      await assert.rejects(read({ organization_tenant_id: tenant }), e => e.payload.error === 'caller_authority_field_forbidden');
    });

    await t.test('business effective periods distinguish amendments from simultaneous deals and receipt order', async () => {
      const entity = randomUUID();
      await term({ entity, value: '20', start: '2020-01-01', submarket: 'Synthetic Period' });
      await term({ entity, value: '22', start: '2022-01-01', submarket: 'Synthetic Period' });
      const corrected = await term({ entity, value: '23', start: '2022-01-01', submarket: 'Synthetic Period' });
      const query = { submarket: 'Synthetic Period' };
      const out = await read(query);
      assert.deepEqual(out.rows.map(r => [r.value, r.effective_from, r.effective_to]),
        [['20', '2020-01-01', '2022-01-01'], ['23', '2022-01-01', null]]);
      assert.equal(out.coverage.comparable_deals, 1);
      assert.equal(out.coverage.comparable_rows, 2);
      assert.equal((await read({ ...query, as_of: '2021-12-31' })).rows[0].value, '20');
      assert.deepEqual((await read({ ...query, as_of: '2022-01-01' })).rows.map(r => r.canonical_fact_id), [corrected.id]);
      assert.equal((await read({ ...query, as_of: '2019-12-31' })).empty, true);
      const snapshot = (await c.query("select ops.market_terms_snapshot('carr-internal') snapshot")).rows[0].snapshot;
      assert.deepEqual(rebuildMarketTerms(snapshot, query), out);
      snapshot.receipts.reverse();
      assert.deepEqual(rebuildMarketTerms(snapshot, query), out);
      assert.deepEqual(await read(query), out);
    });

    await t.test('a newer contextless canonical version cannot leave an older term current', async () => {
      const entity = randomUUID(), submarket = 'Synthetic Correction';
      await term({ entity, submarket, value: '20' });
      const before = await read({ submarket });
      await term({ entity, value: '30', classified: false });
      const after = await read({ submarket });
      assert.deepEqual(after.rows, []);
      assert.equal(after.coverage.unclassified_receipts, before.coverage.unclassified_receipts + 1);
      assert.equal(after.coverage.unsupported_receipts, before.coverage.unsupported_receipts + 1);
    });

    await t.test('extractor quarantine recomputes support while preserving human review and unaffected independent evidence', async () => {
      const submarket = 'Synthetic Quarantine';
      const human = await term({ submarket, versions: ['synthetic-bad'] });
      const lost = await term({ submarket, versions: ['synthetic-bad', 'synthetic-good'], basis: 'independent_agreement' });
      const kept = await term({ submarket, versions: ['synthetic-bad', 'synthetic-good', 'synthetic-other'], basis: 'independent_agreement' });
      const query = { submarket };
      const before = await read(query);
      assert.equal(before.rows.length, 3);
      await c.query("insert into ops.market_term_quarantine values('synthetic-other-tenant','synthetic-good','Synthetic foreign quarantine')");
      assert.deepEqual(await read(query), before);
      await c.query("insert into ops.market_term_quarantine values('carr-internal','synthetic-bad','Synthetic failed extractor')");
      const after = await read(query);
      assert.deepEqual(new Set(after.rows.map(r => r.canonical_fact_id)), new Set([human.id, kept.id]));
      assert.ok(!after.rows.some(r => r.canonical_fact_id === lost.id));
      assert.equal(after.coverage.unsupported_receipts, before.coverage.unsupported_receipts + 1);
      assert.equal(after.rows.find(r => r.canonical_fact_id === human.id).sources[0].quarantined, true);
      assert.equal(after.rows.find(r => r.canonical_fact_id === kept.id).source_coverage.unaffected_observations, 2);
      assert.deepEqual(after.coverage.quarantined_extractor_versions, ['synthetic-bad']);
      assert.equal(after.coverage.canonical_receipts, before.coverage.canonical_receipts);
      assert.equal(after.coverage.recorded_documents, before.coverage.recorded_documents);
    });

    await t.test('quarantining an amendment does not reopen the superseded period', async () => {
      const entity = randomUUID(), submarket = 'Synthetic Closed';
      await term({ entity, submarket, value: '20', start: '2020-01-01' });
      await term({ entity, submarket, value: '20', start: '2022-01-01', basis: 'independent_agreement',
        versions: ['synthetic-bad', 'synthetic-good'] });
      assert.equal((await read({ submarket, as_of: '2023-01-01' })).empty, true);
      const earlier = await read({ submarket, as_of: '2021-01-01' });
      assert.equal(earlier.rows[0].effective_to, '2022-01-01');
    });

    await t.test('runtime roles cannot write context or quarantine or read source tables', async () => {
      for (const role of ['carr_reader', 'carr_writer', 'carr_authority', 'dot_reader']) {
        await c.query(`set role ${role}`);
        try {
          for (const table of ['market_term_context', 'market_term_quarantine']) {
            await assert.rejects(c.query(`select * from ops.${table}`), { code: '42501' });
            await assert.rejects(c.query(`insert into ops.${table} default values`), { code: '42501' });
          }
        } finally { await c.query('reset role'); }
      }
      const unclassified = await term({ classified: false });
      await assert.rejects(c.query(`insert into ops.market_term_context
        select f.id,'synthetic-other-tenant','Synthetic North','medical','executed','2024-01-01','2024-01-01',
          null,o.document_ref,$2 from ops.canonical_fact f join ops.fact_observation o on o.id=any(f.observation_ids)
          where f.id=$1`, [unclassified.id, actor.id]), { code: '23514' });
      await assert.rejects(c.query("update ops.market_term_context set use_type='dental'"), { code: '23514' });
    });

    await t.test('rollback disables the reader and preserves confirmed facts and classification receipts', async () => {
      const facts = (await c.query('select id from ops.canonical_fact order by id')).rows;
      const observations = (await c.query('select id from ops.fact_observation order by id')).rows;
      const contexts = (await c.query('select * from ops.market_term_context order by canonical_fact_id')).rows;
      const before = await read();
      await c.query('revoke execute on function ops.market_terms_snapshot(text) from carr_reader');
      await assert.rejects(read(), e => e.payload.error === 'database_refused_the_statement' && e.payload.sqlstate === '42501');
      assert.deepEqual((await c.query('select id from ops.canonical_fact order by id')).rows, facts);
      assert.deepEqual((await c.query('select id from ops.fact_observation order by id')).rows, observations);
      assert.deepEqual((await c.query('select * from ops.market_term_context order by canonical_fact_id')).rows, contexts);
      await c.query('grant execute on function ops.market_terms_snapshot(text) to carr_reader');
      assert.deepEqual(await read(), before);
    });
  } finally { await db.close(); }
});
