import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';

const key = 'ac000000-0000-4000-8000-000000000001';
const partner = slug => ({ id: slug === 'joe' ? 'a1' : 'a2', slug, human: true, via: 'oauth-google' });
function store({ unavailable = null, populated = false } = {}) {
  const seen = new Map(), envelopes = new Map();
  let now = '2026-10-01T12:00:00.000Z';
  const c = slug => ({ query: async (sql, p = []) => {
    if (/savepoint|advisory/.test(sql)) return { rows: [] };
    if (/^\s*select\b/i.test(sql) && /\bfrom\s+tool_call\b/i.test(sql)) return { rows: envelopes.has(p[0]) ? [envelopes.get(p[0])] : [] };
    if (sql.includes('insert into tool_call')) {
      const columns = sql.match(/insert into tool_call\s*\(([^)]+)\)/i)[1].split(',').map(s => s.trim());
      const row = Object.fromEntries(columns.map((name,i) => [name,p[i]]));
      row.response = JSON.parse(row.response);
      envelopes.set(row.idempotency_key,row);
      return { rows: [] };
    }
    if (sql.includes('ops.whats_new_context')) return { rows: [{ result: { ok: true, since: seen.get(slug) || new Date(Date.parse(now)-86400000).toISOString(), high_water: now, snapshot: '100:100:', previous_snapshot: null, first_call: !seen.has(slug) } }] };
    if (sql.includes('ops.whats_new_section')) {
      if (p[0] === unavailable) throw new Error('synthetic private error');
      const items = populated ? [p[0] === 'shipped_releases'
        ? { ref:'release:synthetic-release', at:'2026-10-01T09:00:00Z', text:'System work shipped', group_ref:'system',group_name:'System work' }
        : { ref: `${p[0]}:synthetic`, at: p[0] === 'deal_changes' ? '2026-10-01T11:00:00Z' : '2026-10-01T10:00:00Z', text: 'A synthetic record changed', group_ref: 'deal:synthetic', group_name: 'Synthetic deal' }] : [];
      return { rows: [{ result: { state: items.length ? 'ready' : 'empty', items } }] };
    }
    if (sql.includes('ops.list_shipped_releases')) {
      if (unavailable === 'shipped_releases') throw new Error('synthetic private release error');
      return { rows: populated ? [{ release_key: 'synthetic-release', git_sha: 'synthetic', completed_at: '2026-10-01T09:00:00Z', member_count: 1 }] : [] };
    }
    if (sql.includes('ops.mark_whats_new_seen')) { seen.set(slug, p[0]); return { rows: [{ result: { ok: true } }] }; }
    throw new Error('unexpected synthetic query');
  }});
  return { c, seen, later: () => { now = '2026-10-01T13:00:00.000Z'; } };
}

test('registered dispatch admits catch-up reads and acknowledgement replay', async () => {
  const s = store({ populated: true });
  const actor = partner('joe');
  assert.equal((await executeRegisteredTool(s.c('joe'), actor, 'whats-new', {})).state, 'ready');
  const args = { mark_seen: true, idempotency_key: key };
  const first = await executeRegisteredTool(s.c('joe'), actor, 'whats-new', args);
  assert.equal(first.marked_seen, true);
  assert.deepEqual(await executeRegisteredTool(s.c('joe'), actor, 'whats-new', args), { replayed: true, ...first });
});

test('first read uses 24 hours and never advances either partner watermark', async () => {
  const s = store();
  const r = await TOOLS['whats-new'].handler(s.c('joe'), partner('joe'), {});
  assert.equal(r.since, '2026-09-30T12:00:00.000Z');
  assert.equal(r.first_call, true);
  assert.equal(r.marked_seen, false);
  assert.equal(s.seen.size, 0);
});

test('marking one partner seen never advances the other partner', async () => {
  const s = store();
  await TOOLS['whats-new'].handler(s.c('joe'), partner('joe'), { mark_seen: true, idempotency_key: key });
  s.later();
  const joe = await TOOLS['whats-new'].handler(s.c('joe'), partner('joe'), {});
  const dell = await TOOLS['whats-new'].handler(s.c('dell'), partner('dell'), {});
  assert.equal(joe.since, '2026-10-01T12:00:00.000Z');
  assert.equal(dell.since, '2026-09-30T13:00:00.000Z');
  assert.equal(dell.first_call, true);
});

