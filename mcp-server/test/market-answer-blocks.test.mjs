import test from 'node:test';
import assert from 'node:assert/strict';
import { Validator } from '@cfworker/json-schema';
import { docConversationTools } from '../src/doc-conversation.js';
import { validateAnswerBlocks } from '../src/answer-blocks.js';

const id = n => `61700000-0000-4000-8000-${String(n).padStart(12, '0')}`;
class ToolError extends Error { constructor(fields) { super(fields.error); Object.assign(this, fields); } }
const receipt = (overrides = {}) => ({ canonical_fact_id: id(3), entity_ref: id(4),
  fact_key: 'base_rent_per_sf', version: 2, value: '24', unit: 'USD/SF/year', expense_basis: 'nnn',
  confidence: 'human_review', submarket: 'Synthetic North', use_type: 'medical', term_kind: 'executed',
  term_date: '2024-01-01', effective_from: '2024-01-01', effective_to: null, eligible: true,
  context_source: {}, sources: [{ document_ref: id(5), quarantined: false }], ...overrides });
function harness(receipts = [receipt()]) {
  const snapshot = { receipts, coverage: { scope: 'recorded_firm_history_only', history_coverage: 'unknown',
    archive_documents: null, canonical_receipts: receipts.length, classified_receipts: receipts.length,
    recorded_documents: receipts.length, observations: receipts.length, quarantined_extractor_versions: [] } };
  const calls = [], stored = [];
  let fail = false;
  const c = { query: async (sql, values) => {
    calls.push([sql, values]);
    if (sql.includes('market_terms_snapshot')) {
      if (fail) throw new Error('synthetic snapshot timeout');
      return { rows: [{ snapshot: structuredClone(snapshot) }] };
    }
    if (sql.includes('append_doc_conversation_turn')) {
      stored.push({ body: values[2], blocks: values[5] && JSON.parse(values[5]) });
      return { rows: [{ appended: { ok: true, sequence: stored.length, msg_id: values[3] } }] };
    }
    if (sql.includes('v_deal_room_negotiation')) return { rows: [{ missing: false,
      comps: [{ id: id(7), deal_id: id(4), side: 'landlord', rate_norm_sf_yr: '30' }] }] };
    throw new Error(sql);
  } };
  const tool = docConversationTools({ ToolError, withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {} })['add-doc-conversation-turn'];
  const args = { conversation_id: id(1), idempotency_key: id(2), role: 'assistant', body: 'Synthetic study', market_terms: {} };
  return { snapshot, stored, calls, tool, args, timeout: () => { fail = true; },
    append: (patch = {}) => tool.handler(c, { slug: 'joe' }, { ...args, ...patch }) };
}

test('market_terms_selector_appends_sourced_table_and_requested_chart', async () => {
  const h = harness();
  assert.equal(new Validator(h.tool.inputSchema).validate({ ...h.args, chart_view: 'bar' }).valid, true);
  await h.append({ chart_view: 'bar' });
  const [table, chart] = h.stored[0].blocks;
  assert.equal(table.id, 'market_terms');
  assert.match(table.columns.find(c => c.key === 'value').label, /Recorded firm-history market terms/);
  assert.deepEqual(table.rows[0].value, { value: 24, source: { fact_id: id(3), version: 2 } });
  assert.equal(table.rows[0].unit.value, 'USD/SF/year');
  assert.equal(table.rows[0].expense_basis.value, 'nnn');
  assert.match(table.rows[0].coverage.value, /unknown/);
  assert.deepEqual(chart, { type: 'chart.v1', id: 'market_terms_chart', table_id: 'market_terms', kind: 'bar', x: 'entity', y: 'value' });
  validateAnswerBlocks(h.stored[0].blocks, ToolError);
  assert.equal(h.calls.filter(([sql]) => sql.includes('market_terms_snapshot')).length, 1);
});

test('shared rebuild filters half-open periods, unsupported receipts and empty studies', async () => {
  const h = harness([receipt({ value: '20', version: 1, effective_from: '2020-01-01' }), receipt(),
    receipt({ entity_ref: id(9), eligible: false }), receipt({ entity_ref: id(8), submarket: 'Other' })]);
  await h.append({ market_terms: { submarket: 'Synthetic North', use_type: 'medical', as_of: '2024-01-01' } });
  assert.equal(h.stored[0].blocks[0].rows.length, 1);
  assert.equal(h.stored[0].blocks[0].rows[0].value.value, 24);
  await h.append({ market_terms: { submarket: 'Absent' }, chart_view: 'line' });
  assert.deepEqual(h.stored[1].blocks[0].rows, []);
  assert.match(h.stored[1].body, /history coverage.*unknown/i);
});

