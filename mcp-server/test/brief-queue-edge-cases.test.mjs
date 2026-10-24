import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';
import { readNeedsJoe } from '../src/needs-joe.js';
const joe = { slug: 'joe', human: true, via: 'oauth-google', client_id: 'fixture' };
const client = (loops, items = []) => ({ query: async sql => {
  if (sql.includes('read_governance_queue')) return { rows: [{ queue: {} }] };
  if (sql.includes('from loop_item')) return { rows: loops };
  if (sql.includes('from board_snapshot')) return { rows: [{
    snapshot_json: { items }, updated_at: new Date().toISOString(),
  }] };
  return { rows: [] };
} });
test('long Unicode local identities produce bounded usable continuation cursors', async () => {
  const c = client([], [{ kind: 'tabled', text: '測'.repeat(1000), at: '2026-01-01' },
    { kind: 'tabled', text: '後'.repeat(1000), at: '2026-01-02' }]);
  const first = await executeRegisteredTool(c, joe, 'morning-brief-queue', { queue: 'needs_joe', limit: 1 });
  assert.equal(first.total, 2);
  assert.ok(first.next_cursor.length < 8000);
  const last = await executeRegisteredTool(c, joe, 'morning-brief-queue',
    { queue: 'needs_joe', limit: 1, cursor: first.next_cursor });
  assert.equal(last.items.length, 1);
  assert.notEqual(first.items[0].key, last.items[0].key);
});
test('duplicate decisions retain their oldest record at millisecond precision', async () => {
  const c = client([['9','2026-01-01T00:00:00.001Z'], ['1','2026-01-01T00:00:00.999Z']].map(([number, at]) => ({
    number, created_at: new Date(at), kind: 'open_loop', owner: 'joe',
    marker: 'decision', label: 'Same synthetic decision',
  })));
  const list = await readNeedsJoe(c, joe, {});
  assert.equal(list.items[0].key, 'loop:9');
  assert.deepEqual(list.items[0].records, ['loop:9', 'loop:1']);
});

test('different loop kinds sharing a number remain reachable on tied Needs Joe pages', async () => {
  const c = client(['open_loop', 'team_loop'].map((kind, i) => ({ number: '7', kind,
    created_at: new Date('2026-01-01'), owner: 'joe', marker: 'decision', label: `Synthetic decision ${i}` })));
  const first = await executeRegisteredTool(c, joe, 'morning-brief-queue', { queue: 'needs_joe', limit: 1 });
  const last = await executeRegisteredTool(c, joe, 'morning-brief-queue',
    { queue: 'needs_joe', limit: 1, cursor: first.next_cursor });
  assert.equal(last.items.length, 1);
  assert.notEqual(first.items[0].title, last.items[0].title);
});
