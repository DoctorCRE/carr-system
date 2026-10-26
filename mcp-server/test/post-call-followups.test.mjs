import test from 'node:test';
import assert from 'node:assert/strict';
import { allowedIn, connectionRouteForTool } from '../src/mcp.js';
import { TOOLS, executeRegisteredTool, assertRegisteredToolInput } from '../src/tools.js';

test('pending call follow-ups return an explicit empty read-only snapshot when there is nothing to review', async () => {
  const result = await executeRegisteredTool({ async query() { return { rows: [] }; } }, { slug: 'joe' }, 'read-post-call-followups', {});
  assert.deepEqual(result, { schema_version: 'post-call-followups.v1', candidates: [], send: false });
});


test('call follow-ups reject caller scope and run on the full-profile read-only writer route', async () => {
  const tool = TOOLS['read-post-call-followups'];
  assert.equal(connectionRouteForTool(tool), 'writer_read_only');
  assert.equal(allowedIn('full', 'read-post-call-followups', tool), true);
  await assert.doesNotReject(() => assertRegisteredToolInput('read-post-call-followups', tool, {}));
  await assert.rejects(() => assertRegisteredToolInput('read-post-call-followups', tool, { session_id: 'other' }), /unregistered_operation_fields/);
});
