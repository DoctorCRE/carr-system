import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import pg from 'pg';

const tenant = 'carr-internal';
const functions = ['fact_review_field', 'promote_fact', 'record_deal_extraction'];

async function replacementSources(client) {
  const installed = (await client.query(`select proname, prosrc from pg_proc p
    join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='ops' and proname=any($1::text[])`, [functions])).rows;
  const ledger = (await client.query('select filename from public.schema_migrations order by applied_at, filename')).rows;
  const files = new Set(await readdir(new URL('../../migrations/', import.meta.url)));
  const latest = new Map();
  for (const { filename } of ledger) {
    if (!files.has(filename)) continue;
    const sql = await readFile(new URL(`../../migrations/${filename}`, import.meta.url), 'utf8');
    for (const { proname, prosrc } of installed) {
      const definition = new RegExp(`create\\s+(?:or\\s+replace\\s+)?function\\s+ops\\.${proname}\\s*\\([^]*?\\bas\\s+(\\$[a-z_0-9]*\\$)([^]*?)\\1`, 'gi');
      for (const match of sql.matchAll(definition)) {
        if (match[2].trim() === prosrc.trim()) latest.set(proname, filename);
      }
    }
  }
  return functions.map(name => `ops.${name} installed body last defined by ${latest.get(name) || 'unmatched source; inspect the installed definition'}`).join('\n');
}

const observation = (id, patch = {}) => ({
  id, fact_key: 'base_rent_per_sf', value: '24', unit: 'USD/SF/year', expense_basis: 'nnn',
  source: { document_ref: `synthetic-${id}`, page_start: 1, page_end: 1,
    cited_text: `Synthetic rent clause ${id}.`, content_hash: 'a'.repeat(64), document_family_id: id },
  method: 'text_layer', extractor_version: 'installed-proof.v1', confidence_tier: 'human_review',
  extraction: { pass: id, model: 'synthetic' }, ...patch,
});

