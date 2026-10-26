import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { readDealItems } from '../src/deal-items.js';
import { CHECKLIST_SOURCES } from '../src/deal-checklist.js';

let fixture, admin, database, c, actor, deal;
before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin, connection;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
    assert.equal(url.search, '', 'fixture connection must not override its loopback host');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    const name = `deal_items_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${name}" template template0`);
    database = name;
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'deal-items-', pgCtl: `${bin}/pg_ctl` });
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
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic physician',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'fixture_active',$2,$2) returning id", [party, actor.id])).rows[0].id;
  deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic coastal purchase',$1,'purchase','research','joe',$2,$2) returning id", [client, actor.id])).rows[0].id;
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

test('the record proof uses an isolated database on the provided CI service', async () => {
  const name = (await c.query('select current_database() name')).rows[0].name;
  if (process.env.CARR_CI_DATABASE_URL) {
    assert.match(name, /^deal_items_/);
    assert.notEqual(name, new URL(process.env.CARR_CI_DATABASE_URL).pathname.slice(1));
  } else {
    assert.equal(name, 'carr_ci');
  }
});

async function command(name, args = {}, identity = actor) {
  await c.query('begin');
  try {
    const result = await executeRegisteredTool(c, identity, name, { idempotency_key: randomUUID(), ...(Object.hasOwn(TOOLS[name].inputSchema.properties,'deal') ? { deal } : {}), ...args });
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}
const item = title => ({ title, due_on: '2026-10-23', source_ref: `synthetic:${title}`, bullets: [{ date: '2026-10-20', kind: 'promised', text: 'Requested on the call.' }] });

test('two sourced items persist and replay through the normal write envelope', async () => {
  const key = randomUUID();
  const first = await command('record-deal-item', { ...item('Vendor Introductions'), idempotency_key: key });
  const second = await command('record-deal-item', item('Prelim Market Study'));
  assert.equal(first.rows_changed, 1);
  assert.equal(second.rows_changed, 1);
  const replay = await command('record-deal-item', { ...item('Vendor Introductions'), idempotency_key: key });
  assert.equal(replay.replayed, true);
  assert.equal(replay.item.id, first.item.id);
  const anotherKey = await command('record-deal-item', item('Vendor Introductions'));
  assert.equal(anotherKey.rows_changed, 0);
  assert.equal((await readDealItems(c, deal)).length, 2);
});

test('instruction and completion affect exactly one item and preserve its sibling', async () => {
  const before = await readDealItems(c, deal);
  const target = before.find(i => i.title === 'Vendor Introductions');
  const sibling = before.find(i => i.title === 'Prelim Market Study');
  const key = randomUUID();
  const written = await command('instruct-deal-item', { item_id: target.id, text: 'Please arrange the introductions.', idempotency_key: key });
  assert.equal(written.instruction_recorded, true);
  assert.equal(written.executed, false);
  await command('instruct-deal-item', { item_id: target.id, text: 'Please arrange the introductions.', idempotency_key: key });
  const instructed = (await readDealItems(c, deal)).find(i => i.id === target.id);
  assert.equal(instructed.bullets.filter(b => b.text.includes('Please arrange')).length, 1);
  await command('complete-deal-item', { item_id: target.id });
  const after = await readDealItems(c, deal);
  assert.equal(after.find(i => i.id === target.id).status, 'done');
  assert.deepEqual(after.find(i => i.id === sibling.id), sibling);
});

test('board and detail expose the same stored item contract', async () => {
  const board = await TOOLS['deal-room-board'].handler(c, actor, {});
  const card = board.deals.find(d => d.id === deal);
  assert.equal(card.item_schema_version, 'deal-items.v1');
  const detail = await TOOLS['get-deal-room'].handler(c, actor, { deal });
  assert.equal(detail.item_schema_version, 'deal-items.v1');
  assert.deepEqual(detail.items, card.items);
});

test('Home agenda exposes the same stored checklist and short heading as Deals in one reader snapshot', async () => {
  await c.query('begin');
  try {
    await c.query("update party set name='Dr. Morgan Example (Synthetic Practice)' where id=(select party_id from client where id=(select client_id from deal where id=$1))", [deal]);
    await c.query("update deal set city='Baytown' where id=$1", [deal]);
    const board = await TOOLS['deal-room-board'].handler(c, actor, {});
    const card = board.deals.find(d => d.id === deal);
    const detail = await TOOLS['get-deal-room'].handler(c, actor, { deal });
    await c.query('set local role carr_reader');
    let queries = 0;
    const reader = { query(...args) { queries++; return c.query(...args); } };
    const agenda = await executeRegisteredTool(reader, actor, 'read-home-agenda', {});
    const home = agenda.deals.find(d => d.id === deal);
    assert.equal(queries, 1);
    assert.equal(home.item_schema_version, 'deal-items.v1');
    assert.equal(home.short_name, "Dr. Example's Baytown Deal");
    assert.deepEqual(home.items, card.items);
    assert.deepEqual(home.items, detail.items);
    assert.deepEqual(home.items.map(i => [i.title, i.status]), [['Vendor Introductions', 'done'], ['Prelim Market Study', 'open']]);
    assert.deepEqual(Object.keys(home.items[0]).sort(), ['bullets', 'due_on', 'id', 'source_ref', 'status', 'title']);
  } finally { await c.query('rollback'); }
});

test('ordinary notes and legacy complete-action do not discard or complete checklist siblings', async () => {
  const before = await readDealItems(c, deal);
  await command('add-deal-note', { text: 'A brief update.' });
  await command('set-next-action', { ref: deal, description: 'An independent legacy action.', due_on: '2026-10-24' });
  await command('complete-action', { ref: deal });
  assert.deepEqual(await readDealItems(c, deal), before);
});

test('an open next-step package backfills exactly once without losing dates or source facts', async () => {
  const text = 'Vendor introductions: lender + healthcare attorney intros; Prelim market study: purchase sweep ~5 mi of Synthetic Location Alpha, 8-10 exam rooms, ~$1M all in; walkthrough call before touring Wed/Thu/Fri; ETL sent 11 Sep via CONGA awaiting signature; raise on the package call rather than chasing separately.';
  await c.query("insert into deal_note(deal_id,kind,text,actor_id) values($1,'next_step',$2,$3)", [deal,text,actor.id]);
  await c.query("update deal set next_date='2026-10-26',city='Baytown' where id=$1", [deal]);
  const result = await command('backfill-deal-items');
  const backfilled = result.items.filter(i => i.source_ref.startsWith('next_step:'));
  assert.deepEqual(backfilled.map(i => i.title), ['Vendor Introductions', 'Prelim Market Study', 'ETL not completed']);
  assert.equal(result.rows_changed, 3);
  assert.ok(backfilled.every(i => i.due_on === '2026-10-26'));
  const repeat = await command('backfill-deal-items');
  assert.equal(repeat.rows_changed, 0);
  assert.equal(repeat.items.length, result.items.length);
  assert.equal((await c.query("select text from deal_note where deal_id=$1 and kind='next_step' order by created_at desc limit 1",[deal])).rows[0].text,text);
});

test('short heading uses a recorded doctor contact and market while preserving unknown names', async () => {
  await c.query("update party set name='Dr. Morgan Example (Synthetic Practice)' where id=(select party_id from client where id=(select client_id from deal where id=$1))",[deal]);
  const result = await TOOLS['deal-room-board'].handler(c,actor,{});
  assert.equal(result.deals.find(d => d.id === deal).short_name,"Dr. Example's Baytown Deal");
});

test('a follow-up entered after conversion is visible without replacing existing checklist items', async () => {
  const before = await readDealItems(c,deal);
  const result = await command('record-deal-item',{ ...item('Schedule package review'),due_on:'2026-10-28',source_ref:`app:deal-item:${randomUUID()}` });
  const after = await readDealItems(c,deal);
  assert.equal(after.length,before.length+1);
  assert.ok(after.some(i => i.title === 'Schedule package review' && i.due_on === '2026-10-28'));
  assert.deepEqual(after.filter(i => before.some(b => b.id === i.id)),before);
  assert.equal(result.item.title,'Schedule package review');
});

test('logging outreach cannot silently complete unrelated structured commitments', async () => {
  const before = await readDealItems(c,deal);
  await command('log-outreach',{ ref:deal,channel:'call',outcome:'connected',summary:'Synthetic check-in completed.',next_on:'2026-10-26',next_step:'Independent legacy follow-up.' });
  assert.deepEqual(await readDealItems(c,deal),before);
});

test('unverified automation and cross-deal item writes are refused', async () => {
  const target = (await readDealItems(c,deal)).find(i => i.status === 'open');
  await assert.rejects(command('complete-deal-item',{ item_id:target.id },{ ...actor,human:false }),e => e.payload?.error === 'human_only_verb_requires_verified_partner');
  await assert.rejects(command('complete-deal-item',{ item_id:randomUUID() }),e => e.payload?.error === 'deal_item_not_found');
});

test('accepting an ordinary captured next step creates structured sourced items', async () => {
  const nonce = randomUUID();
  const session = (await c.query(`insert into capture_session(nonce,device_id,actor_id,mode,started_at,consent_announced_at,session_token_hash,expires_at,state_at)
    values($1,'synthetic-device',$2,'meeting',now(),now(),$1,now()+interval '1 hour',now()) returning id`, [nonce,actor.id])).rows[0].id;
  const candidate = (await c.query(`insert into capture_candidate(session_id,idempotency_key,batch_hash,item_index,kind,payload,evidence_quote,confidence)
    values($1,$2,'synthetic',0,'next_step',$3::jsonb,'Please introduce the lender',0.9) returning id`, [session,randomUUID(),JSON.stringify({ deal,text:'Vendor introductions: lender intros',next_date:'2026-10-27' })])).rows[0].id;
  const accepted = await command('resolve-candidate',{ candidate_id:candidate,accept:true });
  assert.equal(accepted.status,'confirmed');
  const items = await readDealItems(c,deal);
  assert.ok(items.some(i => i.source_ref === `capture_candidate:${candidate}` && i.title === 'Vendor Introductions'));
});


test('due checklist pings reach only the owner feed and replay without duplicate delivery', async () => {
  await c.query('begin');
  try {
    const document = (await c.query("insert into doctrine_document(slug,title,content_class) values('synthetic-checklist','Synthetic checklist','reference') returning id")).rows[0].id;
    for (const source of CHECKLIST_SOURCES) {
      await c.query("insert into doctrine_section(id,document_id,section_key,body_hash) values($1::uuid,$2,$1::text,$3)", [source.section_id, document, source.content_hash]);
    }
    await c.query("insert into actor(slug,kind,display_name) values('dell','human','Synthetic other partner') on conflict(slug) do update set active=true");
    const date = (await c.query("insert into critical_date(deal_id,kind,due_on,source,created_by) values($1,'attorney_review','2000-01-01','synthetic:lease',$2) returning id", [deal,actor.id])).rows[0].id;
    await c.query("select set_config('carr.acting_actor_slug','joe',true), set_config('carr.verified_human_actor_slug','joe',true)");
    await c.query('set local role carr_writer');
    const key = randomUUID();
    const ping = key => executeRegisteredTool(c, actor, 'ping-deal-checklist', { deal, idempotency_key: key });
    const first = await ping(key);
    assert.equal(first.queued, 1);
    assert.equal((await ping(key)).replayed, true);
    const retry = await ping(randomUUID());
    assert.equal(retry.queued, 0);
    assert.equal(retry.deduplicated, 1);
    assert.equal(retry.notifications[0].notification_id, first.notifications[0].notification_id);
    const feed = await executeRegisteredTool(c, actor, 'notification-feed', {});
    assert.equal(feed.unread_count, 1);
    assert.deepEqual(feed.notifications.map(n => [n.id,n.subject_type,n.subject_ref,n.severity]),
      [[first.notifications[0].notification_id,'deal',deal,'action_required']]);
    assert.deepEqual(feed.notifications[0].delivery, [{ channel: 'in_app', state: 'pending' }]);
    await c.query('savepoint failed_ping');
    const failedKey = randomUUID();
    const failingStore = { query: (sql, args) => sql.includes('ops.mint_notification')
      ? c.query('select 1/0') : c.query(sql, args) };
    await assert.rejects(executeRegisteredTool(failingStore, actor, 'ping-deal-checklist',
      { deal, idempotency_key: failedKey }));
    await c.query('rollback to savepoint failed_ping');
    const unchanged = await executeRegisteredTool(c, actor, 'notification-feed', {});
    assert.deepEqual(unchanged, feed);
    assert.equal((await c.query('select count(*)::int as count from event where idempotency_key=$1', [failedKey])).rows[0].count, 0);
    assert.equal((await c.query('select count(*)::int as count from tool_call where idempotency_key=$1', [failedKey])).rows[0].count, 0);

    await c.query("select set_config('carr.acting_actor_slug','dell',true), set_config('carr.verified_human_actor_slug','dell',true)");
    assert.equal((await executeRegisteredTool(c, { ...actor, slug: 'dell' }, 'notification-feed', {})).unread_count, 0);
    await c.query('reset role');
    const event = (await c.query('select e.subject_id,e.new_value from event e join ops.notification n on n.event_ref=e.id where n.id=$1', [first.notifications[0].notification_id])).rows[0];
    assert.equal(event.subject_id, deal);
    assert.deepEqual(event.new_value, { checklist_item_id: `critical-date:${date}`, due_on: '2000-01-01', recipient: 'joe' });
    await c.query("insert into client_status(slug,label) values('prospect','Synthetic prospect') on conflict do nothing");
    await c.query("update client set status='prospect',prospect_engagement_status='inactive' where id=(select client_id from deal where id=$1)", [deal]);
    await c.query('set local role carr_writer');
    const inactive = await ping(randomUUID());
    assert.equal(inactive.reason, 'deal_not_active');
    assert.equal(inactive.queued, 0);

  } finally { await c.query('rollback'); }
});
