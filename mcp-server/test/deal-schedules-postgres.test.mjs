// Verify structured schedule review, comparison, and promotion on disposable PostgreSQL.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';

const job = { id: '00000000-0000-4000-8000-000000000002', slug: 'codex', human: false, sponsoring_human_slug: 'joe' };
const partner = { id: '00000000-0000-4000-8000-000000000001', slug: 'joe', human: true };
const period = (start, end, rate, method = 'ocr') => Object.fromEntries(Object.entries({
  start_month: start, end_month: end, rate, unit: 'USD/SF/year', expense_basis: 'nnn', escalation_percent: start === 1 ? null : '3',
}).map(([key, value]) => [key, { value, source: { page: 1, region_id: `row-${start}`, method } }]));

test('schedule relations survive storage and human promotion; OCR agreement never promotes automatically', async () => {
  const db = await postgresDatabase('deal-schedules-');
  const c = db.client;
  const invoke = async (verb, args, actor = job) => {
    await c.query(`set role ${verb.startsWith('list-') || verb.startsWith('read-') ? 'carr_reader' : 'carr_writer'}`);
    try { return await executeRegisteredTool(c, actor, verb, args); }
    finally { await c.query('reset role'); }
  };
  try {
    await c.query(await readFile(new URL('../../migrations/0001_init.sql', import.meta.url), 'utf8'));
    await c.query('create schema ops; create role carr_reader; create role carr_writer; create role carr_authority; create role dot_reader');
    await c.query(`create function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as $$select '{"secdef_execute":[]}'::jsonb$$`);
    for (const file of ['0983_fact_store.sql', '1099_fact_review.sql', '1103_deal_extraction.sql', '1109_deal_schedules.sql'])
      await c.query(await readFile(new URL(`../../migrations/${file}`, import.meta.url), 'utf8'));
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner'),($2,'codex','automation','Synthetic extractor')", [partner.id, job.id]);
    const entity_ref = randomUUID(), document_ref = randomUUID(), hash = 'a'.repeat(64);
    await c.query("insert into attachment(id,subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by) values($1,'deal',$2,'documents/synthetic.pdf','synthetic.pdf','application/pdf',$3,10,$4)", [document_ref, entity_ref, hash, partner.id]);
    const observation = { id: 'schedule', fact_key: 'rent_schedule', value: [period(1, 12, '24'), period(13, 24, '24.72')], unit: 'schedule',
      method: 'ocr', extractor_version: 'deal-regions.v2', confidence_tier: 'auto_accept', extraction: { pass: 'ground', model: 'haiku' },
      source: { document_ref, content_hash: hash, document_family_id: `sha256:${hash}`, page_start: 1, page_end: 1, cited_text: 'Year 1 | $24 | NNN. Year 2 | $24.72 | NNN | 3%.' } };
    const record = async o => invoke('record-fact-observations', { idempotency_key: randomUUID(), entity_ref, observations: [o] });
    const a = await record(observation);
    const b = await record({ ...observation, id: 'second-engine' });
    const comparison = await invoke('compare-fact-observations', { entity_ref, observations: [observation, { ...observation, id: 'other', value: [period(1, 12, '24.72'), period(13, 24, '24')] }] });
    assert.equal(comparison.fields[0].status, 'conflict');
    assert.equal(comparison.review_items[0].priority, 'high');
    const before = (await invoke('list-fact-review', { entity_ref }, partner)).items[0];
    assert.equal(before.priority, 'normal');
    const agreeing = await invoke('compare-fact-observations', { entity_ref, observations: [observation] });
    assert.equal(agreeing.review_items[0].priority, 'normal');
    assert.deepEqual(before.observations[0].value, observation.value);
    assert.equal(before.observations[0].normalized_value[1].start_month, 13);
    await c.query("update system_config set value='true'::jsonb where key='facts.automatic_promotion_enabled'");
    const refusal = await invoke('promote-fact', { idempotency_key: randomUUID(), entity_ref, fact_key: 'rent_schedule', basis: 'independent_agreement', observation_ids: [a.observations[0].id, b.observations[0].id], base_version: 0 });
    assert.equal(refusal.error, 'observation_not_rule_eligible');
    const approved = await invoke('resolve-fact-review', { idempotency_key: randomUUID(), entity_ref, fact_key: 'rent_schedule', decision: 'approve', reason: 'Synthetic schedule adjudication.', observation_id: a.observations[0].id, base_version: 0, review_version: before.review_version }, partner);
    assert.equal(approved.canonical_fact.normalized_schedule[1].rate, '24.72');
    assert.equal((await invoke('list-fact-review', { entity_ref }, partner)).items.length, 0);
    const repeat = await invoke('promote-fact', { idempotency_key: randomUUID(), entity_ref, fact_key: 'rent_schedule', basis: 'human_review', observation_ids: [a.observations[0].id], base_version: 1 }, partner);
    assert.equal(repeat.canonical_fact.normalized_schedule[1].rate, '24.72');
    assert.equal((await invoke('list-fact-review', { entity_ref }, partner)).items.length, 0, 'promoting the reviewed schedule and source stays resolved');
    await invoke('promote-fact', { idempotency_key: randomUUID(), entity_ref, fact_key: 'rent_schedule', basis: 'human_review', observation_ids: [b.observations[0].id], base_version: 2 }, partner);
    assert.equal((await invoke('list-fact-review', { entity_ref }, partner)).items[0].pending, true, 'another schedule source reopens review');
    await invoke('promote-fact', { idempotency_key: randomUUID(), entity_ref, fact_key: 'rent_schedule', basis: 'human_review', observation_ids: [a.observations[0].id], base_version: 3 }, partner);
    assert.equal((await invoke('list-fact-review', { entity_ref }, partner)).items.length, 0);
    await assert.rejects(record({ ...observation, method: 'text_layer' }), e => e.payload.error === 'validation_error');
    const overlap = structuredClone(observation); overlap.value[1].start_month.value = 12;
    await assert.rejects(record(overlap), e => e.payload.error === 'validation_error');
    await assert.rejects(c.query('select ops.record_fact_observations($1,$2,$3,$4,$5::jsonb)', ['carr-internal', job.id, randomUUID(), entity_ref, JSON.stringify([{ ...observation, method: 'text_layer' }])]), { code: '23514' });
    const contract = { idempotency_key: randomUUID(), entity_ref, document_ref, document_type: 'lease', model: 'haiku', extractor_version: 'deal-regions.v2' };
    const source = (await invoke('read-deal-extraction-source', contract)).document;
    const bound = { ...observation, confidence_tier: 'human_review' };
    const receipt = { schema_version: 'deal-extraction.v2', contract,
      source: Object.fromEntries(Object.entries(source).filter(([k]) => !['r2_key','mime','bytes'].includes(k))),
      passes: ['extract','ground'].map(phase => ({ phase, output_sha256: hash, model: null })), attempts: [], cost_usd: '0', cost_complete: true,
      grounding_changes: [], abstentions: ['base_rent_per_sf','landlord_ti_per_sf','term_months','free_rent_months'].map(fact_key => ({ fact_key, status:'unreadable',reason:'no_text_layer' })), proposed_effects: ['rent_schedule'], canonical: false };
    receipt.abstentions[0].reason = 'ocr_disagreement';
    const result = await invoke('record-deal-extraction', { ...contract, content_hash: hash, receipt, observations: [bound] });
    assert.equal(result.canonical, false);
    assert.equal(result.observations.length, 1);
    const disagreement = (await invoke('list-fact-review', { entity_ref }, partner)).items.find(i => i.fact_key === 'base_rent_per_sf');
    assert.equal(disagreement.priority, 'high');
    const altered = structuredClone(bound); altered.value[1].expense_basis.value = 'gross';
    await record(altered);
    const reopened = (await invoke('list-fact-review', { entity_ref }, partner)).items.find(i => i.fact_key === 'rent_schedule');
    assert.ok(reopened.reasons.includes('conflict'));
    assert.equal(reopened.priority, 'high');
    await c.query('set role carr_writer');
    await assert.rejects(c.query('select * from ops.fact_observation'), { code:'42501' });
    await c.query('reset role');
  } finally { await db.close(); }
});
