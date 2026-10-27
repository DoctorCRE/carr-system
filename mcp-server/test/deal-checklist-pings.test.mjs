import { NOW } from "./helpers/fixed-clock.mjs";
// Test sourced deal checklist reminders, eligibility, and retry behavior.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { TOOLS } from '../src/tools.js';
import { PROFILES } from '../src/mcp.js';
import { CHECKLIST_SOURCES } from '../src/deal-checklist.ts';

const dealId = '10000000-0000-4000-8000-000000000001';
const actor = { id: '20000000-0000-4000-8000-000000000001', slug: 'joe', human: true };

function database(overrides = {}) {
  const deal = { id: dealId, name: 'Synthetic renewal', owner: 'dell', type: 'renewal',
    phase: 'legal', operating_state: 'active', closed_on: null, invoiced_on: null, ...overrides };
  const dates = [
    { id: 'late', kind: 'attorney_review', due_on: '2000-01-01', source: 'fixture:lease', status: 'open' },
    { id: 'future', kind: 'possession', due_on: '2099-01-01', source: 'fixture:lease', status: 'open' },
    { id: 'cleared', kind: 'option', due_on: '2000-01-01', source: 'fixture:lease', status: 'cleared' },
  ];
  const sources = CHECKLIST_SOURCES.map(s => ({ id: s.section_id, status: 'active', body_hash: s.content_hash }));
  const receipts = new Map(), notifications = new Map(), events = [], items = [];
  const client = { deal, dates, sources, events, notifications, receipts, items, async query(sql, values = []) {
    if (sql.includes('pg_advisory_xact_lock')) return { rows: [] };
    if (sql.startsWith('select request_hash, response')) return { rows: receipts.has(values[0]) ? [receipts.get(values[0])] : [] };
    if (sql.startsWith('insert into tool_call')) {
      receipts.set(values[0], { request_hash: values[3], response: JSON.parse(values[4]) });
      return { rows: [] };
    }
    if (sql.includes('from v_ref_index')) return { rows: [{ subject_type: 'deal', subject_id: dealId }] };
    if (sql.includes('from v_deal_room_board where id=$1')) return { rows: deal.prospect_engagement_status === 'inactive' ? [] : [deal] };
    if (sql.includes('from critical_date where deal_id=$1')) return { rows: dates };
    if (sql.includes('checklist:doctrine')) return { rows: sources };
    if (sql.includes('deal-items:read')) return { rows: items };
    if (sql.startsWith('insert into event')) {
      const event = { id: randomUUID(), verb: values[2], subject_type: values[3], subject_id: values[4],
        field: values[5], new_value: JSON.parse(values[7]), idempotency_key: values[10] };
      events.push(event);
      return { rows: [{ id: event.id }] };
    }
    if (sql.includes("verb='ping-deal-checklist'")) return { rows: events
      .filter(e => e.subject_id === values[0] && e.new_value.checklist_item_id === values[1]
        && e.new_value.due_on === values[2] && e.new_value.recipient === values[3])
      .slice(-1).map(e => ({ id: e.id })) };
    if (sql.includes('ops.mint_notification')) {
      if (client.mintFailure) throw new Error('notification store unavailable');
      const [eventSource, eventRef, subjectType, subjectRef, reason, severity, deepLink, key, recipient, bypass, hold] = values;
      assert.equal(eventSource, 'event');
      const source = events.find(e => e.id === eventRef);
      assert.ok(source, 'a notification must refer to a stored source event');
      const prior = notifications.get(`${recipient}:${key}`);
      if (prior) return { rows: [{ result: { ok: true, minted: false, deduplicated: true, notification_id: prior.id } }] };
      const notification = { id: randomUUID(), subjectType, subjectRef, reason, severity, deepLink, key,
        recipient, bypass, hold, source };
      notifications.set(`${recipient}:${key}`, notification);
      return { rows: [{ result: { ok: true, minted: true, notification_id: notification.id } }] };
    }
    if (sql.trimStart().startsWith('select')) return { rows: [] };
    throw new Error(`Unexpected statement ${sql.slice(0, 80)}`);
  } };
  return client;
}

