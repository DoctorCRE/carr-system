import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';

const JOE = { slug: 'joe', display: 'Joe', human: true, via: 'oauth-google', client_id: 'fixture' };
const vendor = (email = 'ada@example.test', suffix = '1') => ({
  vendor_id: `10000000-0000-4000-8000-${suffix.padStart(12, '0')}`,
  contact: { id: `20000000-0000-4000-8000-${suffix.padStart(12, '0')}`, name: 'Synthetic contact', email },
  company: { name: 'Synthetic company', email: 'company@example.test' },
});
function snapshot(overrides = {}) {
  const at = hours => new Date(Date.now() + hours * 3600000).toISOString();
  return { schema: 'carr-calendar-agenda.v1', observed_at: at(0), warnings: [], items: [
    { title: 'Synthetic lender meeting', start: at(1), end: at(2), location: 'Synthetic office',
      organizer: { email: ' ADA@example.test ' }, attendees: [] },
  ], ...overrides };
}
function client({ calendar = snapshot(), items = [vendor()], fail = false, inTransaction = false } = {}) {
  const queries = [];
  return { inTransaction, queries, async query(sql, params = []) {
    queries.push({ sql, params });
    if (/^(savepoint|release savepoint|rollback to savepoint) vendor_calendar/.test(sql)) return { rows: [] };
    if (sql.includes('from board_snapshot')) {
      if (fail) throw new Error('Synthetic private SQL diagnostic');
      return { rows: calendar === null ? [] : [{ snapshot_json: calendar }] };
    }
    if (sql.includes('with candidates')) return { rows: [{ total: items.length, items }] };
    throw new Error(`Unexpected query ${sql}`);
  } };
}

test('vendor meeting suggestions show possible upcoming meetings for the exact contact email', async () => {
  const c = client();
  const result = await executeRegisteredTool(c, JOE, 'read-vendor-meetings', {});
  assert.equal(result.items[0].possible_meetings[0].title, 'Synthetic lender meeting');
  assert.equal(result.calendar.state, 'ready');
  assert.deepEqual(c.queries.find(q => q.sql.includes('from board_snapshot')).params,
    ['carr-internal', 'joe', 'calendar-agenda']);
  assert.equal(result.calendar.items, undefined, 'unrelated calendar items are not returned');
});

test('matches attendees without borrowing company, name, domain, or absent contact addresses', async () => {
  const calendar = snapshot();
  calendar.items[0].organizer = { name: 'Synthetic contact', email: 'other@example.test' };
  calendar.items[0].attendees = [{ email: 'ADA@example.test' }, { email: 'company@example.test' }];
  const items = [vendor(' ada@EXAMPLE.test '), vendor('unmatched@example.test', '2'), vendor(null, '3'),
    { ...vendor(undefined, '4'), contact: null }];
  const result = await executeRegisteredTool(client({ calendar, items }), JOE, 'read-vendor-meetings', { view: 'research' });
  assert.deepEqual(result.items.map(item => item.possible_meetings.length), [1, 0, 0, 0]);
});

test('only future or ongoing meetings inside the shared seven-day horizon appear', async () => {
  const calendar = snapshot();
  const meeting = calendar.items[0];
  const at = hours => new Date(Date.now() + hours * 3600000).toISOString();
  calendar.items = [
    { ...meeting, title: 'Synthetic ended meeting', start: at(-2), end: at(-1) },
    { ...meeting, title: 'Synthetic next week', start: at(193), end: at(194) },
    { ...meeting, title: 'Synthetic ongoing', start: at(-1), end: at(1) },
    { ...meeting, title: 'Synthetic upcoming', start: at(2), end: at(3) },
  ];
  const result = await executeRegisteredTool(client({ calendar }), JOE, 'read-vendor-meetings', {});
  assert.deepEqual(result.items[0].possible_meetings.map(item => item.title), ['Synthetic ongoing', 'Synthetic upcoming']);
});

test('absent and malformed snapshots stay unavailable while a fresh empty calendar is empty', async () => {
  for (const calendar of [null, {}, snapshot({ items: [] })]) {
    const c = client({ calendar, inTransaction: true });
    const result = await executeRegisteredTool(c, JOE, 'read-vendor-meetings', {});
    assert.equal(result.calendar.state, calendar?.items ? 'empty' : 'unavailable');
    assert.deepEqual(result.items[0].possible_meetings, []);
    assert.equal(result.total, 1);
    assert.deepEqual(c.queries.filter(q => q.sql.includes('savepoint')).map(q => q.sql),
      ['savepoint vendor_calendar', 'release savepoint vendor_calendar']);
  }
});

test('stale observations and lifecycle gaps remain visible beside possible matches', async () => {
  const calendar = snapshot({ observed_at: new Date(Date.now() - 19 * 3600000).toISOString(),
    warnings: [{ code: 'responses_unverified' }] });
  const result = await executeRegisteredTool(client({ calendar }), JOE, 'read-vendor-meetings', {});
  assert.equal(result.calendar.state, 'partial');
  assert.equal(result.calendar.sources.outlook_calendar.state, 'stale');
  assert.deepEqual(result.calendar.notes, [
    'May include meetings you declined or that were cancelled or deleted. Check Outlook.',
    'This calendar was last read from Outlook more than 18 hours ago.',
  ]);
  assert.equal(result.items[0].possible_meetings.length, 1);
});

test('calendar failure preserves the vendor page and recovers its read transaction without private diagnostics', async () => {
  const c = client({ fail: true, inTransaction: true });
  const result = await executeRegisteredTool(c, JOE, 'read-vendor-meetings', {});
  assert.equal(result.items[0].contact.email, 'ada@example.test');
  assert.deepEqual(result.items[0].possible_meetings, []);
  assert.equal(result.calendar.state, 'unavailable');
  assert.equal(result.calendar.reason, 'source_unavailable');
  assert.equal(result.calendar.scheduling.state, 'unavailable');
  assert.deepEqual(result.calendar.scheduling.items, []);
  assert.ok(!JSON.stringify(result).includes('diagnostic'));
  assert.deepEqual(c.queries.filter(q => q.sql.includes('savepoint')).map(q => q.sql), [
    'savepoint vendor_calendar', 'rollback to savepoint vendor_calendar', 'release savepoint vendor_calendar',
  ]);
});

test('calendar enrichment preserves keyset pagination and cannot accept caller-selected scope', async () => {
  const c = client({ items: [vendor(), vendor('bo@example.test', '2')] });
  const result = await executeRegisteredTool(c, JOE, 'read-vendor-meetings', { limit: 1 });
  assert.equal(result.total, 2);
  assert.equal(result.items.length, 1);
  assert.equal(result.has_more, true);
  assert.deepEqual(JSON.parse(decodeURIComponent(atob(result.next_cursor))).after,
    [result.items[0].vendor_id, result.items[0].contact.id]);
  await assert.rejects(executeRegisteredTool(client(), JOE, 'read-vendor-meetings', { sponsor: 'dell' }));
  const transaction = client({ inTransaction: true });
  await executeRegisteredTool(transaction, JOE, 'read-vendor-meetings', {});
  assert.deepEqual(transaction.queries.filter(q => q.sql.includes('savepoint')).map(q => q.sql),
    ['savepoint vendor_calendar', 'release savepoint vendor_calendar']);
});
