import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { publicError } from '../src/public-errors.js';

const db = { query() { throw new Error('comparison must not read or write the database'); } };
const actor = { id: 'synthetic-reviewer', human: true };
const observation = (id, patch = {}) => ({
  id, fact_key: 'base_rent_per_sf', value: '24', unit: 'USD/SF/year', expense_basis: 'nnn',
  source: { document_ref: 'synthetic-lease-v1', page_start: 2, page_end: 2, cited_text: 'Base rent is $24 per SF per year.' },
  method: 'text_layer', extractor_version: 'synthetic-v1', confidence_tier: 'auto_accept', ...patch,
});
const compare = observations => TOOLS['compare-fact-observations'].handler(db, actor, {
  entity_ref: 'synthetic-deal-1', observations,
});

test('repeated document evidence is reported without treating agreement as independent support', async () => {
  const out = await compare([observation('annual'), observation('monthly', { value: '2', unit: 'USD/SF/month' })]);
  assert.deepEqual(out.shared_source_groups, [{
    fact_key: 'base_rent_per_sf', basis: 'document_ref', source_ref: 'synthetic-lease-v1',
    observation_ids: ['annual', 'monthly'],
  }]);
  assert.equal(out.fields[0].status, 'agreement');
  assert.equal(out.evidence_independence, 'not_assessed');
  assert.equal(out.source_verification, 'unverified');
  assert.equal(out.canonical, false);
  assert.equal(out.records_written, 0);
});

test('different document references with the same hash report a shared source and preserve supplied hash case', async () => {
  const inputs = [
    observation('lease', { source: { ...observation('x').source, document_ref: 'lease', content_hash: 'aB'.repeat(32) } }),
    observation('copy', { source: { ...observation('x').source, document_ref: 'copy', content_hash: 'Ab'.repeat(32) } }),
  ];
  const original = structuredClone(inputs);
  const out = await compare(inputs);
  assert.deepEqual(out.shared_source_groups, [{
    fact_key: 'base_rent_per_sf', basis: 'content_hash', source_ref: 'ab'.repeat(32), observation_ids: ['lease', 'copy'],
  }]);
  assert.deepEqual(out.observations.map(o => o.input), original);
  assert.deepEqual(inputs, original);
  assert.equal(out.evidence_independence, 'not_assessed');
});

test('shared roots and ancestor references expose common source families once per observation', async () => {
  const inputs = [
    observation('original', { source: { ...observation('x').source, document_ref: 'original', document_family_id: 'root' } }),
    observation('revision', { source: { ...observation('x').source, document_ref: 'revision', document_family_id: 'root', derivation_chain: ['root', 'upstream', 'upstream'] } }),
    observation('quote', { source: { ...observation('x').source, document_ref: 'quote', document_family_id: 'quote', derivation_chain: ['upstream', 'root'] } }),
  ];
  const original = structuredClone(inputs);
  const out = await compare(inputs);
  assert.deepEqual(out.shared_source_groups, [
    { fact_key: 'base_rent_per_sf', basis: 'document_family_id', source_ref: 'root', observation_ids: ['original', 'revision', 'quote'] },
    { fact_key: 'base_rent_per_sf', basis: 'document_family_id', source_ref: 'upstream', observation_ids: ['revision', 'quote'] },
  ]);
  assert.deepEqual(out.observations.map(o => o.input), original);
  assert.deepEqual(inputs, original);
  assert.equal(out.evidence_independence, 'not_assessed');
});

