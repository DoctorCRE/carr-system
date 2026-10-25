import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';

const actorId = '10000000-0000-4000-8000-000000000001';
const draftId = '10000000-0000-4000-8000-000000000002';
const leadId = '10000000-0000-4000-8000-000000000003';
const key = '10000000-0000-4000-8000-000000000004';
const moveId = '10000000-0000-4000-8000-000000000005';
const sponsored = (slug = 'codex', sponsor = 'joe') => ({ id: actorId, slug, human: false,
  native_agent_verified: true, sponsoring_human_slug: sponsor, via: 'oauth-agent' });

function syntheticClient({ undo = false, version = 2, latestMove = moveId, approved = false } = {}) {
  const writes = [];
  return { writes, query: async (sql, params = []) => {
    if (/select d\.\* from lead_contact_draft/.test(sql))
      return { rows: [{ id: draftId, lead_id: leadId, approved_at: approved ? '2026-10-01T12:00:00Z' : null }] };
    if (/select m\.\*,l\.(version|stage)/.test(sql))
      return { rows: [{ id: moveId, lead_id: leadId, version, status: undo ? 'applied' : 'proposed',
        stage: undo ? 'engaged' : 'qualified', from_stage: 'qualified', to_stage: 'engaged',
        activity_id: draftId, evidence_ref: 'fixture:lead-contact', reason: 'Synthetic reply' }] };
    if (/select id,new_value from event/.test(sql))
      return { rows: [{ id: draftId, new_value: { move_id: latestMove } }] };
    if (/^update lead set/.test(sql)) {
      writes.push({ sql, params });
      return { rows: [{ id: leadId }], rowCount: 1 };
    }
    if (/^(update lead_contact_draft|update lead_stage_move|insert into event|insert into tool_call)\b/.test(sql.trim())) {
      writes.push({ sql, params });
      return { rows: [] };
    }
    if (/^select (pg_advisory_xact_lock|request_hash, response from tool_call)/.test(sql)) return { rows: [] };
    assert.fail(`Unexpected database operation: ${sql}`);
  } };
}

test('verified sponsored draft approval completes without sending or moving the lead', async () => {
  const db = syntheticClient();
  const result = await executeRegisteredTool(db, sponsored(), 'approve-lead-draft',
    { idempotency_key: key, draft_id: draftId });
  assert.deepEqual(result, { ok: true, draft_id: draftId, approved: true, sent: false });
  assert.equal(db.writes.filter(({ sql }) => /^update lead set/.test(sql)).length, 0);
  assert.equal(db.writes.find(({ sql }) => /^update lead_contact_draft/.test(sql)).params[1], actorId);
});

test('verified sponsored move approval retains the proposal evidence', async () => {
  const result = await executeRegisteredTool(syntheticClient(), sponsored(), 'approve-lead-move',
    { idempotency_key: key, move_id: moveId, base_version: 2 });
  assert.deepEqual(result, { ok: true, stage: 'engaged', evidence_ref: 'fixture:lead-contact' });
});

test('verified sponsored undo restores the latest move', async () => {
  const result = await executeRegisteredTool(syntheticClient({ undo: true }), sponsored(), 'undo-lead-move',
    { idempotency_key: key, move_id: moveId, base_version: 2 });
  assert.deepEqual(result, { ok: true, stage: 'qualified', undone_move_id: moveId, undone_by: actorId });
});

const cases = [
  ['approve-lead-draft', { draft_id: draftId }, { ok: true, draft_id: draftId, approved: true, sent: false }],
  ['approve-lead-move', { move_id: moveId, base_version: 2 }, { ok: true, stage: 'engaged', evidence_ref: 'fixture:lead-contact' }],
  ['undo-lead-move', { move_id: moveId, base_version: 2 }, { ok: true, stage: 'qualified', undone_move_id: moveId, undone_by: actorId }],
];
const permitted = [
  ...['joe', 'dell'].map(slug => ({ id: actorId, slug, human: true, via: 'oauth' })),
  sponsored('codex', 'joe'), sponsored('claude', 'dell'),
  sponsored('joe-local', 'joe'), sponsored('dell-local', 'dell'),
  { ...sponsored('joe-local', 'joe'), unattended: true, via: 'unattended-token' },
  { ...sponsored('dell-local', 'dell'), unattended: true, via: 'unattended-token' },
];
for (const [name, args, expected] of cases) {
  for (const actor of permitted) {
    test(`${name} completes for ${actor.slug} via ${actor.via} and preserves acting identity`, async () => {
      const db = syntheticClient({ undo: name === 'undo-lead-move' });
      assert.deepEqual(await executeRegisteredTool(db, actor, name, { idempotency_key: key, ...args }), expected);
      const event = db.writes.find(({ sql }) => /^insert into event/.test(sql.trim()));
      assert.equal(event.params[1], actorId);
      assert.equal(event.params[2], name);
      assert.equal(event.params[14], actor.sponsoring_human_slug || actor.slug);
      assert.match(event.sql, name === 'approve-lead-move' ? /'human_stated'/ :
        name === 'undo-lead-move' ? /'human_correction'/ : /'automation_job'/);
      const receipt = db.writes.find(({ sql }) => /^insert into tool_call/.test(sql.trim()));
      assert.equal(receipt.params[2], actorId);
      assert.equal(receipt.params[8], actor.sponsoring_human_slug || actor.slug);
    });
  }
  for (const [label, actor] of [
    ['unsponsored agent', { id: actorId, slug: 'codex', human: false }],
    ['unverified sponsor', { ...sponsored(), native_agent_verified: false }],
    ['reviewer', { id: actorId, slug: 'reviewer', human: false, review: true }],
    ['unknown human', { id: actorId, slug: 'unknown', human: true }],
  ]) {
    test(`${name} refuses ${label} before database access`, async () => {
      const db = { query: async () => assert.fail('Unauthorized caller reached the database') };
      await assert.rejects(executeRegisteredTool(db, actor, name, { idempotency_key: key, ...args }),
        error => error.payload?.error === 'human_only_verb_requires_verified_partner');
    });
  }
}

for (const name of ['approve-lead-move', 'undo-lead-move']) {
  test(`${name} still refuses stale versions for sponsored authority without mutation`, async () => {
    const db = syntheticClient({ undo: name === 'undo-lead-move', version: 3 });
    await assert.rejects(executeRegisteredTool(db, sponsored(), name,
      { idempotency_key: key, move_id: moveId, base_version: 2 }),
    error => error.payload?.error === (name === 'undo-lead-move' ? 'stale_lead_undo' : 'stale_lead_proposal'));
    assert.deepEqual(db.writes, []);
  });
}

test('sponsored undo refuses a newer stage event without mutation', async () => {
  const db = syntheticClient({ undo: true, latestMove: draftId });
  await assert.rejects(executeRegisteredTool(db, sponsored(), 'undo-lead-move',
    { idempotency_key: key, move_id: moveId, base_version: 2 }),
  error => error.payload?.error === 'newer_stage_change_exists');
  assert.deepEqual(db.writes, []);
});

test('sponsored draft approval still refuses an already approved draft without mutation', async () => {
  const db = syntheticClient({ approved: true });
  await assert.rejects(executeRegisteredTool(db, sponsored(), 'approve-lead-draft',
    { idempotency_key: key, draft_id: draftId }), error => error.payload?.error === 'draft_not_pending');
  assert.deepEqual(db.writes, []);
});
