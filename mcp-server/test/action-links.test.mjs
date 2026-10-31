// One-tap action links (WR-000617 slice 5, server half): a link points at one card,
// carries no credential, and resolves only for the card's owner while it is actionable.
import test from 'node:test';
import assert from 'node:assert/strict';
import { allowedIn, connectionRouteForTool } from '../src/mcp.js';
import { TOOLS, assertRegisteredToolInput } from '../src/tools.js';
import { ACTION_LINK_SCHEMA_VERSION, buildActionLink, parseActionLinkRef } from '../src/action-links.js';

const joe = { id: '20000000-0000-4000-8000-000000000001', slug: 'joe' };
const dell = { id: '20000000-0000-4000-8000-000000000002', slug: 'dell' };
const draftId = '20000000-0000-4000-8000-0000000000d1';
const leadId = '20000000-0000-4000-8000-0000000000e1';
const row = (over = {}) => ({
  id: draftId, lead_id: leadId, owner_id: joe.id, revision: 3, approved_at: null,
  subject: 'Synthetic subject', body: 'Synthetic body', scheduled_for: '2026-11-03T08:00:00-06:00',
  time_zone: 'America/Chicago', eligible: true, ...over,
});
const client = rows => ({ calls: [], async query(sql, params) { this.calls.push({ sql, params }); return { rows }; } });
const resolve = (db, actor, ref) => TOOLS['resolve-action-link'].handler(db, actor, { ref });
const refusal = async (db, actor, ref) => {
  try { await resolve(db, actor, ref); } catch (error) { return error.payload; }
  assert.fail('expected a refusal');
};

test('the helper builds a versioned, relative, opaque link for one card', () => {
  const link = buildActionLink({ kind: 'lead_draft', id: draftId });
  assert.equal(link.schema_version, ACTION_LINK_SCHEMA_VERSION);
  assert.equal(link.schema_version, 'action-link.v1');
  assert.match(link.ref, /^al1_[A-Za-z0-9_-]+$/);
  assert.equal(link.path, `/a/${link.ref}`);
  assert.equal(link.url, `https://app.doctorcre.com/a/${link.ref}`);
  assert.deepEqual(buildActionLink({ kind: 'lead_draft', id: draftId }), link, 'stable for the same card');
  assert.notEqual(buildActionLink({ kind: 'lead_draft', id: leadId }).ref, link.ref);
  assert.deepEqual(parseActionLinkRef(link.ref), { kind: 'lead_draft', id: draftId });
});

test('the link string carries no credential material', () => {
  const { path, url, ref } = buildActionLink({ kind: 'lead_draft', id: draftId });
  for (const text of [path, url, ref]) {
    assert.doesNotMatch(text, /[?#=@:]\w*(token|key|secret|session|sig|auth|password|bearer)/i);
    assert.doesNotMatch(text, /[?#&]/, 'no query or fragment');
  }
  assert.equal(new URL(url).search, '');
  assert.equal(new URL(url).username, '');
  assert.throws(() => buildActionLink({ kind: 'lead_draft', id: draftId, token: 'x' }), /action_link_input_invalid/);
  assert.throws(() => buildActionLink({ kind: 'lead_draft', id: 'not-a-uuid' }), /action_link_input_invalid/);
  assert.throws(() => buildActionLink({ kind: 'mystery', id: draftId }), /action_link_input_invalid/);
});

test('malformed references do not parse', () => {
  for (const bad of ['', 'al1_', 'al2_abc', 'al1_!!', 'al1_' + 'A'.repeat(500), null, 7, '/a/al1_x'])
    assert.equal(parseActionLinkRef(bad), null);
});

test('the owner resolves their own pending card to its current state and the verb that acts on it', async () => {
  const { ref } = buildActionLink({ kind: 'lead_draft', id: draftId });
  const db = client([row()]);
  const result = await resolve(db, joe, ref);
  assert.deepEqual(result, {
    schema_version: 'action-link-resolve.v1', ref,
    card: {
      kind: 'lead_draft', id: draftId, lead_id: leadId, state: 'pending_approval', revision: 3,
      subject: 'Synthetic subject', body: 'Synthetic body',
      scheduled_for: '2026-11-03T14:00:00.000Z', time_zone: 'America/Chicago',
      actions: [{ action: 'approve', verb: 'approve-lead-draft', args: { draft_id: draftId, revision: 3 } }],
    },
  });
  assert.equal(db.calls.length, 1);
  assert.deepEqual(db.calls[0].params, [draftId]);
});

test('another partner\'s card refuses exactly like an unknown one, with no data', async () => {
  const { ref } = buildActionLink({ kind: 'lead_draft', id: draftId });
  const other = await refusal(client([row()]), dell, ref);
  const unknown = await refusal(client([]), joe, buildActionLink({ kind: 'lead_draft', id: leadId }).ref);
  const unowned = await refusal(client([row({ owner_id: null })]), joe, ref);
  assert.deepEqual(other, { error: 'action_link_not_found' });
  assert.deepEqual(unknown, other);
  assert.deepEqual(unowned, other);
});

test('a settled card refuses as no longer actionable', async () => {
  const { ref } = buildActionLink({ kind: 'lead_draft', id: draftId });
  assert.deepEqual(await refusal(client([row({ approved_at: '2026-11-01T12:00:00.000Z' })]), joe, ref),
    { error: 'action_link_no_longer_actionable' });
});

test('a card the approval path would refuse (lead moved, suppressed, party changed, merged or restricted) is no longer actionable', async () => {
  const { ref } = buildActionLink({ kind: 'lead_draft', id: draftId });
  assert.deepEqual(await refusal(client([row({ eligible: false })]), joe, ref), { error: 'action_link_no_longer_actionable' });
});

test('eligibility is the approval path\'s own predicate, including the lead\'s current party', async () => {
  const { DRAFT_ELIGIBLE_SQL } = await import('../src/lead-automation.js');
  assert.match(DRAFT_ELIGIBLE_SQL, /d\.party_id=l\.party_id/);
  const { ref } = buildActionLink({ kind: 'lead_draft', id: draftId });
  const db = client([row()]);
  await resolve(db, joe, ref);
  assert.ok(db.calls[0].sql.includes(DRAFT_ELIGIBLE_SQL));
  assert.match(db.calls[0].sql, /join party p on p\.id = l\.party_id/);
});

test('an unknown or malformed reference refuses without touching the database', async () => {
  const db = client([row()]);
  assert.deepEqual(await refusal(db, joe, 'al1_bm9wZQ'), { error: 'action_link_not_found' });
  assert.deepEqual(await refusal(db, joe, 'garbage'), { error: 'action_link_not_found' });
  assert.equal(db.calls.length, 0);
});

test('the resolve verb is a read-only registered operation on the reader route', async () => {
  const tool = TOOLS['resolve-action-link'];
  assert.equal(tool.write, undefined);
  assert.equal(connectionRouteForTool(tool), 'reader');
  assert.equal(allowedIn('full', 'resolve-action-link', tool), true);
  await assert.doesNotReject(() => assertRegisteredToolInput('resolve-action-link', tool, { ref: 'al1_x' }));
  await assert.rejects(() => assertRegisteredToolInput('resolve-action-link', tool, { ref: 'al1_x', owner: 'joe' }), /unregistered_operation_fields/);
});
