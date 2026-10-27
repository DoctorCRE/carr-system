// Tests brief review queue and continuation regressions.
import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';
import { readNeedsJoe } from '../src/needs-joe.js';

const joe = { slug: 'joe', human: true, via: 'oauth-google', client_id: 'fixture' };
const client = loops => ({ query: async sql => ({ rows: sql.includes('read_governance_queue')
  ? [{ queue: {} }] : sql.includes('from loop_item') ? loops : [] }) });
const loop = (number, created_at = null) => ({ number, created_at, kind: 'open_loop',
  owner: 'joe', marker: 'decision', label: `Decision ${number}` });

test('undated and invalid-date duplicate decisions use the stable record key', async () => {
  for (const at of [null, 'not-a-date']) {
    const c = client(['z', 'a'].map(number => ({ ...loop(number, at), label: 'Same decision' })));
    const result = await readNeedsJoe(c, joe, {});
    assert.equal(result.items[0].key, 'loop:a');
    assert.deepEqual(result.items[0].records, ['loop:a', 'loop:z']);
  }
});
test('queue preserves the actionable record key', async () => {
  const page = await executeRegisteredTool(client([loop('17')]), joe, 'morning-brief-queue', { queue: 'needs_joe' });
  assert.equal(page.items[0].key, 'loop:17');
  assert.equal(page.items[0].records[0], page.items[0].key);
});
test('each Needs Joe page reports its own count', async () => {
  const c = client(Array.from({ length: 51 }, (_, i) => loop(String(i))));
  const first = await executeRegisteredTool(c, joe, 'morning-brief-queue', { queue: 'needs_joe' });
  assert.equal(first.count, first.items.length);
  assert.equal(first.count, 50);
  assert.equal(first.total, 51);
  const last = await executeRegisteredTool(c, joe, 'morning-brief-queue', { queue: 'needs_joe', cursor: first.next_cursor });
  assert.equal(last.count, 1);
  assert.equal(last.items.length, 1);
  assert.equal(last.has_more, false);
});

for (const number of ['a'.repeat(257), '測'.repeat(10000)])
  test(`loop continuation preserves long identities of length ${number.length}`, async () => {
    const numbers = [number, `${number}b`, 'z'];
    const c = client(numbers.map(number => ({ number, kind: 'open_loop', owner: 'joe', due_on: '2026-01-01' })));
    const found = [];
    let cursor;
    for (let i = 0; i < numbers.length; i++) {
      const page = await executeRegisteredTool(c, joe, 'morning-brief-queue',
        { queue: 'loops', limit: 1, ...(cursor ? { cursor } : {}) });
      assert.equal(page.total, numbers.length);
      assert.equal(page.returned, 1);
      assert.equal(page.remaining, numbers.length - i - 1);
      assert.equal(page.has_more, i < numbers.length - 1);
      found.push(page.items[0].number);
      cursor = page.next_cursor;
      if (page.has_more) assert.ok(cursor.length <= 8000);
    }
    assert.equal(cursor, null);
    assert.deepEqual(found.slice().sort(), numbers.slice().sort());
  });

test('loop urgency paging traverses 62 rows and fences cursor scope', async () => {
  const c = client(Array.from({ length: 62 }, (_, i) => ({ number: String(i), kind: 'open_loop',
    owner: i % 2 ? 'dell' : 'joe', due_on: i === 61 ? '2026-01-01' : null })));
  const first = await executeRegisteredTool(c, joe, 'morning-brief-queue', { queue: 'loops' });
  assert.equal(first.total, 62);
  assert.equal(first.returned, 50);
  assert.equal(first.items[0].number, '61');
  const last = await executeRegisteredTool(c, joe, 'morning-brief-queue', { queue: 'loops', cursor: first.next_cursor });
  assert.equal(last.returned, 12);
  assert.equal(last.next_cursor, null);
  assert.equal(new Set([...first.items, ...last.items].map(row => row.number)).size, 62);
  for (const [actor, args] of [[{ ...joe, slug: 'dell' }, { queue: 'loops', cursor: first.next_cursor }],
    [joe, { queue: 'needs_joe', cursor: first.next_cursor }], [joe, { queue: 'loops', limit: 0 }],
    [joe, { queue: 'loops', sponsor: 'dell' }]])
    await assert.rejects(executeRegisteredTool(c, actor, 'morning-brief-queue', args));
});
