// Tests CRE deal authority admission and dispatch.
import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';
import { registeredOperation } from '../src/mutation-registry.js';

const id = '10000000-0000-4000-8000-000000000001';
const key = '10000000-0000-4000-8000-000000000002';
const cases = [
  ['commit-winning-property', 'assignment', 'winner_selection_commitment'],
  ['record-deal-closing', 'deal', 'final_closing_settlement'],
  ['cancel-pending-deal', 'deal', 'deal_failure_record'],
];
const sponsored = (slug, sponsor) => ({ id, slug, human: false, native_agent_verified: true,
  sponsoring_human_slug: sponsor, via: 'oauth-agent' });
const denied = [
  ...[['codex', 'joe'], ['claude', 'dell'], ['joe-local', 'joe'], ['dell-local', 'dell']]
    .map(([slug, sponsor]) => [slug, sponsored(slug, sponsor)]),
  ['unattended flag', { ...sponsored('joe-local', 'joe'), unattended: true }],
  ['unattended transport', { ...sponsored('dell-local', 'dell'), via: 'unattended-token' }],
  ['unsponsored agent', { id, slug: 'codex', human: false }],
  ['unverified sponsor', { ...sponsored('codex', 'joe'), native_agent_verified: false }],
  ['reviewer', { id, slug: 'reviewer', human: false, review: true }],
  ['unknown human', { id, slug: 'unknown', human: true }],
];

function missingSubjectClient(slug) {
  const calls = [];
  return { calls, query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes('ops.f01_principal()')) return { rows: [{
      principal: { actor_slug: slug, human: true, authorization_class: 'verified_partner' },
      sponsoring_partner: slug, server_now: '2026-10-23T12:00:00.000Z',
    }] };
    if (/^(select request_hash, response from tool_call|insert into tool_call)/.test(sql.trim()) ||
        sql.includes('ops.j102_replay_outcome') || sql.includes('ops.j102_subject(')) return { rows: [] };
    assert.fail(`Unexpected database operation: ${sql}`);
  } };
}

for (const [name, subject_kind, evidence_kind] of cases) {
  const args = { idempotency_key: key,
    subject_ref: { subject_kind, subject_id: 'synthetic-subject' },
    evidence_refs: [{ evidence_kind, record_id: 'synthetic-record' }],
  };
  test(`${name} publishes literal-human admission and preserves authority routing`, () => {
    assert.equal(TOOLS[name].humanCallerOnly, true);
    assert.equal(registeredOperation(name).human_only, true);
    assert.equal(registeredOperation(name).authority_only, true);
  });
  for (const [label, actor] of denied) {
    test(`${name} refuses ${label} before database access`, async () => {
      const db = { query: async () => assert.fail('Unauthorized caller reached the database') };
      await assert.rejects(executeRegisteredTool(db, actor, name, { ...args }),
        error => error.payload?.error === 'human_only_verb_requires_verified_partner');
    });
  }
  for (const slug of ['joe', 'dell']) {
    test(`${name} admits ${slug} and preserves the store's missing-subject refusal`, async () => {
      const db = missingSubjectClient(slug);
      const result = await executeRegisteredTool(db, { id, slug, human: true, via: 'oauth-google' }, name, { ...args });
      assert.equal(result.ok, false);
      assert.equal(result.reason_id, 'subject_not_found');
      assert.equal(result.actor_slug, slug);
      assert.equal(result.records_written, 0);
      assert.deepEqual(db.calls.find(({ sql }) => sql.includes('ops.j102_subject(')).params,
        [subject_kind, 'synthetic-subject']);
      const receipt = db.calls.find(({ sql }) => sql.trim().startsWith('insert into tool_call'));
      assert.equal(receipt.params[2], id);
      assert.equal(receipt.params[8], slug);
      assert.deepEqual(JSON.parse(receipt.params[4]), result);
    });
  }
}