test('missing and distinct lineage leave independence unknown and never join different fact fields', async () => {
  const first = observation('rent', { source: { ...observation('x').source,
    content_hash: 'a'.repeat(64), document_family_id: 'rent-root', derivation_chain: ['rent-upstream'],
  } });
  const term = observation('term', { fact_key: 'term_months', value: '60', unit: 'months', source: { ...first.source } });
  delete term.expense_basis;
  const inputs = [first, term,
    observation('other-rent', { source: { ...observation('x').source, document_ref: 'other', content_hash: 'b'.repeat(64),
      document_family_id: 'other-root', derivation_chain: [] } }),
    observation('unknown-rent', { source: { ...observation('x').source, document_ref: 'unknown' } }),
  ];
  for (const observations of [inputs, [first], [observation('legacy')]]) {
    const out = await compare(observations);
    assert.deepEqual(out.shared_source_groups, []);
    assert.equal(out.evidence_independence, 'not_assessed');
    assert.equal(out.source_verification, 'unverified');
    assert.equal(out.canonical, false);
    assert.equal(out.records_written, 0);
  }
  const ancestorOnly = await compare([
    observation('a', { source: { ...observation('x').source, document_ref: 'a', derivation_chain: ['upstream'] } }),
    observation('b', { source: { ...observation('x').source, document_ref: 'b', derivation_chain: ['upstream'] } }),
  ]);
  assert.deepEqual(ancestorOnly.shared_source_groups, [{ fact_key: 'base_rent_per_sf', basis: 'document_family_id',
    source_ref: 'upstream', observation_ids: ['a', 'b'] }]);
});

test('malformed source lineage refuses as a public validation error without exposing cited text', async t => {
  t.mock.method(console, 'error', () => {});
  const patches = [
    ...['a'.repeat(63), 'a'.repeat(65), `${'a'.repeat(64)}\n`, 'g'.repeat(64), null, 42, ['a'.repeat(64)], undefined]
      .map(content_hash => ({ content_hash })),
    ...['', ' ', 'f'.repeat(201), null, 42, [], undefined].map(document_family_id => ({ document_family_id })),
    ...[null, 'root', {}, [null], [42], [''], [' '], ['x'.repeat(201)], new Array(1),
      Array.from({ length: 21 }, () => 'root'), [{ document_family_id: 'root' }], undefined]
      .map(derivation_chain => ({ derivation_chain })),
    { independence_verified: true }, { canonical: true },
  ];
  for (const patch of patches) await assert.rejects(() => compare([
    observation('bad', { source: { ...observation('x').source, ...patch } }),
  ]), error => {
    assert.equal(error.payload?.error, 'validation_error');
    const out = publicError(error, { inputSchema: TOOLS['compare-fact-observations'].inputSchema });
    assert.equal(out.error, 'validation_error');
    assert.equal(JSON.stringify(out).includes('Base rent is'), false);
    return true;
  });
  const maxChain = Array.from({ length: 20 }, (_, i) => `synthetic-root-${i}`);
  const out = await compare([observation('bounded', { source: { ...observation('x').source,
    document_family_id: 'x'.repeat(200), derivation_chain: maxChain, content_hash: 'F'.repeat(64),
  } })]);
  assert.deepEqual(out.observations[0].input.source.derivation_chain, maxChain);
  assert.deepEqual(out.shared_source_groups, []);
});

test('registered source groups retain conflict review evidence and OCR escalation in deterministic order', async () => {
  const source = { ...observation('x').source, content_hash: 'C'.repeat(64), document_family_id: 'same-root',
    derivation_chain: ['old-root', 'same-root'] };
  const rent = [observation('first', { source }), observation('scan', { value: '25', method: 'ocr', source })];
  const term = observation('term', { fact_key: 'term_months', value: '60', unit: 'months', source });
  delete term.expense_basis;
  const args = { entity_ref: 'synthetic-lineage-deal', observations: [term, rent[0], rent[1]] };
  const original = structuredClone(args);
  const out = await executeRegisteredTool(db, actor, 'compare-fact-observations', args);
  assert.deepEqual(out.shared_source_groups, [
    { fact_key: 'base_rent_per_sf', basis: 'document_ref', source_ref: 'synthetic-lease-v1', observation_ids: ['first', 'scan'] },
    { fact_key: 'base_rent_per_sf', basis: 'content_hash', source_ref: 'c'.repeat(64), observation_ids: ['first', 'scan'] },
    { fact_key: 'base_rent_per_sf', basis: 'document_family_id', source_ref: 'same-root', observation_ids: ['first', 'scan'] },
    { fact_key: 'base_rent_per_sf', basis: 'document_family_id', source_ref: 'old-root', observation_ids: ['first', 'scan'] },
  ]);
  assert.deepEqual(out.fields, [
    { fact_key: 'term_months', status: 'single_observation', observation_ids: ['term'] },
    { fact_key: 'base_rent_per_sf', status: 'conflict', observation_ids: ['first', 'scan'] },
  ]);
  assert.deepEqual(out.review_items[0].observations.map(o => o.input), rent);
  assert.deepEqual(out.review_items[0].observations.map(o => o.effective_confidence_tier), ['auto_accept', 'human_review']);
  assert.deepEqual(out.review_items[0].reasons, ['conflict', 'human_review', 'ocr']);
  assert.equal(out.review_items[0].priority, 'high');
  assert.equal(out.canonical, false);
  assert.equal(out.records_written, 0);
  assert.equal(out.evidence_independence, 'not_assessed');
  assert.deepEqual(args, original);
});

