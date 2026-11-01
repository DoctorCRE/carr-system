import { test } from 'node:test';
import assert from 'node:assert/strict';
import { emailProposalTools } from '../src/email-proposals.js';

const queue = emailProposalTools({})['email-router-queue'];
const actor = { human: true, slug: 'joe', id: '30000000-0000-0000-0000-000000000001' };

test('filed link sources retain exact deals and page without an age window', async () => {
  const rows = [
    { activity_id: '10000000-0000-0000-0000-000000000001', deal: 'synthetic-deal',
      body: 'Recovered synthetic document', occurred_at: '2026-01-01T00:00:00Z' },
    { activity_id: '10000000-0000-0000-0000-000000000002', deal: 'second-deal', body: 'Other' },
  ];
  const calls = [];
  const db = { query: async (sql, args) => { calls.push({ sql, args }); return { rows: calls.length === 1 ? rows : [] }; } };
  const first = await queue.handler(db, actor, { purpose: 'links', limit: 1 });
  assert.deepEqual(first.emails, rows.slice(0, 1));
  assert.equal(first.more, true);
  assert.equal(first.next_cursor, rows[0].activity_id);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].args, [null, 2, 'joe', actor.id]);
  assert.match(calls[0].sql, /a\.deal_id is not null/);
  assert.match(calls[0].sql, /a\.kind in \('email_in','email_out'\)/);
  assert.doesNotMatch(calls[0].sql, /make_interval/);
  assert.match(calls[0].sql, /order by a\.id limit/);
  assert.match(calls[0].sql, /mailbox_owner/);
});

test('hydrated text can only come from the same source actor and record', async () => {
  let query;
  let parameters;
  const db = { query: async (sql, args) => { query = sql; parameters = args; return { rows: [] }; } };
  const after = '10000000-0000-0000-0000-000000000001';
  const result = await queue.handler(db, actor, { purpose: 'links', after });
  assert.deepEqual(result.emails, []);
  assert.equal(result.next_cursor, null);
  assert.deepEqual(parameters, [after, 26, 'joe', actor.id]);
  assert.match(query, /h\.email_context->>'source_activity_id'=a\.id::text/);
  assert.match(query, /h\.actor_id=a\.actor_id/);
  for (const field of ['client_id', 'vendor_id', 'lead_id', 'deal_id']) {
    assert.ok(query.includes(`h.${field} is not null and h.${field}=a.${field}`));
  }
});

test('hydration source identity is an optional UUID in the activity context contract', async () => {
  const { TOOLS } = await import('../src/tools.js');
  const context = TOOLS['log-activity'].inputSchema.properties.email_context;
  assert.deepEqual(context.properties.source_activity_id, { type: 'string', format: 'uuid' });
  assert.equal(context.required.includes('source_activity_id'), false);
});

test('ordinary router mode retains the existing bounded classification contract', async () => {
  const calls = [];
  const db = { query: async (sql, args) => { calls.push({ sql, args }); return { rows: [] }; } };
  const result = await queue.handler(db, actor, { days: 3, limit: 25, exclude: [] });
  assert.deepEqual(result.emails, []);
  assert.deepEqual(result.open_deals, []);
  assert.equal(result.schema_version, 'email-router-queue.v2');
  assert.match(calls[0].sql, /a\.kind in \('email_in','email_out'\)/);
  assert.match(calls[0].sql, /make_interval\(days=>\$1\)/);
  assert.deepEqual(calls[0].args.slice(0, 5), [3, [], 26, 'joe', actor.id]);
  assert.ok(!Number.isNaN(Date.parse(calls[0].args[5])), 'window start is the server clock');
});


test('outcome reads admit the registered contract and refuse ambiguous inputs before database access', async () => {
  const { TOOLS, assertRegisteredToolInput, ToolError } = await import('../src/tools.js');
  await assertRegisteredToolInput('email-router-queue', TOOLS['email-router-queue'],
    { purpose: 'outcomes', account: 'fixture@example.test' });
  const read = emailProposalTools({ ToolError })['email-router-queue'];
  for (const args of [{ purpose: 'outcomes' },
    { purpose: 'outcomes', account: 'fixture@example.test', days: 3 }]) {
    await assert.rejects(read.handler(null, actor, args),
      error => error.payload.error === 'email_outcomes_arguments_invalid');
  }
  await assert.rejects(read.handler(null, { human: false, slug: 'machine' },
    { purpose: 'outcomes', account: 'fixture@example.test' }),
  error => error.payload.error === 'email_sender_identity_required');
});
