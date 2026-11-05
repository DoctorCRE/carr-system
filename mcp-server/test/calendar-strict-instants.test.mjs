// Test stored calendar instants and truthful coverage states.
import test from 'node:test';
import assert from 'node:assert/strict';
import { calendarSection } from '../src/brief-calendar.js';

const now = new Date('2026-03-02T11:00:00Z');
const meeting = { title: 'Synthetic review', start: '2026-03-02T12:00:00Z', end: '2026-03-02T13:00:00Z' };
const snapshot = extra => ({ schema: 'carr-calendar-agenda.v1', observed_at: '2026-03-02T10:00:00Z', items: [meeting], ...extra });
const read = extra => calendarSection(snapshot(extra), now);
const invalid = ['2026-02-31T12:00:00Z', '2026-02-29T12:00:00Z', '1900-02-29T12:00:00Z',
  '2026-04-31T12:00:00Z', '2026-13-01T12:00:00Z', '2026-00-01T12:00:00Z',
  '2026-03-00T12:00:00Z', '2026-03-02T24:00:00Z', '2026-03-02T12:60:00Z',
  '2026-03-02T12:00:60Z', '2026-03-02T12:00:00+24:00', '2026-03-02T12:00:00+01:60',
  '2026-03-02T12:00:00', '2026-03-02', 'March 2, 2026 12:00 UTC', '0000-03-02T12:00:00Z', null, 123];

test('February 31 never becomes a March meeting', () => {
  const result = read({ items: [{ ...meeting, start: invalid[0], end: '2026-02-31T13:00:00Z' }] });
  assert.equal(result.state, 'partial');
  assert.deepEqual(result.items, []);
  assert.equal(result.malformed_items, 1);
});
for (const value of invalid) {
  test(`invalid instant ${JSON.stringify(value)} is rejected at every boundary`, () => {
    assert.equal(read({ observed_at: value }).state, 'unavailable');
    for (const field of ['start', 'end']) {
      const result = read({ items: [{ ...meeting, [field]: value }] });
      assert.equal(result.state, 'partial');
      assert.deepEqual(result.items, []);
      const reviews = read({ follow_up_reviews: [{ key: 'a'.repeat(16), state: 'needs_review',
        title: 'Synthetic review', prompt: 'Did this happen?', start: '2026-03-02T08:00:00Z', end: '2026-03-02T09:00:00Z', [field]: value }] });
      assert.equal(reviews.state, 'partial');
      assert.deepEqual(reviews.follow_up_reviews, []);
    }
    const alternate = read({ items: [{ ...meeting, times: [meeting.start, value] }] });
    assert.equal(alternate.state, 'partial');
    assert.deepEqual(alternate.items[0].times, ['2026-03-02T12:00:00.000Z']);
    assert.match(alternate.notes.join(' '), /alternate.*could not be shown/);
  });
}

test('real leap dates, fractions and offsets normalize without rollover', () => {
  for (const year of [2000, 2024]) {
    const start = `${year}-02-29T12:00:00.123456+01:30`;
    const end = `${year}-02-29T12:30:00.987654+0130`;
    const result = calendarSection(snapshot({ observed_at: start, items: [{ ...meeting, start, end, times: [start] }] }), new Date(start));
    assert.equal(result.state, 'ready');
    assert.equal(result.items[0].start, `${year}-02-29T10:30:00.123Z`);
    assert.equal(result.items[0].end, `${year}-02-29T11:00:00.987Z`);
  }
  assert.equal(read({ items: [{ ...meeting, start: '2026-03-02T06:00:00-0600' }] }).items[0].start, '2026-03-02T12:00:00.000Z');
});

test('chart states recover to ready after a valid refresh', () => {
  const cases = [[{ observed_at: invalid[0] }, 'unavailable'], [{}, 'ready'],
    [{ items: [{ ...meeting, times: [invalid[0]] }] }, 'partial'],
    [{ observed_at: '2026-03-01T10:00:00Z' }, 'partial'],
    [{ observed_at: '2026-03-02T12:00:00Z' }, 'partial'], [{ items: [] }, 'empty']];
  assert.equal(calendarSection(null, now), null);
  for (const [extra, state] of cases) {
    assert.equal(read(extra).state, state);
    assert.equal(read({}).state, 'ready');
  }
  assert.equal(read({ observed_at: '2026-03-01T10:00:00Z' }).sources.outlook_calendar.state, 'stale');
});

test('ongoing and all-day meetings, horizon and caps retain their contract', () => {
  const ongoing = { ...meeting, title: 'Ongoing', start: '2026-03-02T10:00:00Z' };
  const allDay = { ...meeting, title: 'All day', start: '2026-03-02T00:00:00Z', end: '2026-03-03T00:00:00Z', all_day: true };
  const result = read({ items: [ongoing, allDay, { ...meeting, start: '2026-03-09T11:00:00Z', end: '2026-03-09T12:00:00Z' }] });
  assert.deepEqual(result.items.map(row => row.title), ['All day', 'Ongoing']);
  assert.ok(result.items.every(row => !row.schedule_conflict));
  const capped = read({ items: Array.from({ length: 101 }, (_, i) => ({ ...meeting, title: `Meeting ${i}` })) });
  assert.equal(capped.items.length, 100);
  assert.equal(capped.state, 'partial');
  assert.equal(capped.items[0].schedule_conflicts.length, 5);
  assert.equal(capped.items[0].schedule_conflicts_omitted, 95);
  assert.equal(read({ items: [{ ...meeting, times: 'unreadable' }] }).state, 'partial');
});

test('valid outcome reviews remain visible only under current observation', () => {
  const review = { key: 'a'.repeat(16), state: 'needs_review', title: 'Synthetic review',
    prompt: 'Did this happen?', start: '2026-03-02T08:00:00Z', end: '2026-03-02T09:00:00Z' };
  assert.equal(read({ follow_up_reviews: [review] }).follow_up_reviews.length, 1);
  for (const observed_at of ['2026-03-01T10:00:00Z', '2026-03-02T12:00:00Z']) {
    const result = read({ observed_at, follow_up_reviews: [review] });
    assert.equal(result.state, 'partial');
    assert.deepEqual(result.follow_up_reviews, []);
  }
});
