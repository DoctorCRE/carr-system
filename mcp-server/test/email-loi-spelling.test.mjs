import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';
const row = (phase, detail, deal_type = 'lease') => ({ deal_id: 'deal', name: 'Synthetic deal',
  client_id: 'client', version: 1, deal_type, phase, activity_id: 'activity', activity_version: 1,
  summary: 'Synthetic email', detail, occurred_at: '2026-10-20T12:00:00Z' });
const check = (phase, evidence_quote) => executeRegisteredTool(
  { query: async () => ({ rows: [row(phase, evidence_quote)] }) }, {}, 'preview-email-deal-progress',
  { deal: 'deal', activity_id: 'activity', evidence_quote });

test('dotted LOI evidence supports the same reviewable deal progress as LOI', async () => {
  assert.equal((await check('site_selection', 'We received the L.O.I. yesterday.')).to_phase, 'negotiation');
  assert.equal((await check('negotiation', 'The L.O.I. was signed by both parties.')).to_phase, 'legal');
});

test('dotted LOI does not let an unsigned lease qualify as lease evidence', async () => {
  await assert.rejects(check('legal', 'The L.O.I. was signed and the lease is attached.'),
    error => error.payload?.error === 'email_deal_quote_invalid');
});

test('unrelated words do not qualify as an LOI milestone', async () => {
  await assert.rejects(check('site_selection', 'The soil report arrived yesterday.'),
    error => error.payload?.error === 'email_deal_quote_invalid');
});
