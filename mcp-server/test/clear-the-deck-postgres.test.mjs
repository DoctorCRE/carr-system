// Clear the deck (WR577) on the real schema: permanent partner confirmation, keep/drop by either partner, bulk expiry.
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
const pre = {};
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
  await c.query(`insert into actor(slug,kind,display_name) values('dell-local','automation','Dell (local)') on conflict (slug) do nothing`);
  ids = Object.fromEntries((await c.query("select slug, id from actor where slug in ('joe','dell','system','joe-local','dell-local','claude')")).rows
    .map(row => [row.slug, row.id]));
  JOE.id = ids.joe; DELL.id = ids.dell;
  block = (await c.query(`insert into loop_block(rel_path,kind,seq,created_by,updated_by)
    values('fixture/open-loops.md','open_loop',1,$1,$1) returning id`, [ids.system])).rows[0].id;
  // History that predates the migration: the backfill must read it.
  pre.joeEdited = await action({ owner: 'joe', touchedBy: 'joe' });
  pre.joeEditedThenSystem = await action({ owner: 'joe' });
  await c.query(`insert into event(occurred_at,actor_id,verb,subject_type,subject_id,cause)
    values(now() - interval '20 days',$1,'set-next-action','next_action',$2,'human_stated')`, [ids.joe, pre.joeEditedThenSystem]);
  await c.query('update next_action set updated_by=$2 where id=$1', [pre.joeEditedThenSystem, ids.system]);
  pre.eventOnly = await loop({ owner: 'claude' });
  await c.query(`insert into event(occurred_at,actor_id,verb,subject_type,subject_id,cause)
    values(now() - interval '35 days',$1,'update-loop','loop_item',$2,'human_stated')`, [ids['dell-local'], pre.eventOnly]);
  pre.untouched = await action();
  await c.query(readFileSync(new URL('../../migrations/1030_clear_the_deck.sql', import.meta.url), 'utf8'));
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

