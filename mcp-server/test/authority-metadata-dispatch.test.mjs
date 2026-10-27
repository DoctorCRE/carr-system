// Test authority metadata and authorization during tool dispatch.
import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';
import { registeredOperation } from '../src/mutation-registry.js';

const id = '10000000-0000-4000-8000-000000000001';
const itemId = '10000000-0000-4000-8000-000000000002';
const key = '10000000-0000-4000-8000-000000000003';
const cases = [
  ['approve-retrieval-proposals', { proposal_ids: [id], base_versions: { [id]: 1 }, golden_suite_digest: 'a'.repeat(64) }, true],
  ['retire-retrieval-curation', { target_type: 'concept', target_id: id, base_version: 1, reason: 'Synthetic retirement', golden_suite_digest: 'a'.repeat(64) }, true],
  ['record-deal-item', { deal: id, title: 'Synthetic task', source_ref: 'fixture:task', bullets: [{ kind: 'status', text: 'Synthetic task recorded.' }] }, false],
  ['backfill-deal-items', { deal: id }, false],
  ['instruct-deal-item', { deal: id, item_id: itemId, text: 'Record synthetic instruction.' }, false],
  ['complete-deal-item', { deal: id, item_id: itemId }, false],
  ['resolve-post-call-candidate', { candidate_id: id, accept: false }, false],
];
const partner = slug => ({ id, slug, human: true, via: 'oauth' });
const sponsored = (slug, sponsor) => ({ id, slug, human: false, native_agent_verified: true,
  sponsoring_human_slug: sponsor, via: 'oauth-agent' });
const forbiddenClient = { query: async () => assert.fail('Unauthorized call reached the database') };

for (const [name, args] of cases) {
  test(`${name} publishes partner authority without escalating its database role`, () => {
    assert.equal(registeredOperation(name).human_only, true);
    assert.equal(registeredOperation(name).authority_only, false);
  });
  for (const [label, actor] of [
    ['unsponsored agent', { id, slug: 'codex', human: false }],
    ['unverified sponsor', { ...sponsored('codex', 'joe'), native_agent_verified: false }],
    ['reviewer', { id, slug: 'reviewer', human: false, review: true }],
  ]) {
    test(`${name} refuses ${label} before any database access`, async () => {
      await assert.rejects(executeRegisteredTool(forbiddenClient, actor, name, { idempotency_key: key, ...args }),
        error => error.payload?.error === 'human_only_verb_requires_verified_partner');
    });
  }
}

function syntheticClient() {
  let item;
  return { query: async (sql, params) => {
    if (/from retrieval_proposal/.test(sql)) return { rows: [{ id, version: 1, status: 'pending' }] };
    if (/retire_retrieval_curation/.test(sql)) return { rows: [{ id, status: 'retired' }] };
    if (/assert_situation_retrieval_golden/.test(sql)) return { rows: [{ status: 'pass' }] };
    if (/from v_ref_index/.test(sql)) return { rows: [{ subject_type: 'deal', subject_id: id }] };
    if (/select id from actor/.test(sql)) return { rows: [{ id }] };
    if (/deal-items:backfill-source/.test(sql)) return { rows: [{ next_step: 'Call synthetic advisor', due_on: null }] };
    if (/deal-items:record/.test(sql)) {
      item = { id: itemId, title: params[3], bullets: JSON.parse(params[5]), source_ref: params[6] };
      return { rows: [{ id: itemId }] };
    }
    if (/deal-items:read/.test(sql)) return { rows: item ? [item] : [] };
    if (/select id,status,item_bullets/.test(sql)) return { rows: [{ id: itemId, status: 'open', bullets: [] }] };
    if (/capture:resolve-post-call-read/.test(sql)) return { rows: [{ id, status: 'pending' }] };
    return { rows: [] };
  } };
}

for (const [name, args, literalHuman] of cases) {
  for (const slug of ['joe', 'dell']) {
    test(`${name} completes its existing operation for human ${slug}`, async () => {
      const result = await executeRegisteredTool(syntheticClient(), partner(slug), name, { idempotency_key: key, ...args });
      assert.equal(result.ok, true);
      if (name === 'approve-retrieval-proposals') assert.deepEqual(result.approved, [id]);
      if (name === 'retire-retrieval-curation') assert.deepEqual(result.retired, { id, status: 'retired' });
      if (name === 'record-deal-item') assert.equal(result.item.title, 'Synthetic task');
      if (name === 'backfill-deal-items') assert.equal(result.items.length, 1);
      if (name === 'instruct-deal-item') assert.equal(result.instruction_recorded, true);
      if (name === 'complete-deal-item') assert.equal(result.status, 'done');
      if (name === 'resolve-post-call-candidate') assert.equal(result.status, 'skipped');
    });
  }
  for (const [slug, sponsor] of [['codex', 'joe'], ['claude', 'dell'], ['joe-local', 'joe'], ['dell-local', 'dell']]) {
    test(`${name} ${literalHuman ? 'refuses' : 'admits'} verified ${slug} sponsored by ${sponsor}`, async () => {
      const actor = sponsored(slug, sponsor);
      if (literalHuman) {
        await assert.rejects(executeRegisteredTool(forbiddenClient, actor, name, { idempotency_key: key, ...args }),
          error => error.payload?.error === 'human_only_verb_requires_verified_partner');
      } else {
        const result = await executeRegisteredTool(syntheticClient(), actor, name, { idempotency_key: key, ...args });
        assert.equal(result.ok, true);
      }
    });
  }
  test(`${name} respects the existing unattended authority allowlist`, async () => {
    const actor = { ...sponsored('joe-local', 'joe'), unattended: true, via: 'unattended-token' };
    await assert.rejects(executeRegisteredTool(forbiddenClient, actor, name, { idempotency_key: key, ...args }),
      error => error.payload?.error === (literalHuman ? 'human_only_verb_requires_verified_partner' : 'unattended_runner_authority_not_admitted'));
  });
}
