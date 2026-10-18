import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS } from '../src/tools.js';

const source = 'uptime-monitor:availability:v1';
const actor = (slug) => ({ id: slug === 'a' ? '10000000-0000-0000-0000-000000000001' : '10000000-0000-0000-0000-000000000002', slug, human: false, via: 'local-token', client_id: slug });
const args = (key, source_note = source) => ({ idempotency_key: key, kind: 'open_loop', domain: 'system', owner: 'claude',
  title: 'DoctorCRE uptime monitoring unavailable', body: 'Restore the monitor', source_note,
  blocker: 'other_lane', blocker_detail: 'Orchestrator must restore the uptime monitor' });

class Records {
  loops = [];
  calls = new Map();
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, ' ').trim();
    if (sql.includes('pg_advisory_xact_lock')) return { rows: [] };
    if (sql.startsWith('select request_hash, response')) return { rows: this.calls.has(params[0]) ? [this.calls.get(params[0])] : [] };
    if (sql.startsWith('select slug from loop_domain')) return { rows: [{ slug: 'system' }] };
    if (sql.includes("source_note=$1")) return { rows: this.loops.filter(row => row.source_note === params[0] && row.status === 'open') };
    if (sql.startsWith('select id, rel_path, col_order')) return { rows: [{ id: 'block', rel_path: 'fixture' }] };
    if (sql.includes('from loop_item where kind = $1')) return { rows: [{ m: this.loops.length }] };
    if (sql.startsWith('select coalesce(max(render_seq)')) return { rows: [{ n: this.loops.length }] };
    if (sql.startsWith('insert into loop_item')) {
      const row = { id: `loop-${this.loops.length + 1}`, number: params[1], source_note: params[9], status: 'open' };
      this.loops.push(row); return { rows: [row] };
    }
    if (sql.startsWith('insert into event')) return { rows: [] };
    if (sql.startsWith('insert into tool_call')) {
      this.calls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) }); return { rows: [] };
    }
    throw new Error(`Unexpected SQL: ${sql}`);
  }
}

test('monitor incident identity survives distinct principals without weakening replay authority', async () => {
  const db = new Records();
  const first = await TOOLS['add-loop'].handler(db, actor('a'), args('a-open'));
  await assert.rejects(() => TOOLS['add-loop'].handler(db, actor('b'), args('a-open')), error => error.message === 'key_reuse');
  const second = await TOOLS['add-loop'].handler(db, actor('b'), args('b-open'));
  assert.equal(second.loop_id, first.loop_id);
  assert.equal(second.deduplicated, true);
  assert.equal(db.loops.length, 1);
  db.loops[0].status = 'dropped';
  const next = await TOOLS['add-loop'].handler(db, actor('b'), args('b-next'));
  assert.notEqual(next.loop_id, first.loop_id);
  assert.equal(db.loops.length, 2);
});

test('ordinary source notes continue to allow separate loops', async () => {
  const db = new Records();
  await TOOLS['add-loop'].handler(db, actor('a'), args('a', 'https://example.com'));
  await TOOLS['add-loop'].handler(db, actor('b'), args('b', 'https://example.com'));
  assert.equal(db.loops.length, 2);
});

test('concurrent monitor writers return one incident under the transaction lock', async () => {
  const db = new Records();
  let writer = Promise.resolve();
  let readers = 0;
  let ready;
  const bothReady = new Promise(resolve => { ready = resolve; });
  async function transaction(principal, key) {
    let release;
    const client = { async query(sql, params) {
      if (sql.startsWith('select request_hash, response')) {
        if (++readers === 2) ready();
        await bothReady;
      }
      if (sql.includes('pg_advisory_xact_lock')) {
        const previous = writer;
        writer = new Promise(resolve => { release = resolve; });
        await previous;
        return { rows: [] };
      }
      return db.query(sql, params);
    } };
    try { return await TOOLS['add-loop'].handler(client, actor(principal), args(key)); }
    finally { release?.(); }
  }
  const [first, second] = await Promise.all([transaction('a', 'concurrent-a'), transaction('b', 'concurrent-b')]);
  assert.equal(first.loop_id, second.loop_id);
  assert.equal(db.loops.length, 1);
});
