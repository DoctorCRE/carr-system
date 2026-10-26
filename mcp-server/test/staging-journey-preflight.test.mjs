// Test staging journey preflight authority refusals.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { callTool } from '../src/mcp.js';
import { tourSharingTools } from '../src/tour-sharing.js';
import { ToolError } from '../src/tool-error.ts';

const actor = { id: randomUUID(), slug: 'joe', human: true, via: 'test' };
const args = () => ({ idempotency_key: randomUUID(), projection_id: randomUUID(),
  token_digest: 'sha256:' + 'a'.repeat(64), permission_scopes: ['invalid-preflight-scope'],
  expires_at: new Date(Date.now() + 60000).toISOString(), receipt_digest: 'sha256:' + 'b'.repeat(64) });

test('staging journey preflight reaches missing authority before opening a writer', async () => {
  await assert.rejects(callTool({ DATABASE_URL_WRITER: 'must-not-open' }, actor, 'issue-tour-share-grant', args()),
    error => error instanceof ToolError && error.payload.error === 'authority_connection_unavailable');
});

test('staging journey preflight cannot enter a write envelope or query a projection', async () => {
  const tools = tourSharingTools({ withEnvelope: () => assert.fail('preflight entered a write envelope'),
    writeEvent: () => assert.fail('preflight wrote an audit event'), ToolError });
  await assert.rejects(tools['issue-tour-share-grant'].handler({ query: () => assert.fail('preflight queried SQL') }, actor, args()),
    error => error instanceof ToolError && error.payload.error === 'tour_share_scope_invalid');
});

test('the preflight refusal keeps its own code through the public projection', async () => {
  const tools = tourSharingTools({ withEnvelope: () => assert.fail('preflight entered a write envelope'),
    writeEvent: () => assert.fail('preflight wrote an audit event'), ToolError });
  const error = await tools['issue-tour-share-grant'].handler({ query: () => assert.fail('preflight queried SQL') }, actor, args())
    .then(() => assert.fail('preflight accepted an invalid scope'), caught => caught);
  const { publicError } = await import('../src/public-errors.ts');
  assert.equal(publicError(error, { env: {} }).error, 'tour_share_scope_invalid');
});
