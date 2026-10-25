import test from 'node:test';
import assert from 'node:assert/strict';
import { assertRegisteredToolInput, TOOLS } from '../src/tools.js';

test('F7 registered workspace admits independent history continuation cursors', async () => {
  await assertRegisteredToolInput('lead-board', TOOLS['lead-board'], {
    workspace: 'leads', lead_id: '30000000-0000-0000-0000-000000000001',
    correspondence_cursor: { occurred_at: '2026-01-01T10:00:00.123456Z', id: '30000000-0000-0000-0000-000000000002' },
    stage_history_cursor: '9007199254740993',
  });
});