const ping = (c, key = randomUUID()) => TOOLS['ping-deal-checklist'].handler(c, actor,
  { deal: dealId, idempotency_key: key });

test('Doc queues due checklist reminders for the recorded partner without exposing client text', async () => {
  const c = database();
  const result = await ping(c);
  assert.equal(result.ok, true);
  assert.equal(result.queued, 1);
  assert.equal(result.deal_id, dealId);
  assert.equal(result.notifications.length, 1);
  const [n] = c.notifications.values();
  assert.equal(n.recipient, 'dell');
  assert.equal(n.subjectType, 'deal');
  assert.equal(n.subjectRef, dealId);
  assert.equal(n.severity, 'action_required');
  assert.equal(n.deepLink, '/notifications');
  assert.equal(n.bypass, false);
  assert.equal(n.hold, true, 'nightly reminders wait for the recipient morning window');
  assert.doesNotMatch(n.reason, /Synthetic renewal|fixture:lease/);
  assert.deepEqual(n.source.new_value, { checklist_item_id: 'critical-date:late', due_on: '2000-01-01', recipient: 'dell' });
  assert.equal(n.source.subject_id, dealId);
  assert.equal(result.notifications[0].notification_id, n.id);
});

test('retries preserve notification identity and changed owner or due date gets its own reminder', async () => {
  const c = database();
  const key = randomUUID();
  const first = await ping(c, key);
  const replay = await ping(c, key);
  assert.deepEqual(replay, { replayed: true, ...first });
  assert.equal(c.events.length, 1);
  const retry = await ping(c);
  assert.equal(retry.queued, 0);
  assert.equal(retry.deduplicated, 1);
  assert.equal(retry.notifications[0].notification_id, first.notifications[0].notification_id);
  assert.equal(c.notifications.size, 1);
  assert.equal(c.events.length, 1, 'fresh sweep keys reuse the reminder source event');
  c.dates[0].due_on = '2000-01-02';
  assert.equal((await ping(c)).queued, 1);
  c.deal.owner = 'joe';
  assert.equal((await ping(c)).queued, 1);
  assert.equal(c.notifications.size, 3);
  assert.equal(c.events.length, 3, 'only a changed due date or recipient creates another source event');
});

test('today is due in Chicago and cleared, future or unsourced deadlines produce no reminders', async () => {
  const c = database();
  c.dates[0].due_on = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Chicago',
    year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(NOW));
  assert.equal((await ping(c)).queued, 1);
  c.dates[0].status = 'cleared';
  assert.deepEqual((await ping(c)).notifications, []);
  c.dates[0].status = 'open';
  c.dates[0].source = '';
  assert.equal((await ping(c)).queued, 0);
  c.dates[0].source = 'fixture:lease';
  c.dates[0].due_on = '2026-02-30';
  assert.equal((await ping(c)).queued, 0);
  assert.equal(c.notifications.size, 1);
});

test('parked, closed, invoiced and non-partner-owned deals do not interrupt anyone', async () => {
  for (const override of [{ operating_state: 'parked' }, { closed_on: '2026-10-20' },
    { invoiced_on: '2026-10-20' }, { outcome: 'lost' }, { phase: 'closed' }, { phase: 'invoiced' },
    { owner: 'joint' }, { owner: null }, { prospect_engagement_status: 'inactive' }]) {
    const c = database(override);
    const result = await ping(c);
    assert.equal(result.queued, 0);
    assert.ok(result.reason);
    assert.equal(c.notifications.size, 0);
    assert.equal(c.events.length, 0);
  }
});