test('final installed fact-review functions preserve reviewed terms and extraction abstentions',
  { skip: !process.env.CARR_FACT_REVIEW_DATABASE_URL }, async t => {
  const dsn = process.env.CARR_FACT_REVIEW_DATABASE_URL;
  assert.ok(dsn, 'CARR_FACT_REVIEW_DATABASE_URL must name the migration lane database');
  assert.ok(['localhost', '127.0.0.1'].includes(new URL(dsn).hostname), 'proof requires loopback PostgreSQL');
  const client = new pg.Client({ connectionString: dsn });
  await client.connect();
  try {
    const sources = await replacementSources(client);
    t.diagnostic(sources);
    await client.query('begin');
    const human = randomUUID(), job = randomUUID(), entity = randomUUID();
    await client.query(`insert into public.actor(id,slug,kind,display_name) values
      ($1,$2,'human','Synthetic fact reviewer'),($3,$4,'automation','Synthetic extractor')`,
    [human, `proof-${human}`, job, `proof-${job}`]);
    const call = async (role, sql, args) => {
      await client.query(`set local role ${role}`);
      try {
        const result = (await client.query(sql, args)).rows[0].result;
        assert.equal(result.ok, true, JSON.stringify(result));
        return result;
      } finally { await client.query('reset role'); }
    };
    const record = (inputs, target = entity) => call('carr_writer', 'select ops.record_fact_observations($1,$2,$3,$4,$5) result',
      [tenant, job, randomUUID(), target, JSON.stringify(inputs)]);
    const read = async (target = entity, includeResolved = true) => (await call('carr_reader',
      'select ops.list_fact_review($1,$2,100,null,$3) result', [tenant, target, includeResolved])).items;
    const resolve = (target, item, decision, observationId) => call('carr_writer',
      'select ops.resolve_fact_review($1,$2,$3) result', [tenant, human, JSON.stringify({
        idempotency_key: randomUUID(), entity_ref: target, fact_key: item.fact_key,
        base_version: item.canonical_version, review_version: item.review_version,
        decision, ...(observationId ? { observation_id: observationId } : {}), reason: 'Synthetic proof review.',
      })]);
    const check = async (target, key, pending, behavior) => {
      const item = (await read(target)).find(i => i.fact_key === key);
      assert.equal(item?.pending, pending, `${behavior}\n${sources}\nReconcile the later body with the earlier behavior and update this proof.`);
      return item;
    };
    const batch = await record([observation('chosen'), observation('different', { value: '30' }), observation('same-value')]);
    const ids = batch.observations.map(o => o.id);
    await resolve(entity, (await read())[0], 'approve', ids[0]);
    const promote = (id, version, target = entity, key = 'base_rent_per_sf') => call('carr_writer',
      'select ops.promote_fact($1,$2,$3,$4,$5,$6,$7,$8) result',
      [tenant, human, randomUUID(), target, key, 'human_review', [id], version]);
    await check(entity, 'base_rent_per_sf', false, 'approval resolves the reviewed field');
    await promote(ids[0], 1);
    const matching = await check(entity, 'base_rent_per_sf', false, 'matching value, unit, basis and sources must stay resolved');
    assert.equal(matching.canonical_version, 2);
    assert.equal(matching.review_history.length, 1);
    assert.deepEqual(matching.canonical_history.map(f => f.normalized_value), ['24', '24']);
    assert.deepEqual(await read(entity, false), []);
    const conflicts = await call('carr_reader', 'select ops.list_fact_conflicts($1,$2,100) result', [tenant, entity]);
    assert.deepEqual(conflicts.conflicts, []);
    await promote(ids[1], 2);
    await check(entity, 'base_rent_per_sf', true, 'changed value must reopen review');
    await promote(ids[2], 3);
    await check(entity, 'base_rent_per_sf', true, 'changed sources at the reviewed value must reopen review');
    await promote(ids[0], 4);
    await check(entity, 'base_rent_per_sf', false, 'restoring reviewed terms must resolve review');
    await record([observation('new-evidence')]);
    await check(entity, 'base_rent_per_sf', true, 'new agreeing evidence must reopen review');

    const extractionEntity = randomUUID(), document = randomUUID();
    await client.query(`insert into public.attachment(id,subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by)
      values($1,'deal',$2,$3,'synthetic.pdf','application/pdf',$4,10,$5)`,
    [document, extractionEntity, `synthetic/${document}`, 'b'.repeat(64), human]);
    const extract = async (model, schedule) => {
      const contract = { idempotency_key: randomUUID(), entity_ref: extractionEntity, document_ref: document,
        document_type: 'lease', model, extractor_version: schedule ? 'deal-regions.v2' : 'installed-proof.v1' };
      const source = await call('carr_reader', 'select ops.read_deal_extraction_source($1,$2,$3) result',
        [tenant, job, JSON.stringify(contract)]);
      const { r2_key, mime, bytes, ...receiptSource } = source.document;
      return call('carr_writer', 'select ops.record_deal_extraction($1,$2,$3) result', [tenant, job, JSON.stringify({
        ...contract, content_hash: receiptSource.content_hash, observations: schedule ? [schedule] : [], receipt: {
          contract, source: receiptSource, canonical: false, abstentions: [
            { fact_key: 'base_rent_per_sf', status: 'unreadable', reason: 'unsupported_schedule' },
            ...['term_months', 'free_rent_months', 'landlord_ti_per_sf'].map(fact_key =>
              ({ fact_key, status: 'not_present', reason: 'absent' })),
          ],
        },
      })]);
    };
    const first = await extract('synthetic-one');
    assert.equal(first.canonical, false);
    assert.deepEqual(first.observations, []);
    for (const [key, reason] of [['base_rent_per_sf', 'unsupported_schedule'], ['term_months', 'absent']]) {
      const item = await check(extractionEntity, key, true, `${reason} abstention must enter review without a scalar observation`);
      assert.ok(item.reasons.includes(reason));
      assert.equal(item.observations.length, 0);
      await resolve(extractionEntity, item, 'reject');
      await check(extractionEntity, key, false, `review can resolve ${reason} abstention`);
    }
    await extract('synthetic-two');
    await check(extractionEntity, 'term_months', true, 'new extraction abstention must reopen review');
    await check(extractionEntity, 'base_rent_per_sf', true, 'new rent-schedule evidence must reopen review');

    const period = (start, end, rate) => Object.fromEntries(Object.entries({
      start_month: start, end_month: end, rate, unit: 'USD/SF/year', expense_basis: 'nnn',
      escalation_percent: start === 1 ? null : '3',
    }).map(([key, value]) => [key, { value, source: { page: 1, region_id: `row-${start}`, method: 'ocr' } }]));
    const schedule = observation('schedule', {
      fact_key: 'rent_schedule', value: [period(1, 12, '24'), period(13, 24, '24.72')], unit: 'schedule',
      method: 'ocr', source: { document_ref: document, content_hash: 'b'.repeat(64),
        document_family_id: 'synthetic-schedule', page_start: 1, page_end: 1, cited_text: 'Synthetic two-year rent schedule.' },
    });
    delete schedule.expense_basis;
    const stored = await extract('synthetic-schedule', schedule);
    const chosen = stored.observations[0].id;
    const alternatives = await record([
      { ...schedule, id: 'changed-schedule', value: [period(1, 12, '25'), period(13, 24, '25.75')] },
      { ...schedule, id: 'other-schedule-source', source: { ...schedule.source, document_ref: 'synthetic-other' } },
    ], extractionEntity);
    const scheduleReview = (await read(extractionEntity)).find(i => i.fact_key === 'rent_schedule');
    assert.ok(scheduleReview.reasons.includes('conflict'), 'different schedule terms must remain a visible conflict');
    assert.equal(scheduleReview.priority, 'high', 'schedule conflicts must retain high review priority');
    await resolve(extractionEntity, scheduleReview, 'approve', chosen);
    await promote(chosen, 1, extractionEntity, 'rent_schedule');
    const settled = await check(extractionEntity, 'rent_schedule', false, 'matching schedule and source must stay resolved');
    assert.equal(settled.canonical_fact.normalized_schedule[1].rate, '24.72');
    await promote(alternatives.observations[0].id, 2, extractionEntity, 'rent_schedule');
    await check(extractionEntity, 'rent_schedule', true, 'changed schedule terms must reopen review');
    await promote(alternatives.observations[1].id, 3, extractionEntity, 'rent_schedule');
    await check(extractionEntity, 'rent_schedule', true, 'changed schedule source must reopen review');
    await promote(chosen, 4, extractionEntity, 'rent_schedule');
    await check(extractionEntity, 'rent_schedule', false, 'restoring the reviewed schedule must resolve review');
    await record([{ ...schedule, id: 'new-schedule-evidence' }], extractionEntity);
    await check(extractionEntity, 'rent_schedule', true, 'new agreeing schedule evidence must reopen review');
  } finally {
    try { await client.query('rollback'); }
    finally { await client.end(); }
  }
});
