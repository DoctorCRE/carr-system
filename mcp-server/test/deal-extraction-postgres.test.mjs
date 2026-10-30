// Exercise stored document job identity and review effects on disposable PostgreSQL.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';

const job = { id: '00000000-0000-4000-8000-000000000002', slug: 'codex', human: false, sponsoring_human_slug: 'joe' };
const partner = { id: '00000000-0000-4000-8000-000000000001', slug: 'joe', human: true };

test('stored extraction jobs bind identity, replay one batch, and review abstentions', async () => {
  const db = await postgresDatabase('deal-extraction-');
  const c = db.client;
  const invoke = async (verb, args, actor = job) => {
    await c.query(`set role ${verb.startsWith('read-') || verb.startsWith('list-') ? 'carr_reader' : 'carr_writer'}`);
    try { return await executeRegisteredTool(c, actor, verb, args); }
    finally { await c.query('reset role'); }
  };
  try {
    await c.query(await readFile(new URL('../../migrations/0001_init.sql', import.meta.url), 'utf8'));
    await c.query('create schema ops; create role carr_reader; create role carr_writer; create role carr_authority; create role dot_reader');
    await c.query("create function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as $$select '{\"secdef_execute\":[]}'::jsonb$$");
    for (const file of ['0983_fact_store.sql', '1070_fact_review.sql', '1078_deal_extraction.sql', '1079_deal_extraction_reruns.sql', '1081_deal_schedules.sql'])
      await c.query(await readFile(new URL(`../../migrations/${file}`, import.meta.url), 'utf8'));
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner'),($2,'codex','automation','Synthetic extractor')", [partner.id, job.id]);
    const entity_ref = randomUUID(), document_ref = randomUUID(), hash = 'a'.repeat(64);
    await c.query("insert into attachment(id,subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by) values($1,'deal',$2,'documents/synthetic/source.pdf','synthetic.pdf','application/pdf',$3,10,$4)", [document_ref, entity_ref, hash, partner.id]);
    const contract = { idempotency_key: randomUUID(), entity_ref, document_ref, document_type: 'lease', model: 'haiku', extractor_version: 'deal-text.v1' };
    const source = await invoke('read-deal-extraction-source', contract);
    assert.equal(source.document.content_hash, hash);
    assert.equal(source.document.lineage_verified, false);
    await assert.rejects(invoke('read-deal-extraction-source', { ...contract, entity_ref: randomUUID() }), e => e.payload.error === 'document_not_found');
    const observation = { id: 'base_rent_per_sf', fact_key: 'base_rent_per_sf', value: '24', unit: 'USD/SF/year', expense_basis: 'nnn',
      method: 'text_layer', extractor_version: 'deal-text.v1', extraction: { pass: 'ground', model: 'haiku' }, confidence_tier: 'human_review',
      source: { document_ref, content_hash: hash, document_family_id: source.document.document_family_id, page_start: 1, page_end: 1, cited_text: 'Synthetic base rent $24 per SF per year NNN.' } };
    const receipt = { schema_version: 'deal-extraction.v1', contract, source: Object.fromEntries(Object.entries(source.document).filter(([k]) => !['r2_key', 'mime', 'bytes'].includes(k))),
      passes: [{ phase: 'extract', output_sha256: hash, model: 'haiku' }, { phase: 'ground', output_sha256: hash, model: 'haiku' }],
      attempts: [{ phase: 'extract', attempt: 1, status: 'completed', cost_usd: '0.01' }, { phase: 'ground', attempt: 1, status: 'completed', cost_usd: '0.01' }],
      cost_usd: '0.02', cost_complete: true, grounding_changes: [], canonical: false, proposed_effects: ['base_rent_per_sf'],
      abstentions: [{ fact_key: 'term_months', status: 'unreadable', reason: 'unsupported_schedule' },
        { fact_key: 'free_rent_months', status: 'not_present', reason: 'absent' },
        { fact_key: 'landlord_ti_per_sf', status: 'unreadable', reason: 'unsupported_ocr' }] };
    const args = { ...contract, content_hash: hash, receipt, observations: [observation] };
    for (const citation of ['No numeric term here.', 'x'.repeat(601) + '24']) {
      await assert.rejects(invoke('record-deal-extraction', { ...args,
        observations: [{ ...observation, source: { ...observation.source, cited_text: citation } }] }),
        e => e.payload.error === 'validation_error');
    }
    const first = await invoke('record-deal-extraction', args);
    assert.equal(first.canonical, false);
    assert.equal(first.replayed, false);
    const replay = await invoke('record-deal-extraction', args);
    assert.equal(replay.replayed, true);
    assert.equal(replay.batch_id, first.batch_id);
    const divergent = await invoke('record-deal-extraction', { ...args, observations: [{ ...observation, source: { ...observation.source, cited_text: 'Different citation still says $24.' } }] });
    assert.equal(divergent.batch_id, first.batch_id);
    assert.equal(divergent.replayed, true);
    assert.equal((await invoke('read-deal-extraction-source', contract)).receipt.job_id, first.job_id);
    await assert.rejects(invoke('record-deal-extraction', { ...args, model: 'sonnet' }), e => e.payload.error === 'validation_error' || e.payload.error === 'key_reuse');
    await assert.rejects(invoke('record-deal-extraction', args, partner), e => e.payload.error === 'not_in_profile');
    const queue = await invoke('list-fact-review', { entity_ref }, partner);
    assert.equal(queue.items.length, 4);
    const rent = queue.items.find(i => i.fact_key === 'base_rent_per_sf');
    assert.equal(rent.observations.length, 1);
    const term = queue.items.find(i => i.fact_key === 'term_months');
    assert.ok(term.reasons.includes('unsupported_schedule'));
    assert.equal(term.extraction_abstentions[0].status, 'unreadable');
    const rejected = await invoke('resolve-fact-review', { idempotency_key: randomUUID(), entity_ref, fact_key: 'term_months',
      base_version: term.canonical_version, review_version: term.review_version, decision: 'reject', reason: 'Synthetic review complete.' }, partner);
    assert.equal(rejected.ok, true);
    assert.equal((await invoke('list-fact-review', { entity_ref }, partner)).items.length, 3);
    await assert.rejects(invoke('record-deal-extraction', { ...args, idempotency_key: randomUUID(), content_hash: 'b'.repeat(64) }), e => e.payload.error === 'validation_error' || e.payload.error === 'source_mismatch');
    const foreign = (await c.query("select ops.read_deal_extraction_source('synthetic-other-tenant',$1,$2::jsonb) r", [job.id, JSON.stringify(contract)])).rows[0].r;
    assert.equal(foreign.error, 'not_in_profile');
    const rerunContract = { ...contract, idempotency_key: randomUUID() };
    const rerunSource = await invoke('read-deal-extraction-source', rerunContract);
    assert.equal(rerunSource.receipt?.job_id, first.job_id);
    const rerun = await invoke('record-deal-extraction', { ...args, ...rerunContract,
      receipt: { ...receipt, contract: rerunContract } });
    assert.equal(rerun.batch_id, first.batch_id);
    assert.equal(rerun.replayed, true);
    const resolvedQueue = await invoke('list-fact-review', { entity_ref, include_resolved: true }, partner);
    assert.equal(resolvedQueue.items.find(i => i.fact_key === 'term_months').pending, false);
    assert.equal(resolvedQueue.items.find(i => i.fact_key === 'base_rent_per_sf').observations.length, 1);
    await c.query('update attachment set sha256=$1 where id=$2', ['c'.repeat(64), document_ref]);
    args.content_hash = 'c'.repeat(64);
    args.receipt.source.content_hash = args.content_hash;
    args.receipt.source.document_family_id = 'sha256:' + args.content_hash;
    observation.source.content_hash = args.content_hash;
    const secondContract = { ...contract, idempotency_key: randomUUID() };
    const secondArgs = { ...args, ...secondContract, receipt: { ...receipt, contract: secondContract } };
    const { default: pg } = await import('pg');
    const outcomes = await Promise.all(Array.from({ length: 3 }, async () => {
      const other = new pg.Client(c.connectionParameters);
      await other.connect();
      try {
        await other.query('set role carr_writer');
        const concurrentContract = { ...secondContract, idempotency_key: randomUUID() };
        return await executeRegisteredTool(other, job, 'record-deal-extraction', { ...secondArgs, ...concurrentContract,
          receipt: { ...secondArgs.receipt, contract: concurrentContract } });
      } finally { await other.end(); }
    }));
    assert.equal(new Set(outcomes.map(o => o.batch_id)).size, 1);
    assert.equal(outcomes.filter(o => !o.replayed).length, 1);
    const reopened = (await invoke('list-fact-review', { entity_ref }, partner)).items.find(i => i.fact_key === 'term_months');
    assert.ok(reopened.pending);
    await assert.rejects(invoke('resolve-fact-review', { idempotency_key: randomUUID(), entity_ref, fact_key: 'term_months',
      base_version: term.canonical_version, review_version: term.review_version, decision: 'reject', reason: 'Stale synthetic review.' }, partner), e => e.payload.error === 'version_conflict');
    const raw = await invoke('record-fact-observations', { idempotency_key: randomUUID(), entity_ref,
      observations: [0, 1].map(i => ({ ...observation, id: `unverified-${i}`, confidence_tier: 'auto_accept',
        source: { ...observation.source, document_ref: `unverified-${i}`, content_hash: String(i + 1).repeat(64), document_family_id: `asserted-${i}` },
        extraction: { pass: `pass-${i}`, model: 'haiku' } })) });
    const promote = () => invoke('promote-fact', { idempotency_key: randomUUID(), entity_ref,
      fact_key: 'base_rent_per_sf', basis: 'independent_agreement', observation_ids: raw.observations.map(o => o.id), base_version: 0 });
    assert.equal((await promote()).error, 'automatic_promotion_disabled');
    await c.query("update system_config set value='true'::jsonb where key='facts.automatic_promotion_enabled'");
    assert.equal((await promote()).error, 'independence_not_proven');
    await c.query("update system_config set value='false'::jsonb where key='facts.automatic_promotion_enabled'");
    for (const role of ['carr_reader', 'carr_writer', 'dot_reader']) {
      await c.query(`set role ${role}`);
      try { await assert.rejects(c.query('select * from ops.deal_extraction_job'), { code: '42501' }); }
      finally { await c.query('reset role'); }
    }
    await c.query('update attachment set deleted_at=now() where id=$1', [document_ref]);
    await assert.rejects(invoke('read-deal-extraction-source', { ...contract, idempotency_key: randomUUID() }), e => e.payload.error === 'document_not_found');
  } finally { await db.close(); }
});
