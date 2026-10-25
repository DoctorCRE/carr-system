import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';
import { readNeedsJoe } from '../src/needs-joe.js';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const fixture = new URL('./fixtures/doctorcre-queues-3bcbce4e/', import.meta.url);
const manifest = JSON.parse(readFileSync(new URL('source.json', fixture), 'utf8'));
const consumer = readFileSync(new URL('brief-queues.js.txt', fixture), 'utf8');
assert.equal(manifest.commit, '3bcbce4ee7bba4c1bfe58e28bb2789a8f5d51753');
assert.equal(createHash('sha256').update(consumer).digest('hex'), manifest.files['brief-queues.js.txt']);
const { mountBriefQueues } = await import(`data:text/javascript;base64,${Buffer.from(consumer
  .replace("'./change-receipts.mjs'", JSON.stringify(new URL('./fixtures/doctorcre-brief-a753e121/change-receipts.mjs', import.meta.url).href))
  .replace("import { readWithDeadline } from './auto-refresh.mjs';", 'const readWithDeadline = read => read();'))
  .toString('base64')}`);

async function consumerView(pages) {
  const menu = {}, content = {};
  const view = mountBriefQueues({ menu, content, getSponsor: () => 'joe',
    getClient: async () => ({ morningBriefQueue: async () => JSON.parse(JSON.stringify(pages.shift())) }),
    onOpen() {}, onBack() {}, onInvalidate() { assert.fail('unexpected audience invalidation'); }, onChange() {} });
  menu.onclick({ target: { closest: () => ({ dataset: { briefQueue: 'needs_joe' } }) } });
  await new Promise(resolve => setImmediate(resolve));
  return { view, content, get updated() { return view.updated; } };
}

const joe = { slug: 'joe', human: true, via: 'oauth-google', client_id: 'fixture' };
const client = (loops, items = []) => ({ query: async sql => {
  if (sql.includes('read_governance_queue')) return { rows: [{ queue: {} }] };
  if (sql.includes('from loop_item')) return { rows: loops };
  if (sql.includes('from board_snapshot')) return { rows: [{
    snapshot_json: { items }, updated_at: new Date().toISOString(),
  }] };
  return { rows: [] };
} });
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

test('an unlabeled eligible decision renders through the pinned app queue consumer', async () => {
  const c = client([{ number: '17', kind: 'open_loop', owner: 'joe',
    marker: 'decision', label: null, created_at: new Date('2026-01-01') }]);
  const page = await executeRegisteredTool(c, joe, 'morning-brief-queue', { queue: 'needs_joe' });
  const view = await consumerView([page]);
  assert.equal(view.updated.error, false);
  assert.equal(view.content.innerHTML.match(/data-queue-item=/g)?.length, 1);
  assert.match(view.content.innerHTML, /Decision loop:17/);
  assert.deepEqual(page.items[0].records, ['loop:17']);
});

for (const limit of [50, 1]) test(`same-number same-title decisions from different sources render with page limit ${limit}`, async () => {
  const c = client(['open_loop', 'action_required'].map(kind => ({ number: '17', kind,
    owner: 'joe', marker: 'decision', label: 'Same decision title', created_at: new Date('2026-01-01') })));
  const first = await executeRegisteredTool(c, joe, 'morning-brief-queue', { queue: 'needs_joe', limit });
  assert.equal(first.total, 2);
  const pages = [first];
  if (first.has_more) pages.push(await executeRegisteredTool(c, joe, 'morning-brief-queue',
    { queue: 'needs_joe', limit, cursor: first.next_cursor }));
  const view = await consumerView([...pages]);
  assert.equal(view.updated.error, false);
  if (first.has_more) {
    view.view.click({ target: { closest: selector => selector === '[data-brief-queue-more]' ? {} : null } });
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.equal(view.updated.error, false);
  assert.equal(view.content.innerHTML.match(/data-queue-item=/g)?.length, 2);
  assert.match(view.content.innerHTML, /2 of 2/);
  const items = pages.flatMap(page => page.items);
  assert.equal(new Set(items.map(row => JSON.stringify([row.key, row.title]))).size, 2);
  for (const row of items) assert.deepEqual(row.records, ['loop:17']);
});