test('monthly and annual rent agree exactly without confirming supplied citations or facts', async () => {
  assert.equal(TOOLS['compare-fact-observations']?.write, false);
  const input = [observation('annual'), observation('monthly', { value: '2.00', unit: 'USD/SF/month' })];
  const expectedInput = structuredClone(input);
  const out = await compare(input);
  assert.deepEqual(input, expectedInput);
  assert.equal(out.schema_version, 'fact-observation-comparison.v1');
  assert.equal(out.entity_ref, 'synthetic-deal-1');
  assert.equal(out.canonical, false);
  assert.equal(out.records_written, 0);
  assert.equal(out.source_verification, 'unverified');
  assert.deepEqual(out.observations.map(o => o.input), expectedInput);
  assert.deepEqual(out.observations.map(o => o.normalized), [
    { value: '24', unit: 'USD/SF/year', expense_basis: 'nnn' },
    { value: '24', unit: 'USD/SF/year', expense_basis: 'nnn' },
  ]);
  assert.deepEqual(out.fields, [{ fact_key: 'base_rent_per_sf', status: 'agreement', observation_ids: ['annual', 'monthly'] }]);
  assert.deepEqual(out.conflicts, []);
});

test('sub-cent rent disagreement remains a named conflict with both citations', async () => {
  const out = await compare([observation('lease'), observation('counter', { value: '24.0000000001' })]);
  assert.equal(out.fields[0].status, 'conflict');
  assert.deepEqual(out.conflicts, [{ fact_key: 'base_rent_per_sf', observation_ids: ['lease', 'counter'] }]);
  assert.equal(out.observations[1].normalized.value, '24.0000000001');
});

test('different expense bases cannot become agreement even with equal rent amounts', async () => {
  const out = await compare([observation('nnn'), observation('gross', { expense_basis: 'full_service_gross' })]);
  assert.equal(out.fields[0].status, 'conflict');
  assert.deepEqual(out.conflicts[0].observation_ids, ['nnn', 'gross']);
});

test('one high-confidence observation and agreeing OCR observations remain unconfirmed', async () => {
  const single = await compare([observation('single')]);
  assert.equal(single.fields[0].status, 'single_observation');
  for (const out of [single, await compare([observation('ocr-a', { method: 'ocr' }), observation('ocr-b', { method: 'ocr' })])]) {
    assert.equal(out.canonical, false);
    assert.equal(out.records_written, 0);
    assert.equal(out.entity_binding, 'caller_asserted');
    assert.equal(out.source_verification, 'unverified');
  }
});

test('term, free-rent and TI values use their own contracts and remain separate fields', async () => {
  const economic = (id, fact_key, value, unit) => {
    const o = observation(id, { fact_key, value, unit });
    delete o.expense_basis;
    return o;
  };
  const out = await compare([
    economic('years', 'term_months', '5.125', 'years'), economic('months', 'term_months', '61.5', 'months'),
    economic('free', 'free_rent_months', '0.5', 'months'),
    economic('ti', 'landlord_ti_per_sf', '50.000000000000000001', 'USD/SF'),
  ]);
  assert.deepEqual(out.observations.map(o => o.normalized), [
    { value: '61.5', unit: 'months' }, { value: '61.5', unit: 'months' },
    { value: '0.5', unit: 'months' }, { value: '50.000000000000000001', unit: 'USD/SF' },
  ]);
  assert.deepEqual(out.fields.map(f => [f.fact_key, f.status]), [
    ['term_months', 'agreement'], ['free_rent_months', 'single_observation'], ['landlord_ti_per_sf', 'single_observation'],
  ]);
});