test('a lost acknowledgement response replays its batch without skipping the next hour', async () => {
  const s = store({ populated: true });
  const args = { mark_seen: true, idempotency_key: key };
  const lost = await TOOLS['whats-new'].handler(s.c('joe'), partner('joe'), args);
  s.later();
  const retried = await TOOLS['whats-new'].handler(s.c('joe'), partner('joe'), args);
  assert.deepEqual(retried, { replayed: true, ...lost });
  assert.equal(s.seen.get('joe'), '2026-10-01T12:00:00.000Z');
  const next = await TOOLS['whats-new'].handler(s.c('joe'), partner('joe'), {});
  assert.equal(next.since, lost.high_water);
});

test('each source distinguishes ready, empty and unavailable; partial answers never advance', async () => {
  const names = ['deal_changes', 'lead_changes', 'next_actions', 'critical_dates', 'partner_activity', 'new_leads', 'doc_suggestions', 'shipped_releases'];
  const empty = await TOOLS['whats-new'].handler(store().c('joe'), partner('joe'), {});
  assert.equal(empty.state, 'empty');
  for (const name of names) assert.equal(empty.sections[name].state, 'empty', name);
  for (const name of names) {
    const s = store({ unavailable: name, populated: true });
    const r = await TOOLS['whats-new'].handler(s.c('joe'), partner('joe'), { mark_seen: true, idempotency_key: key });
    assert.equal(r.sections[name].state, 'unavailable', name);
    assert.equal(r.state, 'unavailable');
    assert.equal(r.marked_seen, false);
    assert.equal(s.seen.size, 0);
    assert.doesNotMatch(JSON.stringify(r), /private error/);
    for (const other of names.filter(n => n !== name)) assert.equal(r.sections[other].state, 'ready', other);
  }
});

test('answers group by deal, newest first, with plain sentences and refs', async () => {
  const r = await TOOLS['whats-new'].handler(store({ populated: true }).c('joe'), partner('joe'), {});
  assert.equal(r.groups.length, 2);
  assert.equal(r.groups[0].items[0].section, 'deal_changes');
  for (const item of r.groups[0].items) assert.equal(item.sentence, `A synthetic record changed (${item.ref}).`);
});

test('unknown suggestion coverage keeps known suggestions visible and prevents acknowledgement', async () => {
  const s = store({ populated: true });
  const c = s.c('joe'), query = c.query;
  c.query = async (sql, p) => {
    const value = await query(sql,p);
    if (sql.includes('ops.whats_new_section') && p[0] === 'doc_suggestions') value.rows[0].result.state = 'unavailable';
    return value;
  };
  const r = await TOOLS['whats-new'].handler(c,partner('joe'), { mark_seen:true,idempotency_key:key });
  assert.equal(r.sections.doc_suggestions.state,'unavailable');
  assert.ok(r.groups[0].items.some(i => i.section === 'doc_suggestions'));
  assert.equal(r.marked_seen,false);
});

test('identity is not selectable and acknowledgement needs a retry key', async () => {
  for (const args of [{ partner: 'dell' }, { since: '2026-01-01' }, { mark_seen: true }, { mark_seen: 'true' }])
    await assert.rejects(TOOLS['whats-new'].handler(store().c('joe'), partner('joe'), args));
  await assert.rejects(TOOLS['whats-new'].handler(store().c('joe'), { slug: 'codex', human: false }, {}));
});

test('malformed source items isolate the whole section and prevent acknowledgement', async () => {
  for (const malformed of [{ text: '   ' }, { at: 'invalid timestamp' }, { ref: null }, { ref: 42 }]) {
    const s = store({ populated: true });
    const c = s.c('joe'), query = c.query;
    c.query = async (sql, p) => {
      const value = await query(sql, p);
      if (sql.includes('ops.whats_new_section') && p[0] === 'next_actions') {
        value.rows[0].result.items.push({ ...value.rows[0].result.items[0], ...malformed });
      }
      return value;
    };
    const r = await TOOLS['whats-new'].handler(c, partner('joe'), { mark_seen: true, idempotency_key: key });
    assert.equal(r.sections.next_actions.state, 'unavailable');
    assert.equal(r.sections.next_actions.items.length, 0);
    assert.equal(r.sections.deal_changes.state, 'ready');
    assert.equal(r.groups.flatMap(g => g.items).some(i => i.section === 'next_actions'), false);
    assert.equal(r.marked_seen, false);
    assert.equal(s.seen.size, 0);
  }
});
