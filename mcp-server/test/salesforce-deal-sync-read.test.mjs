// Test Salesforce deal synchronization reads and updates.
import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS } from '../src/tools.js';

test('sync snapshot includes closed deal identities, clients, owners and labeled close dates', async () => {
  const rows = [{ id: 'example-deal', phase: 'closed', outcome: 'won', base_version: 2,
    salesforce_id: '006000000000001', client_ref: 'C-001', client_name: 'Example Practice',
    owner: 'dell', sf_close_date_placeholder: '2026-12-01' }];
  const result = await TOOLS['read-deal-reconciliation'].handler({ query: async sql => {
    assert.match(sql, /v_deal_reconciliation_read/);
    assert.match(sql, /v_export_deals/);
    assert.doesNotMatch(sql, /source_row|commission|where .*outcome|where .*phase/i);
    return { rows };
  } }, {}, { all: true });
  assert.deepEqual(result, { deals: rows });
});

test('bulk sync read cannot silently ignore a deal selector', async () => {
  await assert.rejects(TOOLS['read-deal-reconciliation'].handler({ query: async () => {
    assert.fail('invalid selection must not query');
  } }, {}, { all: true, deal: 'example-deal' }), error => error.payload?.error === 'choose_deal_or_all');
});

const dealId = '00000000-0000-4000-8000-000000000001';
const actor = { id: '00000000-0000-4000-8000-000000000002', slug: 'joe', human: true };
function updateDb() {
  const writes = [];
  return { writes, query: async (sql, args) => {
    if (sql.includes('from tool_call')) return { rows: [] };
    if (sql.includes('from v_ref_index')) return { rows: [{ subject_type: 'deal', subject_id: dealId }] };
    if (sql.startsWith('select version')) return { rows: [{ version: 1 }] };
    if (sql.startsWith('select sf_close_date_placeholder')) return { rows: [{ sf_close_date_placeholder: null }] };
    if (sql.startsWith('update deal') || sql.startsWith('insert into event')) {
      writes.push({ sql, args }); return { rows: [] };
    }
    if (sql.startsWith('insert into tool_call')) return { rows: [] };
    throw new Error(sql);
  } };
}

test('Salesforce close date updates only its labeled placeholder and records an event', async () => {
  const db = updateDb();
  const result = await TOOLS['update-deal'].handler(db, actor, {
    idempotency_key: '00000000-0000-4000-8000-000000000003', deal: dealId, base_version: 1,
    fields: { sf_close_date_placeholder: '2026-12-01' },
  });
  assert.deepEqual(result.updated, ['sf_close_date_placeholder']);
  assert.equal(db.writes.length, 2);
  assert.match(db.writes[0].sql, /sf_close_date_placeholder=/);
  assert.doesNotMatch(db.writes[0].sql, /closed_on|won_value/);
});

test('invalid Salesforce close dates fail before a write', async () => {
  for (const value of ['2026-02-30', '2026-13-01', 'tomorrow', null]) {
    const db = updateDb();
    await assert.rejects(TOOLS['update-deal'].handler(db, actor, {
      idempotency_key: '00000000-0000-4000-8000-000000000003', deal: dealId, base_version: 1,
      fields: { sf_close_date_placeholder: value },
    }), error => error.payload?.error === 'invalid_sf_close_date_placeholder');
    assert.equal(db.writes.length, 0);
  }
});