test('stale doctrine refuses checklist reminders and failed mints do not record a successful receipt', async () => {
  const c = database();
  c.sources[0].body_hash = 'changed';
  await assert.rejects(ping(c), /deal_checklist_unavailable/);
  assert.equal(c.notifications.size, 0);
  assert.equal(c.events.length, 0);
  assert.equal(c.receipts.size, 0);
  const failed = database();
  failed.mintFailure = true;
  await assert.rejects(ping(failed), /notification store unavailable/);
  assert.equal(failed.notifications.size, 0);
  assert.equal(failed.receipts.size, 0);
  failed.mintFailure = false;
  assert.equal((await ping(failed)).queued, 1);
  assert.equal(failed.events.length, 1, 'retry repairs an existing source event without duplicating it');
});

test('the ping contract accepts no caller-provided recipient, date or checklist', () => {
  const tool = TOOLS['ping-deal-checklist'];
  assert.equal(PROFILES.doc.has('ping-deal-checklist'), true);
  assert.equal(tool.write, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(), ['deal', 'idempotency_key']);
});


test('an unknown deal type retains sourced due dates without inventing a document checklist', async () => {
  const c = database({ type: 'unknown' });
  const result = await ping(c);
  assert.equal(result.queued, 1);
  assert.deepEqual(result.notifications.map(n => n.checklist_item_id), ['critical-date:late']);
});


test('stored open checklist deadlines join critical dates without exposing their text', async () => {
  const c = database();
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Chicago',
    year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(NOW));
  const base = { title: 'Synthetic vendor follow-up', source_ref: 'synthetic:private-source', status: 'open' };
  c.items.push(...[
    { id: 'late', due_on: '2000-01-01' },
    { id: 'today', due_on: today },
    { id: 'future', due_on: '2099-01-01' },
    { id: 'undated', due_on: null },
    { id: 'invalid', due_on: '2026-02-30' },
    { id: 'done', due_on: '2000-01-01', status: 'done' },
    { id: 'dropped', due_on: '2000-01-01', status: 'dropped' },
    { id: 'unsourced', due_on: '2000-01-01', source_ref: ' ' },
  ].map(item => ({ ...base, ...item })));
  const result = await ping(c);
  assert.equal(result.queued, 3);
  assert.deepEqual(result.notifications.map(n => n.checklist_item_id),
    ['critical-date:late', 'deal-item:late', 'deal-item:today']);
  const notifications = [...c.notifications.values()];
  assert.ok(notifications.every(n => n.recipient === 'dell' && !n.bypass && n.hold));
  assert.ok(notifications.every(n => !/Synthetic vendor|private-source/.test(n.reason)));
  assert.deepEqual(notifications[1].source.new_value,
    { checklist_item_id: 'deal-item:late', due_on: '2000-01-01', recipient: 'dell' });
});


test('stored item reminders deduplicate across requests and stop after completion', async () => {
  const c = database();
  c.dates.length = 0;
  c.items.push({ id: 'stored', status: 'open', source_ref: 'synthetic:call', due_on: '2000-01-01' });
  const key = randomUUID();
  const first = await ping(c, key);
  assert.equal(first.queued, 1);
  assert.deepEqual(await ping(c, key), { replayed: true, ...first });
  const retry = await ping(c);
  assert.equal(retry.queued, 0);
  assert.equal(retry.deduplicated, 1);
  assert.equal(retry.notifications[0].notification_id, first.notifications[0].notification_id);
  assert.equal(c.events.length, 1, 'two sweeps write one event for the stored item');
  c.items[0].due_on = '2000-01-02';
  assert.equal((await ping(c)).queued, 1);
  c.deal.owner = 'joe';
  assert.equal((await ping(c)).queued, 1);
  c.items[0].status = 'done';
  assert.deepEqual((await ping(c)).notifications, []);
  assert.equal(c.notifications.size, 3);
  assert.equal(c.events.length, 3, 'only a changed due date or recipient creates another source event');
});
