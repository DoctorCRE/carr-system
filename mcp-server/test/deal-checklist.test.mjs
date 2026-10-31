// Test doctrine-derived deal checklists and sourced deadlines.
import test from 'node:test';
import assert from 'node:assert/strict';
import { deriveDealChecklist, CHECKLIST_SOURCES } from '../src/deal-checklist.ts';

const sources = () => CHECKLIST_SOURCES.map(source => ({ id: source.section_id, body_hash: source.content_hash, status: 'active' }));
const checklist = (record = {}) => deriveDealChecklist({ type: 'renewal', phase: 'legal', critical_dates: [], ...record }, sources(), '2026-10-20');

test('renewal checklist follows doctrine phases and keeps missing documents visible', () => {
  const result = checklist();
  assert.equal(result.schema_version, 'deal-checklist.v1');
  assert.equal(result.template_state, 'current');
  assert.deepEqual(result.phases.map(phase => phase.id), ['research', 'site_selection', 'negotiation', 'legal', 'closing', 'invoice']);
  const items = result.phases.flatMap(phase => phase.items);
  assert.equal(items.find(item => item.id === 'current_lease_abstraction').status, 'not_recorded');
  assert.equal(items.find(item => item.id === 'executed_lease').status, 'not_recorded');
  assert.equal(items.some(item => item.id === 'title_commitment'), false);
  assert.ok(items.every(item => item.source.section_id || item.source.request));
});

test('sourced open critical dates surface due work and cleared dates disappear', () => {
  const result = checklist({ critical_dates: [
    { id: 'due', kind: 'attorney_review', due_on: '2026-10-20', source: 'fixture:attorney', status: 'open' },
    { id: 'late', kind: 'dd_expiration', due_on: '2026-10-19', source: 'fixture:psa', status: 'open' },
    { id: 'future', kind: 'possession', due_on: '2026-10-25', source: 'fixture:lease', status: 'open' },
    { id: 'cleared', kind: 'option', due_on: '2026-10-18', source: 'fixture:lease', status: 'cleared' },
    { id: 'unsourced', kind: 'option', due_on: '2026-10-18', source: '', status: 'open' },
  ] });
  assert.deepEqual(result.due_items.map(item => [item.id, item.status]), [['critical-date:late', 'overdue'], ['critical-date:due', 'due']]);
  assert.equal(result.phases.flatMap(phase => phase.items).find(item => item.id === 'critical-date:future').status, 'upcoming');
  assert.equal(result.due_items[0].evidence_ref, 'fixture:psa');
});

test('current lease and invoice facts prove completion; phase and draft documents do not', () => {
  const result = checklist({ phase: 'closed', invoiced_on: '2026-10-19',
    lease: { id: 'lease', status: 'current', executed_on: '2026-10-17', expiration_on: '2036-10-16', evidence_ref: 'fixture:lease' },
    commissions: [{ commission_id: 'one', status: 'received', received_on: '2026-10-20' }, { commission_id: 'two', status: 'invoiced' }],
    documents: [{ sent_status: 'draft', note: 'LOI accepted' }],
  });
  const items = result.phases.flatMap(phase => phase.items);
  assert.equal(items.find(item => item.id === 'executed_lease').status, 'complete');
  assert.equal(items.find(item => item.id === 'expiration_recorded').status, 'complete');
  assert.equal(items.find(item => item.id === 'invoice_recorded').status, 'complete');
  assert.equal(items.find(item => item.id === 'commission_received').status, 'not_recorded');
  assert.equal(items.find(item => item.id === 'business_terms').status, 'not_recorded');
  assert.equal(items.find(item => item.id === 'loi_submission').status, 'not_recorded');
});

test('a changed or retired doctrine section disables stale checklist instructions', () => {
  for (const change of [{ body_hash: 'changed' }, { status: 'retired' }]) {
    const rows = sources(); Object.assign(rows[0], change);
    const result = deriveDealChecklist({ type: 'renewal' }, rows, '2026-10-20');
    assert.equal(result.template_state, 'source_changed');
    assert.deepEqual(result.phases, []);
  }
});

test('purchase and land use tailored diligence; unknown deal types do not silently become leases', () => {
  const ids = type => checklist({ type }).phases.flatMap(phase => phase.items).map(item => item.id);
  assert.ok(ids('purchase').includes('title_commitment'));
  assert.equal(ids('purchase').includes('feasibility'), false);
  assert.ok(ids('land').includes('feasibility'));
  assert.equal(checklist({ type: 'unknown' }).template_state, 'deal_type_unknown');
  assert.equal(ids('unknown').includes('executed_lease'), false);
});

test('several dates for the same milestone retain every deadline independently', () => {
  const result = checklist({ critical_dates: [
    { id: 'one', kind: 'executed_lease', due_on: '2026-10-19', status: 'open', source: 'fixture:first' },
    { id: 'two', kind: 'executed_lease', due_on: '2026-10-20', status: 'open', source: 'fixture:second' },
  ] });
  assert.deepEqual(result.due_items.map(item => item.id), ['critical-date:one', 'critical-date:two']);
});

test('requested deadlines enter the existing due list', () => {
  const result = checklist({ requests: [
    { id: 'one', row_key: 'requirements', owner: 'joe', due_on: '2026-10-19' },
    { id: 'two', row_key: 'market_survey', owner: 'joe', due_on: '2026-10-20' },
  ] });
  assert.deepEqual(result.due_items.map(item => [item.id, item.status]), [['requirements', 'overdue'], ['market_survey', 'due']]);
});