let seq = 0;
async function action({ by = 'system', owner = 'joe', ageDays = 40, text = 'Enrich: confirm the suite', touchedBy = null } = {}) {
  const row = (await c.query(`insert into next_action(subject_type,subject_id,owner_id,due_on,description,created_by,created_at)
    values('deal',gen_random_uuid(),$1,current_date - 3,$2,$3,now() - make_interval(days => $4)) returning id`,
  [ids[owner], text, ids[by], ageDays])).rows[0];
  if (touchedBy) await c.query('update next_action set updated_by=$2 where id=$1', [row.id, ids[touchedBy]]);
  return row.id;
}
async function loop({ by = 'system', owner = 'claude', ageDays = 40, body = 'hosted CI failed on current head\nNext action: rerun' } = {}) {
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

test('partner history confirms a row permanently; only unconfirmed automation rows reach the deck', async () => {
  assert.equal(await deckRow('next_action', pre.joeEdited), undefined, 'a past partner edit confirms');
  assert.equal(await deckRow('next_action', pre.joeEditedThenSystem), undefined, 'a later automation edit cannot undo it');
  assert.equal(await deckRow('loop', pre.eventOnly), undefined, 'a partner event in history confirms');
  assert.equal((await deckRow('next_action', pre.untouched)).stale, true);
  for (const by of ['joe', 'joe-local', 'dell-local'])
    assert.equal(await deckRow('next_action', await action({ by, ageDays: 400 })), undefined, `${by} rows are partner-made`);
  const touched = await action();
  await c.query('update next_action set updated_by=$2 where id=$1', [touched, ids.dell]);
  await c.query("update next_action set updated_by=$2, description='automation rewrite' where id=$1", [touched, ids.system]);
  const confirmed = (await c.query('select confirmed_by, confirmed_at from next_action where id=$1', [touched])).rows[0];
  assert.equal(confirmed.confirmed_by, ids.dell);
  assert.ok(confirmed.confirmed_at);
  assert.equal(await deckRow('next_action', touched), undefined);
  assert.equal((await deckRow('next_action', await action({ ageDays: 10 }))).stale, false);
  assert.equal((await deckRow('candidate', await candidate({ ageDays: 50 }))).stale, false, 'candidates get 60 days');
  assert.equal((await deckRow('candidate', await candidate())).stale, true);
  const renewal = await candidate();
  await c.query("update candidate_pool set source='renewal-radar' where id=$1", [renewal]);
  assert.equal(await deckRow('candidate', renewal), undefined, 'the renewal snapshot owns renewal-radar membership');
});

test('stale never hides a row from triage; only stored expired does', async () => {
  const stale = await action();
  const triage = async () => new Set((await c.query("select id from v_today_triage where item_kind='next_action'")).rows.map(r => r.id));
  assert.ok((await triage()).has(stale));
  await asWriter(JOE, { idempotency_key: randomUUID(), items: [
    { kind: 'next_action', id: stale, version: await version('next_action', stale), action: 'drop' }] });
  assert.equal((await triage()).has(stale), false);
});

test('either partner sees and decides every automation row, whoever owns it', async () => {
  const joes = await action({ text: 'Deck: Joe stale' });
  const dells = await action({ owner: 'dell', text: 'Deck: Dell stale' });
  const soon = await action({ text: 'Deck: Joe soon', ageDays: 27 });
  for (const partner of [JOE, DELL]) {
    const deck = await read(partner);
    const group = bucket => deck.groups.find(g => g.kind === 'next_action' && g.bucket === bucket).items;
    assert.ok(group('stale').some(r => r.id === joes) && group('stale').some(r => r.id === dells));
    assert.ok(group('expiring_this_week').some(r => r.id === soon));
    assert.deepEqual(group('stale')[0].actions, ['keep', 'drop']);
  }
  await assert.rejects(read({ slug: 'codex', human: false }), refusal('deck_requires_partner_scope'));
  const key = randomUUID();
  const result = await asWriter(DELL, { idempotency_key: key, items: [
    { kind: 'next_action', id: joes, version: await version('next_action', joes), action: 'drop' }] });
  assert.equal(result.counts.next_action.expired, 1);
  const event = (await c.query(`select a.slug, e.new_value from event e join actor a on a.id=e.actor_id
    where e.verb='clear-the-deck-apply' and e.idempotency_key=$1`, [key])).rows[0];
  assert.equal(event.slug, 'dell');
  assert.deepEqual(event.new_value.rows, [{ id: joes, owner: 'joe', status: 'open' }]);
});

test('keep confirms for good and assigns the keeper; drop expires reversibly; replay changes nothing', async () => {
  const keepMe = await action();
  const dropMe = await action();
  const loopId = await loop();
  const key = randomUUID();
  const args = { idempotency_key: key, items: [
    { kind: 'next_action', id: keepMe, version: await version('next_action', keepMe), action: 'keep' },
    { kind: 'next_action', id: dropMe, version: await version('next_action', dropMe), action: 'drop' },
    { kind: 'loop', id: loopId, version: await version('loop_item', loopId), action: 'drop' },
  ] };
  const first = await asWriter(JOE, args);
  assert.deepEqual(first.counts.next_action, { kept: 1, expired: 1, unchanged: 0 });
  assert.deepEqual(first.counts.loop, { kept: 0, expired: 1, unchanged: 0 });
  const kept = (await c.query(`select n.status, a.slug, n.confirmed_by from next_action n join actor a on a.id=n.owner_id
    where n.id=$1`, [keepMe])).rows[0];
  assert.deepEqual(kept, { status: 'open', slug: 'joe', confirmed_by: ids.joe });
  await c.query("update next_action set created_at = now() - interval '400 days' where id=$1", [keepMe]);
  assert.equal(await deckRow('next_action', keepMe), undefined, 'a kept row never returns to the deck');
  const dropped = (await c.query('select status, confirmed_at from next_action where id=$1', [dropMe])).rows[0];
  assert.deepEqual(dropped, { status: 'expired', confirmed_at: null }, 'drop does not confirm');
  const closed = (await c.query('select status, closed_at, close_outcome from loop_item where id=$1', [loopId])).rows[0];
  assert.equal(closed.status, 'expired'); assert.ok(closed.closed_at); assert.match(closed.close_outcome, /^expired/);
  const events = async () => Number((await c.query("select count(*) from event where verb='clear-the-deck-apply' and idempotency_key=$1", [key])).rows[0].count);
  const before = await events();
  assert.equal((await asWriter(JOE, args)).replayed, true);
  assert.equal(await events(), before);
  const restore = await asWriter(DELL, { idempotency_key: randomUUID(), items: [
    { kind: 'loop', id: loopId, version: await version('loop_item', loopId), action: 'keep' }] });
  assert.equal(restore.counts.loop.kept, 1);
  const reopened = (await c.query('select status, closed_at, owner, confirmed_by from loop_item where id=$1', [loopId])).rows[0];
  assert.deepEqual(reopened, { status: 'open', closed_at: null, owner: 'dell', confirmed_by: ids.dell });
});

test('stale versions, partner-made rows and duplicates refuse the whole batch', async () => {
  const a = await action();
  const b = await action();
  await assert.rejects(asWriter(JOE, { idempotency_key: randomUUID(), items: [
    { kind: 'next_action', id: a, version: await version('next_action', a), action: 'drop' },
    { kind: 'next_action', id: b, version: (await version('next_action', b)) + 7, action: 'drop' }] }), refusal('version_conflict'));
  assert.equal((await c.query('select status from next_action where id=$1', [a])).rows[0].status, 'open');
  const partnerMade = await action({ by: 'joe-local' });
  await assert.rejects(asWriter(JOE, { idempotency_key: randomUUID(), items: [
    { kind: 'next_action', id: partnerMade, version: await version('next_action', partnerMade), action: 'drop' }] }),
  refusal('deck_item_not_found'));
  await assert.rejects(asWriter(JOE, { idempotency_key: randomUUID(), items: [
    { kind: 'next_action', id: a, version: 1, action: 'drop' }, { kind: 'next_action', id: a, version: 1, action: 'keep' }] }),
  refusal('deck_item_duplicate'));
});

test('expire_all_matching previews exact counts across owners and expires on apply', async () => {
  await c.query("update loop_item set status='done', close_outcome='fixture reset', closed_at=now() where status='open'");
  const bounds = (await c.query("select now() - interval '60 days' as f, now() + interval '1 minute' as b")).rows[0];
  const window = { created_from: bounds.f.toISOString(), created_before: bounds.b.toISOString() };
  const ciLoops = [await loop(), await loop({ owner: 'dell' }), await loop({ body: 'branch idle for configured limit\nNext action' })];
  await loop({ body: 'Real work an automation filed' });
  await loop({ by: 'joe-local' });
  const filter = { kind: 'loop', created_by: 'system', ...window,
    text_prefixes: ['hosted CI failed on current', 'branch idle', 'current head has merge confl', 'Jev outage remains open'] };
  const preview = await asWriter(JOE, { dry_run: true, expire_all_matching: filter });
  assert.equal(preview.would.matched, 3);
  assert.equal(preview.would.loop.drop, 3);
  assert.equal((await c.query("select count(*)::int as n from loop_item where status='expired' and id=any($1)", [ciLoops])).rows[0].n, 0);
  const { text_prefixes: _p, ...unfiltered } = filter;
  assert.equal((await asWriter(JOE, { dry_run: true, expire_all_matching: unfiltered })).would.matched, 4);
  for (const created_by of ['joe', 'joe-local', 'dell-local'])
    await assert.rejects(asWriter(JOE, { dry_run: true, expire_all_matching: { ...filter, created_by } }),
      refusal('deck_partner_rows_never_expire'));
  await assert.rejects(asWriter(JOE, { dry_run: true, expire_all_matching: { ...filter, created_from: '2026-07-31' } }),
    refusal('deck_filter_range_invalid'));
  await assert.rejects(asWriter(JOE, { dry_run: true, expire_all_matching: { ...filter, kind: 'candidate', owner: 'joe' } }),
    refusal('deck_filter_owner_not_applicable'));
  const key = randomUUID();
  const done = await asWriter(DELL, { idempotency_key: key, expire_all_matching: filter });
  assert.equal(done.counts.loop.expired, 3);
  assert.equal((await c.query("select count(*)::int as n from loop_item where status='expired' and id=any($1)", [ciLoops])).rows[0].n, 3);
  assert.equal((await asWriter(DELL, { idempotency_key: key, expire_all_matching: filter })).replayed, true);
});
