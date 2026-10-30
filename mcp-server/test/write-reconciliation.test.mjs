// Tests retry and concurrent write reconciliation for room and business writes.
import test from 'node:test';
import assert from 'node:assert/strict';
import { roomMessageId } from '../src/dealroom-web.js';
import { appendRoomTurn } from '../src/partner-room.js';
import { TOOLS } from '../src/tools.js';

const key = '11111111-2222-4333-8444-555555555555';
const deal = '10000000-0000-4000-8000-000000000001';
const actor = { id: '20000000-0000-4000-8000-000000000001', slug: 'joe', human: true };

test('room retry identity survives a lost reply across the old minute boundary', async () => {
  const first = await roomMessageId('session-one', actor.slug, key, 59_999);
  assert.equal(await roomMessageId('session-one', actor.slug, key, 120_001), first);
  assert.notEqual(await roomMessageId('session-two', actor.slug, key), first);
  assert.notEqual(await roomMessageId('session-one', 'dell', key), first);
  assert.notEqual(await roomMessageId('session-one', actor.slug, '22222222-2222-4333-8444-555555555555'), first);
});

test('room retries return the original receipt and reject different material under its key', async () => {
  let stored = null, effects = 0;
  const db = { async query(sql, args) {
    if (sql.startsWith('insert into partner_room_turn')) {
      if (stored) return { rows: [] };
      effects++;
      stored = { id: 7, at: '2026-10-23T12:00:00Z', room_id: args[0], sponsor: args[1],
        seat: args[2], kind: args[3], body: args[4], msg_id: args[5], origin_channel: args[6], origin_actor: args[7] };
      return { rows: [stored] };
    }
    return { rows: [stored] };
  } };
  const request = { room: 'partner-line', sponsor: 'joe', seat: 'human', kind: 'turn', body: 'Review the synthetic deal',
    msgId: key, originChannel: 'browser-human', originActor: 'joe' };
  const receipt = await appendRoomTurn(db, request); // The browser loses this reply.
  const replay = await appendRoomTurn(db, request);
  assert.equal(replay.seq, receipt.seq);
  assert.equal(replay.at, receipt.at);
  assert.equal(effects, 1);
  assert.deepEqual(await appendRoomTurn(db, { ...request, body: 'A different command' }), { ok: false, error: 'key_reuse' });
  assert.equal(effects, 1);
});

class RecordStore {
  version = 1;
  effects = 0;
  receipts = new Map();
  async query(sql, args = []) {
    const query = sql.replace(/\s+/g, ' ').trim();
    if (query.startsWith('select pg_advisory')) return { rows: [] };
    if (query.startsWith('select request_hash')) return { rows: this.receipts.has(args[0]) ? [this.receipts.get(args[0])] : [] };
    if (query.startsWith('insert into tool_call')) { this.receipts.set(args[0], { request_hash: args[3], response: JSON.parse(args[4]) }); return { rows: [] }; }
    if (query.includes('from v_ref_index')) return { rows: [{ subject_type: 'deal', subject_id: deal }] };
    if (/^select version from (deal|client) where id=\$1 for update/.test(query)) return { rows: [{ version: this.version }] };
    if (query.startsWith('select created_at')) return { rows: [{ created_at: '2026-01-01' }] };
    if (query.includes('from event e join actor')) return { rows: [{ field: 'next_step', actor: 'dell' }] };
    if (query.startsWith('select account_client_id')) return { rows: [{ account_client_id: deal }] };
    if (query.startsWith('select id from client')) return { rows: [{ id: deal }] };
    if (query.startsWith('select id from actor')) return { rows: [{ id: actor.id }] };
    if (query.startsWith('select next_date')) return { rows: [{ next_date: null }] };
    if (query.startsWith('select agent_name')) return { rows: [{ agent_name: 'Prior synthetic agent', market: 'Synthetic city' }] };
    if (query.includes('dealroom:current-step')) return { rows: [] };
    if (query.startsWith('update deal set') || query.startsWith('update client set')) { this.version++; return { rows: [{ version: this.version }] }; }
    if (query.startsWith('insert into deal_market_assignment') || query.startsWith('insert into national_account_owner') || query.startsWith('insert into deal_note')) this.effects++;
    return { rows: [{ id: key, created_at: '2026-10-23T12:00:00Z' }] };
  }
}

for (const [verb, material] of [
  ['set-next-step', { deal, text: 'Review the synthetic lease', next_date: null }],
  ['set-market-agent', { deal, agent_name: 'Synthetic agent', market: 'Synthetic city', source: 'Partner reviewed' }],
  ['set-national-account-owner', { account_client_id: deal, owner: 'dell' }],
]) {
  test(`${verb}: two partners on one version conflict; a lost-reply retry replays before CAS`, async () => {
    const store = new RecordStore();
    const request = { ...material, base_version: 1, idempotency_key: key };
    const receipt = await TOOLS[verb].handler(store, actor, request);
    assert.equal(store.version, 2);
    assert.equal(store.effects, 1);
    const replay = await TOOLS[verb].handler(store, actor, request);
    assert.equal(replay.replayed, true);
    assert.deepEqual({ ...replay, replayed: undefined }, { ...receipt, replayed: undefined });
    assert.equal(store.effects, 1);
    await assert.rejects(TOOLS[verb].handler(store, { ...actor, slug: 'dell', id: 'dell-id' }, request),
      error => error.payload?.error === 'key_reuse');
    await assert.rejects(TOOLS[verb].handler(store, { ...actor, slug: 'dell', id: 'dell-id' },
      { ...material, base_version: 1, idempotency_key: '00000571-0000-4000-8000-000000000259' }), error => error.payload?.error === 'version_conflict');
    assert.equal(store.effects, 1);
  });
  test(`${verb}: missing expected version refuses before effects`, async () => {
    const store = new RecordStore();
    await assert.rejects(TOOLS[verb].handler(store, actor, { ...material, idempotency_key: key }),
      error => ['missing_base_version', 'missing_required'].includes(error.payload?.error));
    assert.equal(store.effects, 0);
  });
}
