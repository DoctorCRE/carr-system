// Test fact store promotion, isolation, and grants on PostgreSQL.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { CONTRACTS } from '../src/fact-observations.ts';
import { SCAC_MUTATION_CURRENT_DB_GRANTS } from '../src/scac-mutation-registry.current.generated.js';

// WR-000039 E09a. Synthetic tenants, entities, documents, and actors only.
const partner = { id: '00000000-0000-4000-8000-000000000001', slug: 'joe', human: true };
const job = { id: '00000000-0000-4000-8000-000000000002', slug: 'codex', human: false, sponsoring_human_slug: 'joe' };
const FACT_FUNCTIONS = /^ops\.(record_fact_observations|promote_fact|list_fact_conflicts|record_human_fact|list_fact_review|resolve_fact_review)\(/;
const hash = n => n.toString(16).padStart(64, '0');

const observation = (id, patch = {}) => {
  const { source = {}, ...rest } = patch;
  return {
    id, fact_key: 'base_rent_per_sf', value: '24', unit: 'USD/SF/year', expense_basis: 'nnn',
    source: { document_ref: `doc-${id}`, page_start: 2, page_end: 2, cited_text: `Synthetic rent clause ${id}.`,
      content_hash: hash(id.length * 7919 + id.charCodeAt(0)), document_family_id: `family-${id}`, ...source },
    method: 'text_layer', extractor_version: 'synthetic-v1', confidence_tier: 'auto_accept',
    extraction: { pass: `pass-${id}`, model: 'synthetic-model' }, ...rest,
  };
};

test('E09a fact store: lineage-proven promotion, conflicts, tenancy, idempotency, retention', async t => {
  const db = await postgresDatabase('fact-store-');
  const c = db.client;
  const as = async (role, actor, verb, args) => {
    await c.query(`set role ${role}`);
    try { return await executeRegisteredTool(c, actor, verb, args); }
    finally { await c.query('reset role'); }
  };
  const record = (entity_ref, observations, actor = job, idempotency_key = randomUUID()) =>
    as('carr_writer', actor, 'record-fact-observations', { idempotency_key, entity_ref, observations });
  const promote = (entity_ref, observation_ids, patch = {}, actor = job) => as('carr_writer', actor, 'promote-fact', {
    idempotency_key: randomUUID(), entity_ref, fact_key: 'base_rent_per_sf', basis: 'independent_agreement',
    observation_ids, base_version: 0, ...patch });
  const conflicts = (args = {}) => as('carr_reader', partner, 'list-fact-conflicts', args);
  const ids = out => out.observations.map(o => o.id);
  try {
    await c.query(await readFile(new URL('../../migrations/0001_init.sql', import.meta.url), 'utf8'));
    await c.query('create schema ops; create role carr_reader; create role carr_writer; create role carr_authority; create role dot_reader; grant usage on schema ops to dot_reader; alter default privileges in schema ops grant select on tables to dot_reader');
    // Grants registered by migrations after 0983 are not in the predecessor; they follow the fact-store grants.
    const LATER_FUNCTIONS = /^ops\.(register_client_portal|adopt_rule|reap_expired_runs|market_terms_snapshot)\(/;
    const predecessor = { ...SCAC_MUTATION_CURRENT_DB_GRANTS,
      secdef_execute: SCAC_MUTATION_CURRENT_DB_GRANTS.secdef_execute.filter(g => !FACT_FUNCTIONS.test(g.signature) && !LATER_FUNCTIONS.test(g.signature)) };
    await c.query(`create function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as $f$select ${c.escapeLiteral(JSON.stringify(predecessor))}::jsonb$f$`);
    await c.query(await readFile(new URL('../../migrations/0983_fact_store.sql', import.meta.url), 'utf8'));
    await c.query(await readFile(new URL('../../migrations/1070_fact_review.sql', import.meta.url), 'utf8'));
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner'),($2,'codex','automation','Synthetic job')", [partner.id, job.id]);

    await t.test('partners record a sourced term from an existing deal attachment', async () => {
      const deal = randomUUID(), document = randomUUID();
      await c.query("insert into attachment(id,subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by) values($1,'deal',$2,$3,'synthetic.pdf','application/pdf',$4,100,$5)",
        [document, deal, `synthetic/${document}`, hash(39), partner.id]);
      const out = await as('carr_writer', partner, 'record-human-fact', {
        idempotency_key: randomUUID(), entity_ref: deal,
        observation: { fact_key: 'base_rent_per_sf', value: '24', unit: 'USD/SF/year', expense_basis: 'nnn', source: { document_ref: document, page_start: 1, page_end: 1, cited_text: 'Synthetic clause.' } },
      });
      assert.equal(out.ok, true);
      assert.equal(out.observation.method, 'human');
      assert.equal(out.observation.source.content_hash, hash(39));
    });

    await t.test('review queue pages every pending field once, including single review tiers', async () => {
      for (const [entity, inputs] of [
        ['queue-a', [observation('a', { confidence_tier: 'verify_band' })]],
        ['queue-b', [observation('a', { confidence_tier: 'human_review' })]],
        ['queue-c', [observation('a'), observation('b', { value: '30' }), observation('c', { value: '31' })]],
        ['queue-d', [observation('a', { method: 'ocr' })]],
        ['queue-e', [observation('a')]],
      ]) await record(entity, inputs);
      const seen = [];
      let cursor;
      do {
        const page = await as('carr_reader', partner, 'list-fact-review', { limit: 1, ...(cursor ? { cursor } : {}) });
        seen.push(...page.items.map(i => i.entity_ref));
        cursor = page.next_cursor;
      } while (cursor);
      assert.deepEqual(seen.filter(e => e.startsWith('queue-')), ['queue-a', 'queue-b', 'queue-c', 'queue-d']);
      assert.equal(new Set(seen).size, seen.length);
      const page = await as('carr_reader', partner, 'list-fact-review', { entity_ref: 'queue-c' });
      assert.equal(page.items.length, 1);
      assert.equal(page.items[0].observations.length, 3);
      assert.deepEqual(page.items[0].reasons, ['conflict']);
    });

    await t.test('approve, correct and reject retain evidence, receipts and stale guards', async () => {
      const deal = randomUUID(), document = randomUUID();
      await c.query("insert into attachment(id,subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by) values($1,'deal',$2,$3,'synthetic.pdf','application/pdf',$4,100,$5)",
        [document, deal, `synthetic/${document}`, hash(40), partner.id]);
      const inputs = [observation('lease'), observation('counter', { value: '25' }), observation('amendment', { value: '26' })];
      await record(deal, inputs);
      const read = async () => (await as('carr_reader', partner, 'list-fact-review', { entity_ref: deal, include_resolved: true })).items[0];
      const resolve = (item, patch = {}, actor = partner) => as('carr_writer', actor, 'resolve-fact-review', {
        idempotency_key: randomUUID(), entity_ref: deal, fact_key: item.fact_key,
        base_version: item.canonical_version, review_version: item.review_version,
        decision: 'approve', observation_id: item.observations[2].id, reason: 'Synthetic partner review.', ...patch });
      const initial = await read();
      const approved = await resolve(initial);
      assert.equal(approved.canonical_fact.normalized_value, '26');
      const settled = await read();
      assert.equal(settled.pending, false);
      assert.equal(settled.observations.length, 3);
      assert.equal(settled.review_history[0].decision, 'approve');
      await assert.rejects(resolve(initial), e => e.payload.error === 'version_conflict');
      const correction = { fact_key: 'base_rent_per_sf', value: '27', unit: 'USD/SF/year', expense_basis: 'nnn',
        source: { document_ref: document, page_start: 3, page_end: 3, cited_text: 'Synthetic corrected clause.' } };
      const key = randomUUID();
      const corrected = await resolve(settled, { idempotency_key: key, decision: 'correct', observation_id: undefined, correction });
      assert.equal(corrected.canonical_fact.normalized_value, '27');
      assert.equal((await resolve(settled, { idempotency_key: key, decision: 'correct', observation_id: undefined, correction })).replayed, true);
      const after = await read();
      assert.equal(after.observations.length, 4);
      assert.equal(after.observations.find(o => o.id === corrected.canonical_fact.observation_ids[0]).method, 'human');
      assert.deepEqual(after.canonical_history.map(h => h.normalized_value), ['26', '27']);
      assert.deepEqual(after.review_history.map(h => h.decision), ['approve', 'correct']);
      await record(deal, [observation('later', { value: '28' })]);
      await assert.rejects(resolve(after, { decision: 'reject', observation_id: undefined }), e => e.payload.error === 'version_conflict');
      const reopened = await read();
      assert.equal(reopened.pending, true);
      const rejected = await resolve(reopened, { decision: 'reject', observation_id: undefined });
      assert.equal(rejected.ok, true);
      const final = await read();
      assert.equal(final.pending, false);
      assert.deepEqual((await conflicts({ entity_ref: deal })).conflicts, []);
      assert.equal(final.observations.length, 5);
      assert.equal(final.canonical_fact.normalized_value, '27');
      assert.deepEqual(final.review_history.map(h => h.decision), ['approve', 'correct', 'reject']);
      await assert.rejects(resolve(reopened, {}, job), e => /human_only/.test(e.payload.error));
      for (const sql of ['delete from ops.fact_review_decision', "update ops.fact_review_decision set reason='changed'"])
        await assert.rejects(c.query(sql), { code: '23514' });
    });

    await t.test('human entries refuse another deal, tenant, deleted document and stale review evidence', async () => {
      const deal = randomUUID(), document = randomUUID();
      await c.query("insert into attachment(id,subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by) values($1,'deal',$2,$3,'synthetic.pdf','application/pdf',$4,100,$5)",
        [document, deal, `synthetic/${document}`, hash(41), partner.id]);
      const observation = { fact_key: 'term_months', value: '5', unit: 'years',
        source: { document_ref: document, page_start: 1, page_end: 1, cited_text: 'Synthetic term.' } };
      const entry = entity_ref => as('carr_writer', partner, 'record-human-fact', { idempotency_key: randomUUID(), entity_ref, observation });
      await assert.rejects(entry(randomUUID()), e => e.payload.error === 'document_not_found');
      const foreign = (await c.query('select ops.record_human_fact($1,$2,$3,$4,$5::jsonb) r',
        ['other-tenant', partner.id, randomUUID(), deal, JSON.stringify(observation)])).rows[0].r;
      assert.equal(foreign.error, 'document_not_found');
      const first = await entry(deal);
      const item = (await as('carr_reader', partner, 'list-fact-review', { entity_ref: deal })).items[0];
      const approved = await as('carr_writer', partner, 'resolve-fact-review', { idempotency_key: randomUUID(), entity_ref: deal,
        fact_key: 'term_months', base_version: 0, review_version: item.review_version, decision: 'approve',
        observation_id: first.observation.id, reason: 'Synthetic approval.' });
      assert.equal(approved.canonical_fact.normalized_value, '60');
      await c.query('update attachment set deleted_at=now() where id=$1', [document]);
      await assert.rejects(entry(deal), e => e.payload.error === 'document_not_found');
    });

    await t.test('concurrent rejections admit one receipt and citation expiry stays honest', async () => {
      await record('review-race', [observation('a', { confidence_tier: 'verify_band' })]);
      await c.query("set session_replication_role=replica; update ops.fact_observation set cited_text_expires_at=now()-interval '1 second' where entity_ref='review-race'; set session_replication_role=origin");
      const item = (await as('carr_reader', partner, 'list-fact-review', { entity_ref: 'review-race' })).items[0];
      assert.equal(item.observations[0].source.cited_text, null);
      assert.match(item.observations[0].source.cited_text_sha256, /^[a-f0-9]{64}$/);
      const outcomes = await Promise.allSettled(Array.from({ length: 3 }, async () => {
        const { default: pg } = await import('pg');
        const other = new pg.Client(c.connectionParameters);
        await other.connect();
        try {
          await other.query('set role carr_writer');
          return await executeRegisteredTool(other, partner, 'resolve-fact-review', { idempotency_key: randomUUID(),
            entity_ref: 'review-race', fact_key: item.fact_key, base_version: 0, review_version: item.review_version,
            decision: 'reject', reason: 'Synthetic rejection.' });
        } finally { await other.end(); }
      }));
      assert.equal(outcomes.filter(o => o.status === 'fulfilled' && o.value.ok).length, 1);
      assert.equal(outcomes.filter(o => o.status === 'rejected' && o.reason.payload.error === 'version_conflict').length, 2);
      const page = await as('carr_reader', partner, 'list-fact-review', { limit: 1, include_resolved: true });
      await assert.rejects(as('carr_reader', partner, 'list-fact-review', { cursor: page.next_cursor }), e => e.payload.error === 'invalid_cursor');
      await assert.rejects(as('carr_reader', partner, 'list-fact-review', { cursor: 'invalid' }), e => e.payload.error === 'invalid_cursor');
      await assert.rejects(as('carr_reader', partner, 'list-fact-review', { cursor: 'null' }), e => e.payload.error === 'invalid_cursor');
    });

    await t.test('the SQL normalization contract equals the comparison contract', async () => {
      const rows = (await c.query('select * from ops.fact_key_contract order by fact_key')).rows;
      assert.deepEqual(Object.fromEntries(rows.map(r => [r.fact_key, { units: r.unit_multipliers, unit: r.canonical_unit,
        ...(r.expense_basis_required ? { expenseBasis: true } : {}) }])), CONTRACTS);
    });

    await t.test('the grant contract registers exactly the installed fact-store definers', async () => {
      const installedContract = (await c.query('select ops.scac_current_grant_contract() as contract')).rows[0].contract;
      installedContract.secdef_execute.push(...SCAC_MUTATION_CURRENT_DB_GRANTS.secdef_execute.filter(g => LATER_FUNCTIONS.test(g.signature)));
      assert.deepEqual({ ...installedContract, secdef_execute: [...installedContract.secdef_execute].sort((a,b) => a.signature.localeCompare(b.signature)) },
        { ...SCAC_MUTATION_CURRENT_DB_GRANTS, secdef_execute: [...SCAC_MUTATION_CURRENT_DB_GRANTS.secdef_execute].sort((a,b) => a.signature.localeCompare(b.signature)) });
      const installed = (await c.query(`select p.oid::regprocedure::text sig,
          n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')' signature,
          p.proconfig,p.provolatile,acl.grantee::regrole::text grantee
        from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        cross join lateral aclexplode(p.proacl) acl
       where n.nspname='ops' and p.prosecdef and acl.privilege_type='EXECUTE' and acl.grantee<>p.proowner
      `)).rows;
      assert.deepEqual(installed.map(r => [r.signature, r.grantee, r.proconfig, r.provolatile]).sort((a, b) => a[0].localeCompare(b[0])),
        SCAC_MUTATION_CURRENT_DB_GRANTS.secdef_execute.filter(g => FACT_FUNCTIONS.test(g.signature))
          .map(g => [g.signature, g.grantee, g.config, g.volatility]).sort((a, b) => a[0].localeCompare(b[0])));
    });

    await t.test('runtime roles reach the store only through its definers', async () => {
      const ungranted = { carr_writer: "select ops.list_fact_conflicts('t',null,1)",
        carr_reader: "select ops.promote_fact('t',null,null,null,null,null,null,null)",
        dot_reader: "select ops.record_fact_observations('t',null,null,null,null)" };
      for (const [role, call] of Object.entries(ungranted)) {
        await c.query(`set role ${role}`);
        try {
          for (const table of ['ops.fact_observation', 'ops.canonical_fact', 'ops.fact_observation_batch', 'ops.fact_key_contract'])
            await assert.rejects(c.query(`select * from ${table}`), { code: '42501' });
          await assert.rejects(c.query(call), { code: '42501' }, role);
        } finally { await c.query('reset role'); }
      }
    });

    await t.test('the definers follow the canonical trusted search path policy', async () => {
      const policy = (await readFile(new URL('./security-definer-search-path-postgres.sql', import.meta.url), 'utf8'))
        .split('-- Negatives exercise')[0].replace(/^\\set.*$/gm, '');
      try { await c.query(policy); } finally { await c.query('rollback'); }
    });

    await t.test('recording is the extraction door: idempotent, never canonical, refused to partners', async () => {
      const key = randomUUID();
      const first = await record('idem-deal', [observation('a'), observation('b')], job, key);
      assert.equal(first.ok, true);
      assert.equal(first.replayed, false);
      assert.equal(first.canonical, false);
      assert.deepEqual(first.observations.map(o => o.observation_ref), ['a', 'b']);
      const again = await record('idem-deal', [observation('a'), observation('b')], job, key);
      assert.equal(again.replayed, true);
      assert.deepEqual(ids(again), ids(first));
      await assert.rejects(record('idem-deal', [observation('a', { value: '25' })], job, key), e => e.payload.error === 'key_reuse');
      await assert.rejects(record('idem-deal', [observation('c')], partner), e => e.payload.error === 'not_in_profile');
      assert.equal((await c.query("select count(*)::int n from ops.fact_observation where entity_ref='idem-deal'")).rows[0].n, 2);
      assert.equal((await c.query("select count(*)::int n from ops.canonical_fact where entity_ref='idem-deal'")).rows[0].n, 0);
    });

    await t.test('two proven-independent agreeing observations promote with a receipt', async () => {
      const obs = await record('agree-deal', [observation('lease'), observation('counter', { value: '2', unit: 'USD/SF/month' })]);
      const out = await promote('agree-deal', ids(obs));
      assert.equal(out.ok, true);
      assert.equal(out.canonical_fact.version, 1);
      assert.equal(out.canonical_fact.normalized_value, '24');
      assert.equal(out.canonical_fact.expense_basis, 'nnn');
      assert.equal(out.canonical_fact.basis, 'independent_agreement');
      assert.equal(out.canonical_fact.promoted_by_actor_id, job.id);
      assert.deepEqual([...out.canonical_fact.observation_ids].sort(), [...ids(obs)].sort());
    });

    await t.test('a single high-confidence observation is never promoted by rule', async () => {
      const obs = await record('single-deal', [observation('only')]);
      assert.deepEqual(await promote('single-deal', ids(obs)), { ok: false, error: 'independence_not_proven', schema_version: 'fact-promotion.v1' });
    });

    await t.test('the five independence denial fixtures refuse rule promotion', async () => {
      const denied = {
        'one-family': [observation('x'), observation('y', { source: { document_family_id: 'family-x' } })],
        'duplicate-document': [observation('x'), observation('y', { source: { content_hash: observation('x').source.content_hash } })],
        'common-upstream': [observation('x', { source: { derivation_chain: ['upstream-loi'] } }),
          observation('y', { source: { derivation_chain: ['upstream-loi'] } })],
        'correlated-extractor': [observation('x', { extraction: { pass: 'schema', model: 'm' } }),
          observation('y', { extraction: { pass: 'schema', model: 'm' } })],
        'circular-support': [observation('x'), observation('y', { source: { derivation_chain: ['family-x'] } })],
        'unproven-lineage': [observation('x'), observation('y', { source: { document_family_id: undefined } })],
      };
      for (const [entity, inputs] of Object.entries(denied)) {
        for (const input of inputs) if (input.source.document_family_id === undefined) delete input.source.document_family_id;
        const out = await promote(entity, ids(await record(entity, inputs)));
        assert.equal(out.error, 'independence_not_proven', entity);
      }
      const foreign = (await c.query(`select ops.record_fact_observations('synthetic-other-tenant',$1,$2,'tenant-deal',$3::jsonb) r`,
        [job.id, randomUUID(), JSON.stringify([observation('x'), observation('y')])])).rows[0].r;
      await assert.rejects(promote('tenant-deal', foreign.observations.map(o => o.id)), e => e.payload.error === 'not_found');
      const local = await record('tenant-deal', [observation('z')]);
      await assert.rejects(promote('tenant-deal', [...ids(local), foreign.observations[0].id]), e => e.payload.error === 'not_found');
      assert.deepEqual((await conflicts({ entity_ref: 'tenant-deal' })).conflicts, []);
    });

    await t.test('OCR, verify-band, and human-review tiers are not rule eligible', async () => {
      for (const patch of [{ method: 'ocr' }, { confidence_tier: 'verify_band' }, { confidence_tier: 'human_review' }]) {
        const entity = `tier-${Object.values(patch)[0]}`;
        const out = await promote(entity, ids(await record(entity, [observation('p', patch), observation('q', patch)])));
        assert.equal(out.error, 'observation_not_rule_eligible', entity);
      }
    });

    await t.test('a seeded two-source conflict is listed and unpromotable until a partner decides', async () => {
      const obs = await record('conflict-deal', [observation('lease'), observation('counter'), observation('amendment', { value: '26' })]);
      const [lease, counter, amendment] = ids(obs);
      assert.equal((await promote('conflict-deal', [lease, counter])).error, 'unresolved_conflict');
      assert.equal((await promote('conflict-deal', [lease, amendment])).error, 'observations_disagree');
      const listed = (await conflicts({ entity_ref: 'conflict-deal' })).conflicts;
      assert.equal(listed.length, 1);
      assert.equal(listed[0].canonical_version, 0);
      assert.deepEqual(listed[0].observations.map(o => [o.observation_ref, o.normalized_value, o.source.cited_text]),
        [['lease', '24', 'Synthetic rent clause lease.'], ['counter', '24', 'Synthetic rent clause counter.'], ['amendment', '26', 'Synthetic rent clause amendment.']]);
      await assert.rejects(promote('conflict-deal', [amendment], { basis: 'human_review' }, job),
        e => e.payload.error === 'human_only_verb_requires_verified_partner');
      const direct = (await c.query(`select ops.promote_fact('carr-internal',$1,$2,'conflict-deal','base_rent_per_sf','human_review',$3::uuid[],0) r`,
        [job.id, randomUUID(), [amendment]])).rows[0].r;
      assert.equal(direct.error, 'human_review_requires_one_observation_and_a_human');
      assert.equal((await promote('conflict-deal', [lease, counter], { basis: 'human_review' }, partner)).error,
        'human_review_requires_one_observation_and_a_human');
      await c.query('update actor set active=false where id=$1', [partner.id]);
      try {
        assert.equal((await promote('conflict-deal', [amendment], { basis: 'human_review' }, partner)).error,
          'human_review_requires_one_observation_and_a_human');
      } finally { await c.query('update actor set active=true where id=$1', [partner.id]); }
      const decided = await promote('conflict-deal', [amendment], { basis: 'human_review' }, partner);
      assert.equal(decided.canonical_fact.normalized_value, '26');
      assert.equal(decided.canonical_fact.promoted_by_actor_id, partner.id);
      assert.deepEqual((await conflicts({ entity_ref: 'conflict-deal' })).conflicts, []);
      await record('conflict-deal', [observation('later', { value: '27' })]);
      assert.equal((await conflicts({ entity_ref: 'conflict-deal' })).conflicts[0].canonical_version, 1);
    });

    await t.test('promotion is base_version guarded, idempotent, and supersedes append-only', async () => {
      const obs = await record('version-deal', [observation('a'), observation('b'), observation('c', { value: '24.00' })]);
      const [a, b, cc] = ids(obs);
      const key = randomUUID();
      const first = await promote('version-deal', [a, b], { idempotency_key: key });
      assert.equal(first.canonical_fact.version, 1);
      const replay = await promote('version-deal', [a, b], { idempotency_key: key });
      assert.equal(replay.replayed, true);
      assert.equal(replay.canonical_fact.id, first.canonical_fact.id);
      await assert.rejects(promote('version-deal', [a, cc], { idempotency_key: key }), e => e.payload.error === 'key_reuse');
      await assert.rejects(promote('version-deal', [a, cc]), e => e.payload.error === 'version_conflict' && e.payload.current_version === 1);
      const second = await promote('version-deal', [a, cc], { base_version: 1 });
      assert.equal(second.canonical_fact.version, 2);
      assert.equal(second.canonical_fact.supersedes_id, first.canonical_fact.id);
      for (const sql of ["update ops.canonical_fact set normalized_value=1", 'delete from ops.canonical_fact',
        "update ops.fact_observation set value='1'", 'delete from ops.fact_observation', "update ops.fact_observation set cited_text='x'"])
        await assert.rejects(c.query(sql), { code: '23514' }, sql);
    });

    await t.test('concurrent promotions of one field admit exactly one version', async () => {
      const obs = await record('race-deal', [observation('a'), observation('b')]);
      const outcomes = await Promise.allSettled(Array.from({ length: 4 }, async () => {
        const { default: pg } = await import('pg');
        const other = new pg.Client(c.connectionParameters);
        await other.connect();
        try {
          await other.query('set role carr_writer');
          return await executeRegisteredTool(other, job, 'promote-fact', { idempotency_key: randomUUID(), entity_ref: 'race-deal',
            fact_key: 'base_rent_per_sf', basis: 'independent_agreement', observation_ids: ids(obs), base_version: 0 });
        } finally { await other.end(); }
      }));
      assert.equal(outcomes.filter(o => o.status === 'fulfilled' && o.value.ok).length, 1);
      assert.ok(outcomes.filter(o => o.status === 'rejected').every(o => o.reason.payload.error === 'version_conflict'));
    });

    await t.test('property: rule promotion succeeds exactly when the kernel oracle says it should', async () => {
      let seed = 39;
      const rand = n => { seed = (seed * 48271) % 0x7fffffff; return seed % n; };
      for (let round = 0; round < 40; round++) {
        const entity = `property-${round}`;
        const inputs = Array.from({ length: 2 + rand(2) }, (_, i) => observation(`o${i}`, {
          value: ['24', '24', '24', '25'][rand(4)],
          confidence_tier: ['auto_accept', 'auto_accept', 'auto_accept', 'verify_band'][rand(4)],
          source: { document_family_id: `family-${rand(3)}`, derivation_chain: rand(3) ? [] : [`family-${rand(3)}`],
            content_hash: hash(rand(3) + 1), document_ref: `doc-${rand(3)}` },
          extraction: { pass: `pass-${rand(2)}`, model: `model-${rand(2)}` },
        }));
        const pairs = inputs.flatMap((a, i) => inputs.slice(i + 1).map(b => [a, b]));
        const lineage = o => [o.source.document_family_id, ...o.source.derivation_chain];
        const expectOk = new Set(inputs.map(o => o.value)).size === 1
          && inputs.every(o => o.confidence_tier === 'auto_accept')
          && pairs.some(([a, b]) => a.source.content_hash !== b.source.content_hash && a.source.document_ref !== b.source.document_ref
            && !lineage(a).some(f => lineage(b).includes(f))
            && (a.extraction.pass !== b.extraction.pass || a.extraction.model !== b.extraction.model));
        const out = await promote(entity, ids(await record(entity, inputs)));
        assert.equal(out.ok, expectOk, `${entity}: ${JSON.stringify({ out, inputs })}`);
      }
    });

    await t.test('cited text expires to null on the next write while its digest remains', async () => {
      await record('retention-deal', [observation('old'), observation('new', { value: '30' })]);
      await c.query("set session_replication_role=replica; update ops.fact_observation set cited_text_expires_at=now()-interval '1 second' where entity_ref='retention-deal' and observation_ref='old'; set session_replication_role=origin");
      const masked = (await conflicts({ entity_ref: 'retention-deal' })).conflicts[0].observations;
      assert.equal(masked[0].source.cited_text, null);
      assert.match(masked[0].source.cited_text_sha256, /^[0-9a-f]{64}$/);
      await record('another-deal', [observation('trigger')]);
      const stored = (await c.query("select cited_text,cited_text_sha256 from ops.fact_observation where entity_ref='retention-deal' order by ordinal")).rows;
      assert.equal(stored[0].cited_text, null);
      assert.equal(stored[0].cited_text_sha256, masked[0].source.cited_text_sha256);
      assert.equal(stored[1].cited_text, 'Synthetic rent clause new.');
    });
  } finally { await db.close(); }
});
