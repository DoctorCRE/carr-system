// Test invoice undo authority dispatch and acting identity.
import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';

const actorId = '10000000-0000-4000-8000-000000000001';
const invoiceId = '10000000-0000-4000-8000-000000000002';
const dealId = '10000000-0000-4000-8000-000000000003';
const key = '10000000-0000-4000-8000-000000000004';
const args = { idempotency_key: key, invoice_id: invoiceId, base_version: 2 };
const sponsored = (slug = 'codex', sponsor = 'joe') => ({ id: actorId, slug, human: false,
  native_agent_verified: true, sponsoring_human_slug: sponsor, via: 'oauth-agent' });

function syntheticClient({ version = 2, phase = 'closed', date = '2026-10-03',
  phaseEvent = 'phase-event', invoiceEvent = 'invoice-event', status = 'applied' } = {}) {
  const writes = [];
  return { writes, query: async (sql, params = []) => {
    if (/^select \* from deal_invoice_email/.test(sql)) return { rows: [{ id: invoiceId, deal_id: dealId,
      status, email_date: '2026-10-03', prior_phase: 'legal', prior_invoiced_on: null,
      phase_event_id: 'phase-event', invoice_event_id: 'invoice-event',
      reason: 'Invoice received 2026-10-03', evidence_ref: 'local-mail:synthetic-invoice' }] };
    if (/^select phase,invoiced_on,version from deal/.test(sql)) return { rows: [{ phase, invoiced_on: date, version }] };
    if (/^select status from deal_invoice_email/.test(sql)) return { rows: [{ status }] };
    if (/select distinct on \(field\)/.test(sql)) return { rows: [
      { field: 'phase', id: phaseEvent }, { field: 'invoiced_on', id: invoiceEvent }] };
    if (/^select subject_type, subject_id from v_ref_index/.test(sql)) return { rows: [{ subject_type: 'deal', subject_id: dealId }] };
    if (/^select version from deal/.test(sql)) return { rows: [{ version }] };
    if (/^select phase,invoiced_on from deal/.test(sql)) return { rows: [{ phase, invoiced_on: date }] };
    if (/^select id from actor/.test(sql)) return { rows: [{ id: actorId }] };
    if (/dealroom:status-plan/.test(sql)) return { rows: [{ action_id: null, overdue: false, next_step: null, next_date: null }] };
    if (/^(update |insert into event|insert into tool_call)/.test(sql.trim())) {
      writes.push({ sql, params }); return { rows: [] };
    }
    if (/^select (pg_advisory_xact_lock|request_hash, response from tool_call)/.test(sql)) return { rows: [] };
    assert.fail(`Unexpected database operation: ${sql}`);
  } };
}

const permitted = [
  ...['joe', 'dell'].map(slug => ({ id: actorId, slug, human: true, via: 'oauth' })),
  sponsored('codex', 'joe'), sponsored('claude', 'dell'),
  { ...sponsored('joe-local', 'joe'), via: 'local-token' },
  { ...sponsored('dell-local', 'dell'), via: 'local-token' },
];
for (const actor of permitted) {
  test(`invoice undo restores prior phase and date for ${actor.slug} with acting identity`, async () => {
    const db = syntheticClient();
    assert.deepEqual(await executeRegisteredTool(db, actor, 'undo-invoice-close', { ...args }),
      { ok: true, deal_id: dealId, phase: 'legal', invoiced_on: null, undone_by: actorId });
    assert.deepEqual(db.writes.find(({ sql }) => /^update deal set phase/.test(sql)).params,
      [actorId, 'legal', null, dealId]);
    assert.deepEqual(db.writes.find(({ sql }) => /^update deal_invoice_email/.test(sql)).params, [invoiceId, actorId]);
    const event = db.writes.find(({ params }) => params[2] === 'undo-invoice-close');
    assert.equal(event.params[1], actorId);
    assert.equal(event.params[14], actor.sponsoring_human_slug || actor.slug);
    assert.match(event.sql, /'human_correction'/);
    assert.deepEqual(JSON.parse(event.params[7]), { phase: 'legal', invoiced_on: null, undone_invoice_id: invoiceId,
      undone_by: actorId, reason: 'Undid: Invoice received 2026-10-03', evidence_ref: 'local-mail:synthetic-invoice' });
    const receipt = db.writes.find(({ sql, params }) => /^insert into tool_call/.test(sql.trim()) && params[1] === 'undo-invoice-close');
    assert.equal(receipt.params[2], actorId);
    assert.equal(receipt.params[8], actor.sponsoring_human_slug || actor.slug);
  });
}

for (const [label, actor, error] of [
  ['unsponsored agent', { id: actorId, slug: 'codex', human: false }],
  ['unverified sponsor', { ...sponsored(), native_agent_verified: false }],
  ['reviewer', { id: actorId, slug: 'reviewer', human: false, review: true }],
  ['unknown human', { id: actorId, slug: 'unknown', human: true }],
  ['unattended flag', { ...sponsored('joe-local'), unattended: true }, 'unattended_runner_authority_not_admitted'],
  ['unattended transport', { ...sponsored('dell-local', 'dell'), via: 'unattended-token' }, 'unattended_runner_authority_not_admitted'],
]) {
  test(`invoice undo refuses ${label} before database access`, async () => {
    const db = { query: async () => assert.fail('Unauthorized caller reached the database') };
    await assert.rejects(executeRegisteredTool(db, actor, 'undo-invoice-close', { ...args }),
      e => e.payload?.error === (error || 'human_only_verb_requires_verified_partner'));
  });
}

for (const [label, state, error] of [
  ['stale version', { version: 3 }],
  ['newer phase', { phase: 'legal' }],
  ['newer invoice date', { date: '2026-10-04' }],
  ['newer phase event', { phaseEvent: 'newer-phase-event' }],
  ['newer invoice event', { invoiceEvent: 'newer-invoice-event' }],
  ['already undone invoice', { status: 'undone' }, 'invoice_close_not_applied'],
]) {
  test(`sponsored invoice undo preserves ${label} without mutation`, async () => {
    const db = syntheticClient(state);
    await assert.rejects(executeRegisteredTool(db, sponsored(), 'undo-invoice-close', { ...args }),
      e => e.payload?.error === (error || 'newer_invoice_change_exists'));
    assert.deepEqual(db.writes, []);
  });
}