test('selectors and chart options refuse invalid, ambiguous or caller-built answers before querying', async () => {
  for (const patch of [{ comps_deals: [id(4)] }, { market_terms: null }, { market_terms: { as_of: '2024-02-30' } },
    { market_terms: { unit: 'x' } }, { chart_view: 'pie' }, { role: 'human' }, { answer_blocks: [] },
    { market_terms: undefined, chart_view: 'bar' }, { market_terms: undefined, comps_deals: [id(4), id(4)] }]) {
    const h = harness();
    await assert.rejects(h.append(patch), /answer_blocks_invalid/);
    assert.equal(h.calls.length, 0);
  }
});

test('chart none/bar/line and comps preserve table-only numeric provenance', async () => {
  for (const chart_view of ['none', 'bar', 'line']) {
    const h = harness();
    await h.append({ market_terms: undefined, comps_deals: [id(4)], chart_view });
    const blocks = h.stored[0].blocks;
    assert.equal(blocks.length, chart_view === 'none' ? 1 : 2);
    assert.match(blocks[0].columns[2].label, /Proposed rent/);
    assert.deepEqual(blocks[0].rows[0].rent.source, { record_id: id(7) });
    validateAnswerBlocks(blocks, ToolError);
  }
});

test('mixed units, expense bases and fact kinds cannot become one chart series', async () => {
  for (const patch of [{ unit: 'USD/month' }, { expense_basis: 'gross' }, { expense_basis: null }, { fact_key: 'ti_per_sf' }]) {
    const h = harness([receipt(), receipt({ entity_ref: id(8), ...patch })]);
    await assert.rejects(h.append({ chart_view: 'bar' }), /answer_blocks_invalid/);
    assert.equal(h.stored.length, 0);
    await h.append();
    assert.equal(h.stored[0].blocks[0].rows.length, 2);
  }
});

test('unsafe numbers and missing numeric provenance refuse; null stays unavailable', async () => {
  for (const patch of [{ value: 'Infinity' }, { value: '9007199254740990.1' }, { value: '0.10000000000000001' }, { value: '1e-400' }, { value: '9007199254740993' }, { value: '' },
    { value: '0x10' }, { canonical_fact_id: undefined }, { version: undefined }]) {
    const h = harness([receipt(patch)]);
    await assert.rejects(h.append(), /answer_blocks_invalid/);
    assert.equal(h.stored.length, 0);
  }
  for (const value of [null, '0', '-5', '24.125']) {
    const h = harness([receipt({ value })]);
    await h.append();
    assert.equal(h.stored[0].blocks[0].rows[0].value.value, value === null ? null : Number(value));
  }
});

test('overflow refuses with filter instruction, 200 rows and 10x scan use one snapshot', async () => {
  const h = harness(Array.from({ length: 2000 }, (_, i) => receipt({ entity_ref: id(i + 10), submarket: i < 200 ? 'Keep' : 'Other' })));
  await assert.rejects(h.append(), e => e.error === 'answer_blocks_invalid' && /filter/i.test(e.hint));
  assert.equal(h.stored.length, 0);
  const before = h.calls.length;
  await h.append({ market_terms: { submarket: 'Keep' } });
  assert.equal(h.calls.length - before, 2);
  assert.equal(h.stored[0].blocks[0].rows.length, 200);
});

test('snapshot failure writes nothing and new reads never rewrite stored historical blocks', async () => {
  const h = harness();
  await h.append();
  h.snapshot.receipts[0].eligible = false;
  await h.append();
  assert.equal(h.stored[0].blocks[0].rows.length, 1);
  assert.equal(h.stored[1].blocks[0].rows.length, 0);
  h.timeout();
  await assert.rejects(h.append(), /snapshot timeout/);
  assert.equal(h.stored.length, 2);
});

test('schema selectors stay exclusive and long caveats refuse before append', async () => {
  const h = harness();
  const validator = new Validator(h.tool.inputSchema);
  assert.equal(validator.validate({ ...h.args, comps_deals: [id(4)] }).valid, false);
  await assert.rejects(h.append({ body: 'x'.repeat(20000) }), /answer_blocks_invalid/);
  assert.equal(h.stored.length, 0);
});