test('malformed observations refuse without returning source text or accepting authority claims', async () => {
  const bad = [
    { value: '24\n' }, { value: '1.2\n' }, { value: '-1' }, { value: 'NaN' }, { value: '1e2' }, { value: 24 }, { value: '01' },
    { value: '1.0000000000000000001' }, { value: '9'.repeat(31) }, { value: '' },
    { fact_key: ['base_rent_per_sf'] }, { unit: ['USD/SF/year'] }, { fact_key: 'unknown' }, { fact_key: '__proto__' }, { unit: 'years' }, { expense_basis: undefined },
    { source: { ...observation('x').source, page_start: 0 } },
    { source: { ...observation('x').source, page_start: 3, page_end: 2 } },
    { source: { ...observation('x').source, page_end: 2.5 } },
    { source: { ...observation('x').source, cited_text: ' ' } },
    { source: { ...observation('x').source, content_hash: 'claimed' } },
    { source: { ...observation('x').source, document_ref: ' ' } },
    { source: { ...observation('x').source, cited_text: 'x'.repeat(4001) } },
    { id: ' ' }, { extractor_version: ' ' }, { method: 'invented' }, { confidence_tier: 'confirmed' },
    { canonical: true }, { tenant: 'other' }, { authority: 'human_reviewed' },
  ];
  for (const patch of bad) await assert.rejects(() => compare([observation('bad', patch)]), error => {
    assert.equal(error.payload?.error, 'validation_error');
    assert.equal(JSON.stringify(error.payload).includes('Base rent is'), false);
    return true;
  });
  for (const observations of [[], [observation('same'), observation('same')], Array.from({ length: 101 }, (_, i) => observation(String(i)))])
    await assert.rejects(() => compare(observations), error => error.payload?.error === 'validation_error');
  await assert.rejects(() => TOOLS['compare-fact-observations'].handler(db, actor, {
    entity_ref: 'synthetic-deal-1', observations: [observation('a')], canonical: true,
  }), error => error.payload?.error === 'validation_error');
});

test('non-rent facts refuse rent basis and incompatible units', async () => {
  for (const patch of [
    { fact_key: 'term_months', unit: 'months' },
    { fact_key: 'landlord_ti_per_sf', unit: 'USD/SF' },
    { fact_key: 'free_rent_months', unit: 'years', expense_basis: undefined },
  ]) await assert.rejects(() => compare([observation('bad', patch)]), error => error.payload?.error === 'validation_error');
});

test('registered dispatch handles decimals beyond binary precision and never accesses records', async () => {
  for (const [value, expected] of [
    ['9007199254740993', '108086391056891916'],
    ['0.000000000000000001', '0.000000000000000012'], ['0.000', '0'],
  ]) {
    const out = await executeRegisteredTool(db, actor, 'compare-fact-observations', {
      entity_ref: 'synthetic-deal-1', observations: [observation('boundary', { value, unit: 'USD/SF/month' })],
    });
    assert.equal(out.observations[0].normalized.value, expected);
    assert.equal(out.evidence_independence, 'not_assessed');
    assert.equal(out.canonical, false);
  }
  await assert.rejects(() => executeRegisteredTool(db, actor, 'compare-fact-observations', {
    entity_ref: 'synthetic-deal-1', observations: [observation('bad', { fact_key: ['base_rent_per_sf'] })],
  }), error => error.payload?.error === 'validation_error');
});


test('invalid comparison input reaches callers as a public validation error', async t => {
  t.mock.method(console, 'error', () => {});
  await assert.rejects(() => compare([observation('bad', { value: '-1' })]), error => {
    const response = publicError(error, { inputSchema: TOOLS['compare-fact-observations'].inputSchema });
    assert.equal(response.error, 'validation_error');
    assert.equal(JSON.stringify(response).includes('Base rent is'), false);
    return true;
  });
});

