// Clear the deck (WR577) on the real schema: expiry eligibility, keep/drop, bulk expiry and triage hiding.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool, ToolError } from '../src/tools.js';

let fixture, admin, database, c, connection, ids, block;
const JOE = { slug: 'joe', human: true, via: 'oauth-google', client_id: 'fixture' };
const DELL = { slug: 'dell', human: true, via: 'oauth-google', client_id: 'fixture' };

before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
    assert.equal(url.search, '', 'fixture connection must not override its loopback host');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    database = `clear_deck_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${database}" template template0`);
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the Clear the deck proof');
    fixture = await acquireDisposablePostgres({ prefix: 'clear-deck-', pgCtl: `${bin}/pg_ctl` });
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
    connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  }
  c = new pg.Client(connection);
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname];
  if (ciDsn) await promisify(execFile)('psql', [connection.connectionString, ...restore], { maxBuffer: 2 * 1024 * 1024 });
  else await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
  await c.query(readFileSync(new URL('../../migrations/1030_clear_the_deck.sql', import.meta.url), 'utf8'));
  ids = Object.fromEntries((await c.query("select slug, id from actor where slug in ('joe','dell','system','joe-local')")).rows
    .map(row => [row.slug, row.id]));
  JOE.id = ids.joe; DELL.id = ids.dell;
  block = (await c.query(`insert into loop_block(rel_path,kind,seq,created_by,updated_by)
    values('fixture/open-loops.md','open_loop',1,$1,$1) returning id`, [ids.system])).rows[0].id;
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

