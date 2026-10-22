import test from 'node:test';
import assert from 'node:assert/strict';
import { allowedIn, connectionRouteForTool } from '../src/mcp.js';
import { TOOLS, assertRegisteredToolInput } from '../src/tools.js';

const deal = {
  id: 'synthetic-deal', name: 'Synthetic agenda', phase: 'search', owner: 'dell', type: 'lease',
  attention: false, next_step: 'Review', next_date: '2026-10-22', last_touch: null, last_review_at: null,
  workspace_kind: 'national_account', account_client_id: 'synthetic-account', operating_state: 'active',
  outcome: null, closed_on: null, invoiced_on: null,
  critical_dates: [{ id: 'date', kind: 'option', due_on: null, note: null, status: 'settled' }],
  next_actions: [{ id: 'action', owner: 'dell', description: 'Review', due_on: null, status: 'open', hold_until: '2026-10-25' }],
};

test('Home agenda returns the versioned minimal snapshot in one read, retaining national-account scope and held/undated rows', async () => {
  const calls = [];
  const client = { async query(sql, params) { calls.push({ sql, params }); return { rows: [{ overflow: false, deals: [deal] }] }; } };
  const result = await TOOLS['read-home-agenda'].handler(client, { slug: 'joe' }, {});
  assert.deepEqual(result, { schema_version: 'home-agenda.v1', actor: 'joe', deals: [deal] });
  assert.equal(calls.length, 1);
  assert.equal(TOOLS['read-home-agenda'].write, undefined);
  assert.deepEqual(TOOLS['read-home-agenda'].inputSchema, { type: 'object', additionalProperties: false, properties: {} });
});

test('Home agenda refuses overflow instead of returning an incomplete agenda', async () => {
  const client = { async query() { return { rows: [{ overflow: true, deals: null }] }; } };
  await assert.rejects(() => TOOLS['read-home-agenda'].handler(client, { slug: 'joe' }, {}), error => {
    assert.equal(error.payload.error, 'agenda_limit_exceeded');
    assert.deepEqual(error.payload.limits, { deals: 1000, critical_dates: 10000, open_actions: 10000 });
    return true;
  });
});

test('Home agenda is admitted as a read-only registered operation with no caller-controlled scope', async () => {
  await assert.doesNotReject(() => assertRegisteredToolInput('read-home-agenda', TOOLS['read-home-agenda'], {}));
  await assert.rejects(() => assertRegisteredToolInput('read-home-agenda', TOOLS['read-home-agenda'], { owner: 'joe' }), /unregistered_operation_fields/);
});

test('Home agenda uses the MCP reader route and authenticated app full profile', () => {
  assert.equal(connectionRouteForTool(TOOLS['read-home-agenda']), 'reader');
  assert.equal(allowedIn('full', 'read-home-agenda', TOOLS['read-home-agenda']), true);
});