test('conflicting auto-accept observations produce one high-priority review item', async () => {
  const out = await compare([
    observation('lease'), observation('counter', { value: '25' }), observation('revision', { value: '25' }),
  ]);
  assert.deepEqual(out.review_items.map(({ observations, ...item }) => item), [{
    entity_ref: 'synthetic-deal-1', fact_key: 'base_rent_per_sf', priority: 'high',
    observation_ids: ['lease', 'counter', 'revision'], reasons: ['conflict'],
  }]);
  assert.equal(out.canonical, false);
  assert.equal(out.records_written, 0);
});

test('review tiers route once per field with high priority before verify-band fields', async () => {
  const economic = (id, fact_key, value, unit, confidence_tier) => {
    const item = observation(id, { fact_key, value, unit, confidence_tier });
    delete item.expense_basis;
    return item;
  };
  const out = await compare([
    economic('term-verify', 'term_months', '60', 'months', 'verify_band'),
    observation('rent-review', { confidence_tier: 'human_review' }),
    observation('rent-verify', { confidence_tier: 'verify_band' }),
    economic('ti-review', 'landlord_ti_per_sf', '50', 'USD/SF', 'human_review'),
    economic('free-verify', 'free_rent_months', '1', 'months', 'verify_band'),
  ]);
  assert.deepEqual(out.review_items.map(({ observations, ...item }) => item), [
    { entity_ref: 'synthetic-deal-1', fact_key: 'base_rent_per_sf', priority: 'high',
      observation_ids: ['rent-review', 'rent-verify'], reasons: ['human_review', 'verify_band'] },
    { entity_ref: 'synthetic-deal-1', fact_key: 'landlord_ti_per_sf', priority: 'high',
      observation_ids: ['ti-review'], reasons: ['human_review'] },
    { entity_ref: 'synthetic-deal-1', fact_key: 'term_months', priority: 'normal',
      observation_ids: ['term-verify'], reasons: ['verify_band'] },
    { entity_ref: 'synthetic-deal-1', fact_key: 'free_rent_months', priority: 'normal',
      observation_ids: ['free-verify'], reasons: ['verify_band'] },
  ]);
});

test('mixed text and OCR fields require OCR human review without changing supplied evidence', async () => {
  const text = observation('text');
  const ocr = observation('ocr', { method: 'ocr', fact_key: 'term_months', value: '60', unit: 'months' });
  delete ocr.expense_basis;
  const input = [text, ocr];
  const original = structuredClone(input);
  const out = await compare(input);
  assert.deepEqual(out.observations.map(o => o.effective_confidence_tier), ['auto_accept', 'human_review']);
  assert.deepEqual(out.observations.map(o => o.input), original);
  assert.deepEqual(input, original);
  assert.deepEqual(out.review_items.map(({ observations, ...item }) => item), [{
    entity_ref: 'synthetic-deal-1', fact_key: 'term_months', priority: 'high',
    observation_ids: ['ocr'], reasons: ['human_review', 'ocr'],
  }]);
  assert.equal(out.canonical, false);
  assert.equal(out.records_written, 0);
});

test('registered dispatch routes all OCR tiers to human review even when values agree', async () => {
  for (const confidence_tier of ['auto_accept', 'verify_band', 'human_review']) {
    const args = { entity_ref: 'synthetic-dispatch-deal', observations: [
      observation('text'), observation('scan', { method: 'ocr', confidence_tier }),
    ] };
    const out = await executeRegisteredTool(db, actor, 'compare-fact-observations', args);
    assert.equal(out.fields[0].status, 'agreement');
    assert.deepEqual(out.observations.map(o => o.effective_confidence_tier), ['auto_accept', 'human_review']);
    assert.deepEqual(out.review_items.map(({ observations, ...item }) => item), [{
      entity_ref: 'synthetic-dispatch-deal', fact_key: 'base_rent_per_sf', priority: 'high',
      observation_ids: ['text', 'scan'], reasons: ['human_review', 'ocr'],
    }]);
    assert.deepEqual(out.observations.map(o => o.input), args.observations);
    assert.equal(out.evidence_independence, 'not_assessed');
    assert.equal(out.canonical, false);
    assert.equal(out.records_written, 0);
  }
});