let seq = 0;
async function action({ by = 'system', owner = 'joe', ageDays = 40, text = 'Enrich: confirm the suite', due = 'current_date - 3', confirmedDaysAgo = null, touchedBy = null } = {}) {
  const row = (await c.query(`insert into next_action(subject_type,subject_id,owner_id,due_on,description,created_by,created_at,confirmed_at)
    values('deal',gen_random_uuid(),$1,${due},$2,$3,now() - make_interval(days => $4),
      case when $5::int is null then null else now() - make_interval(days => $5::int) end) returning id`,
  [ids[owner], text, ids[by], ageDays, confirmedDaysAgo])).rows[0];
  if (touchedBy) await c.query('update next_action set updated_by=$2 where id=$1', [row.id, ids[touchedBy]]);
  return row.id;
}
async function loop({ by = 'joe-local', owner = 'claude', ageDays = 40, body = 'hosted CI failed on current head\nNext action: rerun' } = {}) {
  seq += 1;
  return (await c.query(`insert into loop_item(kind,number,block_id,render_seq,body,owner,tier,created_by,updated_by,created_at)
    values('open_loop',$1,$2,$3,$4,$5,'shared',$6,$6,now() - make_interval(days => $7)) returning id`,
  [`F${seq}`, block, seq, body, owner, ids[by], ageDays])).rows[0].id;
}
async function candidate({ by = 'system', ageDays = 70 } = {}) {
  seq += 1;
  return (await c.query(`insert into candidate_pool(source,source_key,source_row,name,created_by,updated_by,created_at)
    values('lead-router',$1,'{}','Synthetic Practice',$2,$2,now() - make_interval(days => $3)) returning id`,
  [`k${seq}`, ids[by], ageDays])).rows[0].id;
}
const deckRow = async (kind, id) => (await c.query('select * from v_deck_item where kind=$1 and id=$2', [kind, id])).rows[0];
const triageIds = async () => new Set((await c.query("select id from v_today_triage where item_kind='next_action'")).rows.map(r => r.id));
const version = async (table, id) => (await c.query(`select version from ${table} where id=$1`, [id])).rows[0].version;
async function asWriter(actor, args) {
  await c.query('begin');
  try {
    await c.query('set local role carr_writer');
    const result = await executeRegisteredTool(c, actor, 'clear-the-deck-apply', args);
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}
async function read(actor) {
  await c.query('begin');
  try {
    await c.query('set local role carr_reader');
    return await executeRegisteredTool(c, actor, 'clear-the-deck', {});
  } finally { await c.query('rollback'); }
}
const refusal = code => error => error instanceof ToolError && error.payload.error === code;

test('only automation-created rows expire, and a partner touch or keep restarts the clock', async () => {
  const partnerMade = await action({ by: 'joe', ageDays: 400 });
  const stale = await action();
  const fresh = await action({ ageDays: 10 });
  const confirmed = await action({ confirmedDaysAgo: 5 });
  const touched = await action({ touchedBy: 'dell' });
  assert.equal(await deckRow('next_action', partnerMade), undefined);
  assert.equal((await deckRow('next_action', stale)).stale, true);
  assert.equal((await deckRow('next_action', fresh)).stale, false);
  assert.equal((await deckRow('next_action', confirmed)).stale, false);
  assert.equal((await deckRow('next_action', touched)).stale, false);
  const visible = await triageIds();
  assert.ok(visible.has(partnerMade) && visible.has(fresh) && visible.has(confirmed) && visible.has(touched));
  assert.equal(visible.has(stale), false, 'a stale automation row leaves triage');
  assert.equal((await deckRow('candidate', await candidate({ ageDays: 50 }))).stale, false, 'candidates get 60 days');
  assert.equal((await deckRow('candidate', await candidate())).stale, true);
  assert.equal(await deckRow('candidate', await candidate({ by: 'dell' })), undefined);
  const renewal = await candidate();
  await c.query("update candidate_pool set source='renewal-radar' where id=$1", [renewal]);
  assert.equal(await deckRow('candidate', renewal), undefined, 'the renewal snapshot owns renewal-radar membership');
});

test('the deck lists stale and expiring rows for the caller, never the other partner', async () => {
  const mine = await action({ text: 'Deck: Joe stale' });
  const soon = await action({ text: 'Deck: Joe soon', ageDays: 27 });
  const dells = await action({ owner: 'dell', text: 'Deck: Dell stale' });
  const deck = await read(JOE);
  const group = (kind, bucket) => deck.groups.find(g => g.kind === kind && g.bucket === bucket);
  const staleIds = group('next_action', 'stale').items.map(row => row.id);
  assert.ok(staleIds.includes(mine));
  assert.equal(staleIds.includes(dells), false);
  assert.ok(group('next_action', 'expiring_this_week').items.some(row => row.id === soon));
  assert.deepEqual(group('next_action', 'stale').items[0].actions, ['keep', 'drop']);
  assert.equal(typeof group('next_action', 'stale').items[0].version, 'number');
  assert.ok((await read(DELL)).groups.find(g => g.kind === 'next_action' && g.bucket === 'stale').items.some(r => r.id === dells));
  await assert.rejects(read({ slug: 'codex', human: false }), refusal('deck_requires_partner_scope'));
});

test('keep confirms and assigns the partner; drop expires reversibly; replay changes nothing', async () => {
  const keepMe = await action();
  const dropMe = await action();
  const loopId = await loop();
  const key = randomUUID();
  const args = { idempotency_key: key, items: [
    { kind: 'next_action', id: keepMe, version: await version('next_action', keepMe), action: 'keep' },
    { kind: 'next_action', id: dropMe, version: await version('next_action', dropMe), action: 'drop' },
    { kind: 'loop', id: loopId, version: await version('loop_item', loopId), action: 'drop' },
  ] };
  const result = await asWriter(DELL, args).catch(e => e);
  assert.ok(refusal('deck_item_not_yours')(result), 'Dell cannot decide Joe-owned rows');
  const first = await asWriter(JOE, args);
  assert.deepEqual(first.counts.next_action, { kept: 1, expired: 1, unchanged: 0 });
  assert.deepEqual(first.counts.loop, { kept: 0, expired: 1, unchanged: 0 });
  const kept = (await c.query('select n.status, a.slug, n.confirmed_at from next_action n join actor a on a.id=n.owner_id where n.id=$1', [keepMe])).rows[0];
  assert.equal(kept.status, 'open'); assert.equal(kept.slug, 'joe'); assert.ok(kept.confirmed_at);
  assert.equal((await deckRow('next_action', keepMe)).stale, false);
  assert.equal((await c.query('select status from next_action where id=$1', [dropMe])).rows[0].status, 'expired');
  const closed = (await c.query('select status, closed_at, close_outcome from loop_item where id=$1', [loopId])).rows[0];
  assert.equal(closed.status, 'expired'); assert.ok(closed.closed_at); assert.match(closed.close_outcome, /^expired/);
  const events = Number((await c.query("select count(*) from event where verb='clear-the-deck-apply' and idempotency_key=$1", [key])).rows[0].count);
  const replay = await asWriter(JOE, args);
  assert.equal(replay.replayed, true);
  assert.equal(Number((await c.query("select count(*) from event where verb='clear-the-deck-apply' and idempotency_key=$1", [key])).rows[0].count), events);
  const restore = await asWriter(JOE, { idempotency_key: randomUUID(), items: [
    { kind: 'loop', id: loopId, version: await version('loop_item', loopId), action: 'keep' }] });
  assert.equal(restore.counts.loop.kept, 1);
  const reopened = (await c.query('select status, closed_at, owner from loop_item where id=$1', [loopId])).rows[0];
  assert.deepEqual(reopened, { status: 'open', closed_at: null, owner: 'joe' });
});

test('stale versions, partner-created rows and duplicates refuse the whole batch', async () => {
  const a = await action();
  const b = await action();
  const before = await version('next_action', b);
  await assert.rejects(asWriter(JOE, { idempotency_key: randomUUID(), items: [
    { kind: 'next_action', id: a, version: await version('next_action', a), action: 'drop' },
    { kind: 'next_action', id: b, version: before + 7, action: 'drop' }] }), refusal('version_conflict'));
  assert.equal((await c.query('select status from next_action where id=$1', [a])).rows[0].status, 'open');
  const partnerMade = await action({ by: 'joe' });
  await assert.rejects(asWriter(JOE, { idempotency_key: randomUUID(), items: [
    { kind: 'next_action', id: partnerMade, version: 1, action: 'drop' }] }), refusal('deck_item_not_found'));
  await assert.rejects(asWriter(JOE, { idempotency_key: randomUUID(), items: [
    { kind: 'next_action', id: a, version: 1, action: 'drop' }, { kind: 'next_action', id: a, version: 1, action: 'keep' }] }),
  refusal('deck_item_duplicate'));
});

test('expire_all_matching previews exact counts, skips the other partner and expires on apply', async () => {
  await c.query("update loop_item set status='done', close_outcome='fixture reset', closed_at=now() where status='open'");
  const bounds = (await c.query("select now() - interval '60 days' as f, now() + interval '1 minute' as b")).rows[0];
  const window = { created_from: bounds.f.toISOString(), created_before: bounds.b.toISOString() };
  const ciLoops = [await loop(), await loop(), await loop({ body: 'branch idle for configured limit\nNext action' })];
  await loop({ body: 'Real work Joe asked for' });
  const dellLoop = await loop({ owner: 'dell' });
  await loop({ by: 'joe' });
  const filter = { kind: 'loop', created_by: 'joe-local', ...window,
    text_prefixes: ['hosted CI failed on current', 'branch idle', 'current head has merge confl', 'Jev outage remains open'] };
  const preview = await asWriter(JOE, { dry_run: true, expire_all_matching: filter });
  assert.equal(preview.would.loop.drop, 3);
  assert.equal(preview.would.matched, 4);
  assert.equal(preview.would.skipped_other_partner, 1, 'the Dell-owned CI loop is not Joe\'s to expire');
  assert.equal((await c.query("select count(*)::int as n from loop_item where status='expired' and id=any($1)", [ciLoops])).rows[0].n, 0);
  const all = await asWriter(JOE, { dry_run: true, expire_all_matching: (({ text_prefixes, ...rest }) => rest)(filter) });
  assert.equal(all.would.matched, 5);
  assert.equal(all.would.skipped_other_partner, 1, 'the Dell-owned loop is not Joe\'s to expire');
  await assert.rejects(asWriter(JOE, { dry_run: true, expire_all_matching: { ...filter, created_by: 'joe' } }),
    refusal('deck_partner_rows_never_expire'));
  await assert.rejects(asWriter(JOE, { dry_run: true, expire_all_matching: { ...filter, created_from: '2026-07-31' } }),
    refusal('deck_filter_range_invalid'));
  const key = randomUUID();
  const done = await asWriter(JOE, { idempotency_key: key, expire_all_matching: filter });
  assert.equal(done.counts.loop.expired, 3);
  assert.equal((await c.query("select count(*)::int as n from loop_item where status='expired' and id=any($1)", [ciLoops])).rows[0].n, 3);
  assert.equal((await c.query('select status from loop_item where id=$1', [dellLoop])).rows[0].status, 'open');
  assert.equal((await asWriter(JOE, { idempotency_key: key, expire_all_matching: filter })).replayed, true);
});

test('the morning brief loop queue leaves out stale automation loops', async () => {
  await loop({ owner: 'joe', body: 'Stale automation loop for Joe' });
  await loop({ by: 'joe', owner: 'joe', body: 'Joe wrote this loop' });
  await c.query('begin');
  try {
    await c.query('set local role carr_reader');
    const queue = await executeRegisteredTool(c, JOE, 'morning-brief-queue', { queue: 'loops' });
    const labels = queue.items.map(row => row.label);
    assert.ok(labels.includes('Joe wrote this loop'));
    assert.equal(labels.includes('Stale automation loop for Joe'), false);
  } finally { await c.query('rollback'); }
});
