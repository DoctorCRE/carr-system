import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';

const NOW = Date.parse('2030-01-07T12:00:00Z');
const JOE = { slug: 'joe', human: true, via: 'oauth-google' };
const at = hours => new Date(NOW + hours * 3600000).toISOString();
const meeting = (start, end, fields = {}) => ({ title: 'Unrelated synthetic meeting', start: at(start), end: at(end), ...fields });
const snapshot = (items = [], fields = {}) => ({ schema: 'carr-calendar-agenda.v1', observed_at: at(0), warnings: [], items, ...fields });
function client(calendar = snapshot(), { fail = false, inTransaction = false } = {}) {
  const queries = [];
  return { queries, inTransaction, async query(sql, params = []) {
    queries.push({ sql, params });
    if (/^(savepoint|release savepoint|rollback to savepoint)/.test(sql)) return { rows: [] };
    if (sql.includes('from board_snapshot')) {
      if (fail) throw new Error('Synthetic private diagnostic');
      return { rows: calendar === null ? [] : [{ snapshot_json: calendar }] };
    }
    if (sql.includes('with candidates')) return { rows: [{ total: 0, items: [] }] };
    throw new Error('Unexpected synthetic query');
  } };
}
const read = (c, actor = JOE) => executeRegisteredTool(c, actor, 'read-vendor-meetings', {});
test.beforeEach(t => t.mock.timers.enable({ apis: ['Date'], now: NOW }));

test('vendor scheduling returns maximal candidate openings around every stored meeting', async () => {
  const result = await read(client(snapshot([meeting(2, 3)])));
  assert.deepEqual(result.calendar.scheduling.items, [
    { start: at(0), end: at(2) }, { start: at(3), end: at(24) },
  ]);
  assert.equal(result.calendar.scheduling.state, 'ready');
  assert.match(result.calendar.scheduling.note, /Check Outlook before booking/);
  assert.equal(JSON.stringify(result).includes('Unrelated synthetic meeting'), false);
});

test('nested, touching, and overlapping meetings form one occupied interval', async () => {
  const result = await read(client(snapshot([meeting(2, 3), meeting(2.25, 2.5), meeting(3, 3.5), meeting(3.25, 4)])));
  assert.deepEqual(result.calendar.scheduling.items, [{ start: at(0), end: at(2) }, { start: at(4), end: at(24) }]);
});

test('all-day entries and meetings crossing window bounds conservatively block their stored intervals', async () => {
  const result = await read(client(snapshot([meeting(-1, 2), meeting(3, 25, { all_day: true })])));
  assert.deepEqual(result.calendar.scheduling.items, [{ start: at(2), end: at(3) }]);
});

for (const [name, calendar] of [
  ['missing', null], ['malformed snapshot', {}], ['stale', snapshot([], { observed_at: at(-19) })],
  ['future observation', snapshot([], { observed_at: at(1) })],
  ['unreadable meeting', snapshot([{ title: 'Bad synthetic time', start: 'invalid', end: at(2) }])],
  ['changed meeting time', snapshot([meeting(2, 3, { time_changed: true })])],
  ['conflicting stored copy', snapshot([meeting(2, 3, { conflict: true })])],
  ['truncated agenda', snapshot([], { warnings: [{ code: 'trimmed', count: 1 }] })],
  ['undecoded calendar', snapshot([], { warnings: [{ code: 'undecoded', count: 1 }] })],
  ['unknown warning', snapshot([], { warnings: [{ code: 'synthetic_unknown' }] })],
  ['invalid warning container', snapshot([], { warnings: {} })],
  ['null warning container', snapshot([], { warnings: null })],
  ['invalid warning count', snapshot([], { warnings: [{ code: 'responses_unverified', count: 0 }] })],
  ['zero-duration all-day entry', snapshot([meeting(2, 2, { all_day: true })])],
  ['invalid warning', snapshot([], { warnings: [null] })],
  ['projection cap', snapshot(Array.from({ length: 100 }, () => meeting(40, 41)))],
]) test(`${name} cannot produce apparent scheduling availability`, async () => {
  const result = await read(client(calendar));
  assert.equal(result.calendar.scheduling.state, 'unavailable');
  assert.deepEqual(result.calendar.scheduling.items, []);
});

test('only gaps of at least thirty minutes appear, including exact equality', async () => {
  const result = await read(client(snapshot([meeting(0.5, 23.75)])));
  assert.deepEqual(result.calendar.scheduling.items, [{ start: at(0), end: at(0.5) }]);
  assert.deepEqual(result.calendar.scheduling.window, { start: at(0), end: at(24) });
  assert.equal(result.calendar.scheduling.minimum_duration_minutes, 30);
  assert.match(result.calendar.scheduling.note, /overnight hours/);
});

test('a fresh empty calendar offers the rolling window while a full calendar has no candidates', async () => {
  const empty = await read(client());
  assert.equal(empty.calendar.state, 'empty');
  assert.equal(empty.calendar.scheduling.state, 'ready');
  assert.deepEqual(empty.calendar.scheduling.items, [{ start: at(0), end: at(24) }]);
  const full = await read(client(snapshot([meeting(-1, 25)])));
  assert.equal(full.calendar.scheduling.state, 'empty');
  assert.deepEqual(full.calendar.scheduling.items, []);
});

test('known lifecycle and group gaps retain candidate openings and their uncertainty', async () => {
  const result = await read(client(snapshot([], { warnings: [
    { code: 'responses_unverified' }, { code: 'group_invites_not_shown' }, { code: 'recurring_unverified' },
    { code: 'canceled_hidden', count: 2 },
  ] })));
  assert.equal(result.calendar.state, 'partial');
  assert.equal(result.calendar.scheduling.state, 'partial');
  assert.equal(result.calendar.scheduling.items.length, 1);
  assert.ok(result.calendar.notes.some(note => note.includes('declined')));
  assert.ok(result.calendar.notes.some(note => note.includes('group')));
  const reviewsTrimmed = await read(client(snapshot([], { warnings: [{ code: 'follow_up_trimmed', count: 1 }] })));
  assert.equal(reviewsTrimmed.calendar.scheduling.items.length, 1);
});

test('failed calendar reads retain vendor results, unavailable scheduling, and transaction recovery', async () => {
  const c = client(snapshot(), { fail: true, inTransaction: true });
  const result = await read(c);
  assert.equal(result.calendar.scheduling.state, 'unavailable');
  assert.deepEqual(result.calendar.scheduling.items, []);
  assert.equal(result.total, 0);
  assert.ok(!JSON.stringify(result).includes('Synthetic private diagnostic'));
  assert.ok(c.queries.some(query => query.sql === 'rollback to savepoint vendor_calendar'));
  assert.equal(c.queries.at(-1).sql, 'release savepoint vendor_calendar');
});

test('scheduling uses the authenticated calendar scope and rejects caller-selected context before reads', async () => {
  const c = client();
  await read(c);
  assert.deepEqual(c.queries.find(query => query.sql.includes('from board_snapshot')).params,
    ['carr-internal', 'joe', 'calendar-agenda']);
  await assert.rejects(read(client(), { slug: 'codex', human: false, via: 'agent-token' }));
  const invalid = client();
  await assert.rejects(executeRegisteredTool(invalid, JOE, 'read-vendor-meetings', { scheduling_window: {} }));
  await assert.rejects(executeRegisteredTool(invalid, JOE, 'read-vendor-meetings', { sponsor: 'other' }));
  assert.equal(invalid.queries.length, 0);
});