test('conflict and tier reasons share one review item containing every competing observation', async () => {
  const out = await compare([
    observation('scan', { method: 'ocr' }),
    observation('verify', { value: '25', confidence_tier: 'verify_band' }),
    observation('review', { value: '26', confidence_tier: 'human_review' }),
  ]);
  assert.deepEqual(out.review_items.map(({ observations, ...item }) => item), [{
    entity_ref: 'synthetic-deal-1', fact_key: 'base_rent_per_sf', priority: 'high',
    observation_ids: ['scan', 'verify', 'review'], reasons: ['conflict', 'human_review', 'verify_band', 'ocr'],
  }]);
});

test('text-layer auto-accept agreement and single observations create no review item or canonical fact', async () => {
  for (const observations of [[observation('single')], [observation('a'), observation('b')]]) {
    const out = await compare(observations);
    assert.deepEqual(out.review_items, []);
    assert.deepEqual(out.observations.map(o => o.effective_confidence_tier), observations.map(() => 'auto_accept'));
    assert.equal(out.canonical, false);
    assert.equal(out.records_written, 0);
  }
});

test('a three-document review item includes cited evidence and normalized values for its field only', async () => {
  const rent = [
    observation('loi', { value: '2.00', unit: 'USD/SF/month', source: {
      document_ref: 'synthetic-loi', page_start: 1, page_end: 2, cited_text: 'Base rent is $2 per SF per month.',
    } }),
    observation('counter', { value: '25', confidence_tier: 'verify_band', source: {
      document_ref: 'synthetic-counter', page_start: 3, page_end: 3, cited_text: 'Counter rent is $25 per SF per year.',
    } }),
    observation('lease', { value: '25.00', method: 'ocr', source: {
      document_ref: 'synthetic-scan', page_start: 4, page_end: 5, cited_text: 'Lease rent is $25 per SF per year.',
    } }),
  ];
  const term = observation('term', { fact_key: 'term_months', value: '5', unit: 'years', confidence_tier: 'verify_band' });
  delete term.expense_basis;
  const args = { entity_ref: 'synthetic-deal-1', observations: [rent[0], term, rent[1], rent[2]] };
  const original = structuredClone(args);
  const out = await executeRegisteredTool(db, actor, 'compare-fact-observations', args);
  assert.equal(out.review_items.length, 2);
  assert.deepEqual(out.review_items[0], {
    entity_ref: 'synthetic-deal-1', fact_key: 'base_rent_per_sf', priority: 'high',
    observation_ids: ['loi', 'counter', 'lease'], reasons: ['conflict', 'human_review', 'verify_band', 'ocr'],
    observations: [
      { input: rent[0], effective_confidence_tier: 'auto_accept', normalized: { value: '24', unit: 'USD/SF/year', expense_basis: 'nnn' } },
      { input: rent[1], effective_confidence_tier: 'verify_band', normalized: { value: '25', unit: 'USD/SF/year', expense_basis: 'nnn' } },
      { input: rent[2], effective_confidence_tier: 'human_review', normalized: { value: '25', unit: 'USD/SF/year', expense_basis: 'nnn' } },
    ],
  });
  assert.deepEqual(out.review_items[1], {
    entity_ref: 'synthetic-deal-1', fact_key: 'term_months', priority: 'normal',
    observation_ids: ['term'], reasons: ['verify_band'], observations: [
      { input: term, effective_confidence_tier: 'verify_band', normalized: { value: '60', unit: 'months' } },
    ],
  });
  assert.deepEqual(args, original);
  assert.equal(out.canonical, false);
  assert.equal(out.records_written, 0);
  assert.equal(out.source_verification, 'unverified');
  assert.equal(out.entity_binding, 'caller_asserted');
  assert.equal(out.evidence_independence, 'not_assessed');
});
