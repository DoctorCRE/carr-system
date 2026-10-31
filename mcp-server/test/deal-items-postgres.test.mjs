// Test persisted deal items, board projections, and item-level writes in PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { applyPendingDealRoomReads } from './helpers/snapshot-schema.mjs';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { readDealItems } from '../src/deal-items.ts';
import { CHECKLIST_SOURCES } from '../src/deal-checklist.ts';

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
  await applyPendingDealRoomReads(c);
  await c.query(readFileSync(new URL('../../migrations/1003_notification_morning_midnight.sql', import.meta.url), 'utf8'));
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

test('expanded deal history retains an update after fifty newer activities', async () => {
  await c.query('begin');
  try {
    await c.query(`insert into activity (deal_id, actor_id, kind, summary, occurred_at, source)
      select $1, $2, 'note',
             case when n=0 then 'Synthetic original requirement' else 'Synthetic follow-up ' || n end,
             '2026-01-01 12:00:00+00'::timestamptz + n * interval '1 minute', 'stated'
        from generate_series(0, 50) n`, [deal, actor.id]);
    const detail = await executeRegisteredTool(c, actor, 'get-deal-room', { deal });
    assert.equal(detail.activities.length, 51);
    assert.equal(detail.activities_truncated, false);
    assert.equal(detail.activities[0].summary, 'Synthetic follow-up 50');
    assert.equal(detail.activities.at(-1).summary, 'Synthetic original requirement');
    assert.equal(new Set(detail.activities.map(activity => activity.id)).size, 51);
  } finally {
    await c.query('rollback');
  }
});

test('deal history caps activities at 500 newest rows', async () => {
  await c.query('begin');
  try {
    await c.query(`insert into activity (deal_id, actor_id, kind, summary, occurred_at, source)
      select $1, $2, 'note', 'Synthetic activity ' || n,
             '2026-01-01 12:00:00+00'::timestamptz + n * interval '1 minute', 'stated'
        from generate_series(0, 500) n`, [deal, actor.id]);
    const detail = await executeRegisteredTool(c, actor, 'get-deal-room', { deal });
    assert.equal(detail.activities.length, 500);
    assert.equal(detail.activities_truncated, true);
    assert.equal(detail.activities[0].summary, 'Synthetic activity 500');
    assert.equal(detail.activities.at(-1).summary, 'Synthetic activity 1');
  } finally {
    await c.query('rollback');
  }
});

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

test('Call Mode and the active agenda share open board eligibility', async () => {
  await c.query('begin');
  try {
    const base = (await c.query('select client_id from deal where id=$1', [deal])).rows[0];
    const excluded = [];
    for (const fields of ["outcome='lost'", "outcome='won'", "phase='closed'", "closed_on='2026-10-20'", "invoiced_on='2026-10-20'"]) {
      const id = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic terminal deal',$1,'purchase','pending','joe',$2,$2) returning id", [base.client_id, actor.id])).rows[0].id;
      await c.query(`update deal set ${fields} where id=$1`, [id]);
      excluded.push(id);
    }
    const parked = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,operating_state,parking_reason,parking_note,parked_at,parked_by,created_by,updated_by) values('Synthetic parked deal',$1,'purchase','pending','joe','parked','other','Synthetic pause',now(),$2,$2,$2) returning id", [base.client_id, actor.id])).rows[0].id;
    const board = await TOOLS['deal-room-board'].handler(c, actor, {});
    for (const id of excluded) {
      assert.ok(!board.deals.some(d => d.id === id), 'terminal deals cannot enter the call agenda');
      assert.ok([...board.closed_deals, ...board.lost_deals].some(d => d.id === id), 'known terminal deals remain in closed history');
      assert.ok((await c.query('select id from v_deal_board where id=$1', [id])).rowCount, 'terminal history remains readable');
    }
    assert.ok((await c.query('select id from v_deal_room_board where id=$1', [excluded[0]])).rowCount, 'uninvoiced terminal history remains readable');
    assert.ok(board.deals.some(d => d.id === parked), 'parked work remains available on its board');
    const agenda = await executeRegisteredTool(c, actor, 'read-home-agenda', {});
    assert.deepEqual(agenda.deals.map(d => d.id), [deal]);
    await c.query('set local role carr_reader');
    const context = await executeRegisteredTool(c, actor, 'get-call-context', { deal_ids: agenda.deals.map(d => d.id) });
    assert.deepEqual(context.deals.map(d => d.id), [deal]);
    for (const id of [...excluded, parked]) {
      await assert.rejects(executeRegisteredTool(c, actor, 'get-call-context', { deal_ids: [deal, id] }), e => e.payload?.error === 'invalid_call_context_deals');
    }
  } finally { await c.query('rollback'); }
});

test('every phase, outcome and closure date combination appears exactly once on the board', async () => {
  await c.query('begin');
  try {
    const { client_id } = (await c.query('select client_id from deal where id=$1', [deal])).rows[0];
    const phases = (await c.query('select slug from deal_phase order by sort,slug')).rows.map(row => row.slug);
    const fixtures = [];
    for (const phase of phases) for (const outcome of [null, 'won', 'lost', 'paused']) {
      for (const closed of [null, '2026-10-20']) for (const invoiced of [null, '2026-10-21']) {
        const { id } = (await c.query(`insert into deal
          (name,client_id,deal_type,phase,outcome,closed_on,invoiced_on,owner,created_by,updated_by)
          values('Synthetic partition deal',$1,'purchase',$2,$3,$4,$5,'joe',$6,$6) returning id`,
        [client_id, phase, outcome, closed, invoiced, actor.id])).rows[0];
        fixtures.push({ id, phase, outcome, closed, invoiced });
      }
    }
    const board = await executeRegisteredTool(c, actor, 'deal-room-board', {});
    const pipeline = await executeRegisteredTool(c, actor, 'deal-board', {});
    for (const fixture of fixtures) {
      const label = JSON.stringify(fixture);
      const lists = [board.deals, board.closed_deals, board.lost_deals];
      assert.equal(lists.flat().filter(row => row.id === fixture.id).length, 1, label);
      const working = !['closed', 'invoiced'].includes(fixture.phase)
        && fixture.outcome === null && fixture.closed === null && fixture.invoiced === null;
      assert.equal(board.deals.some(row => row.id === fixture.id), working, label);
      assert.equal(pipeline.deals.some(row => row.id === fixture.id), working, label);
      assert.equal(board.lost_deals.some(row => row.id === fixture.id), fixture.outcome === 'lost', label);
    }
  } finally { await c.query('rollback'); }
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


test('morning release includes the previous day window at 00:30 Chicago', async () => {
  await c.query('begin');
  try {
    await c.query(`insert into ops.notification_preference(actor, timezone, quiet_hours_start, quiet_hours_end)
      values($1, 'America/Chicago', '04:30', '23:30')`, [actor.id]);
    const definition = (await c.query("select pg_get_functiondef('ops.notification_morning_release_at(uuid)'::regprocedure) as definition")).rows[0].definition;
    await c.query(definition.replaceAll('now()', "'2026-10-27 05:30:00+00'::timestamptz"));
    const result = (await c.query('select ops.notification_morning_release_at($1) as release', [actor.id])).rows[0];
    assert.equal(result.release.toISOString(), '2026-10-27T05:30:00.000Z');
  } finally { await c.query('rollback'); }
});

test('due checklist pings reach only the owner feed and replay without duplicate delivery', async () => {
  await c.query('begin');
  try {
    const definitions = (await c.query(`select pg_get_functiondef(p.oid) as definition
      from pg_proc p join pg_namespace n on n.oid=p.pronamespace
      where n.nspname='ops' and p.proname in
        ('notification_morning_release_at','notification_quiet_now','mint_notification','notification_feed_facts')`)).rows;
    for (const { definition } of definitions)
      await c.query(definition.replaceAll('now()', "'2026-10-27 17:00:00+00'::timestamptz"));
    const document = (await c.query("insert into doctrine_document(slug,title,content_class) values('synthetic-checklist','Synthetic checklist','reference') returning id")).rows[0].id;
    for (const source of CHECKLIST_SOURCES) {
      await c.query("insert into doctrine_section(id,document_id,section_key,body_hash) values($1::uuid,$2,$1::text,$3)", [source.section_id, document, source.content_hash]);
    }
    await c.query("insert into actor(slug,kind,display_name) values('dell','human','Synthetic other partner') on conflict(slug) do update set active=true");
    const date = (await c.query("insert into critical_date(deal_id,kind,due_on,source,created_by) values($1,'attorney_review','2000-01-01','synthetic:lease',$2) returning id", [deal,actor.id])).rows[0].id;
    await c.query("update next_action set due_on='2099-01-01' where subject_type='deal' and subject_id=$1", [deal]);
    const stored = await executeRegisteredTool(c, actor, 'record-deal-item', {
      deal, ...item('Synthetic vendor follow-up'), due_on: '2000-01-01', idempotency_key: randomUUID(),
    });
    const future = await executeRegisteredTool(c, actor, 'record-deal-item', {
      deal, ...item('Future vendor follow-up'), due_on: '2099-01-01', idempotency_key: randomUUID(),
    });
    const nonce = randomUUID();
    const session = (await c.query(`insert into capture_session(nonce,device_id,actor_id,mode,started_at,consent_announced_at,session_token_hash,expires_at,state_at)
      values($1,'synthetic-device',$2,'meeting',now(),now(),$1,now()+interval '1 hour',now()) returning id`, [nonce,actor.id])).rows[0].id;
    const candidate = (await c.query(`insert into capture_post_call_candidate(session_id,idempotency_key,batch_hash,item_index,kind,deal_id,assignee_slug,action_description,due_on,evidence_quote,confidence)
      values($1,$2,'synthetic',0,'assigned_action',$3,'dell','Call the synthetic vendor','2000-01-01','Call the vendor',0.9) returning id`, [session,randomUUID(),deal])).rows[0].id;
    await c.query("select set_config('carr.acting_actor_slug','joe',true), set_config('carr.verified_human_actor_slug','joe',true)");
    await c.query('set local role carr_writer');
    const captured = await executeRegisteredTool(c, actor, 'resolve-post-call-candidate', {
      candidate_id: candidate, accept: true, idempotency_key: randomUUID(),
    });
    await c.query('reset role');
    await c.query(`insert into ops.notification_preference(actor, timezone, quiet_hours_start, quiet_hours_end)
      values($1,'America/Chicago','16:00','11:00')`, [actor.id]);
    await c.query('set local role carr_writer');
    const key = randomUUID();
    const ping = key => executeRegisteredTool(c, actor, 'ping-deal-checklist', { deal, idempotency_key: key });
    const first = await ping(key);
    assert.equal(first.queued, 3);
    assert.deepEqual(first.notifications.map(n => n.checklist_item_id).sort(),
      [`critical-date:${date}`, `deal-item:${stored.item.id}`, `deal-item:${captured.ref}`].sort());
    assert.ok(!first.notifications.some(n => n.checklist_item_id === `deal-item:${future.item.id}`));
    assert.equal((await ping(key)).replayed, true);
    const retry = await ping(randomUUID());
    assert.equal(retry.queued, 0);
    assert.equal(retry.deduplicated, 3);
    assert.equal(retry.notifications[0].notification_id, first.notifications[0].notification_id);
    assert.equal((await c.query("select count(*)::int as count from event where subject_id=$1 and field='checklist_ping'", [deal])).rows[0].count, 3);
    const feed = await executeRegisteredTool(c, actor, 'notification-feed', {});
    assert.equal(feed.unread_count, 3);
    assert.deepEqual(feed.notifications.map(n => [n.id,n.subject_type,n.subject_ref,n.severity]).sort(),
      first.notifications.map(n => [n.notification_id,'deal',deal,'action_required']).sort());
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

    for (const id of [stored.item.id, captured.ref]) {
      await executeRegisteredTool(c, actor, 'complete-deal-item', {
        deal, item_id: id, idempotency_key: randomUUID(),
      });
    }
    const afterCompletion = await ping(randomUUID());
    assert.equal(afterCompletion.queued, 0);
    assert.deepEqual(afterCompletion.notifications.map(n => n.checklist_item_id), [`critical-date:${date}`]);
    assert.equal((await executeRegisteredTool(c, actor, 'notification-feed', {})).unread_count, 3);

    await c.query("select set_config('carr.acting_actor_slug','dell',true), set_config('carr.verified_human_actor_slug','dell',true)");
    assert.equal((await executeRegisteredTool(c, { ...actor, slug: 'dell' }, 'notification-feed', {})).unread_count, 0);
    await c.query('reset role');
    const event = (await c.query('select e.subject_id,e.new_value from event e join ops.notification n on n.event_ref=e.id where n.id=$1', [first.notifications[0].notification_id])).rows[0];
    assert.equal(event.subject_id, deal);
    assert.deepEqual(event.new_value, { checklist_item_id: `critical-date:${date}`, due_on: '2000-01-01', recipient: 'joe' });
    await c.query(`update ops.notification_preference set device_opt_in=true,
      quiet_hours_start='11:00', quiet_hours_end='13:00'
      where actor=$1`, [actor.id]);
    await c.query("update critical_date set due_on='2000-01-02' where id=$1", [date]);
    await c.query("select set_config('carr.acting_actor_slug','joe',true), set_config('carr.verified_human_actor_slug','joe',true)");
    await c.query('set local role carr_writer');
    const held = await ping(randomUUID());
    assert.equal(held.queued, 1);
    assert.equal((await ping(randomUUID())).deduplicated, 1);
    await c.query('reset role');
    const delivery = (await c.query(`select channel,state,held_until > '2026-10-27 17:00:00+00'::timestamptz as future_hold,settled_at
      from ops.notification_delivery where notification_id=$1 order by channel`, [held.notifications[0].notification_id])).rows;
    assert.deepEqual(delivery, ['device','in_app'].map(channel =>
      ({ channel, state: 'held_until_morning', future_hold: true, settled_at: null })));
    assert.equal((await c.query("select count(*)::int as count from event where subject_id=$1 and field='checklist_ping'", [deal])).rows[0].count, 4);
    await c.query("insert into client_status(slug,label) values('prospect','Synthetic prospect') on conflict do nothing");
    await c.query("update client set status='prospect',prospect_engagement_status='inactive' where id=(select client_id from deal where id=$1)", [deal]);
    await c.query('set local role carr_writer');
    const inactive = await ping(randomUUID());
    assert.equal(inactive.reason, 'deal_not_active');
    assert.equal(inactive.queued, 0);

  } finally { await c.query('rollback'); }
});


test('Home discovers pending call follow-ups through existing grants without executing them', async t => {
  const nonce = randomUUID();
  const session = (await c.query(`insert into capture_session(nonce,device_id,actor_id,mode,started_at,
      consent_announced_at,session_token_hash,expires_at,state_at,state,post_call)
    values($1,'synthetic-device',$2,'meeting',now(),now(),$1,now()+interval '1 hour',now(),'distilling',true)
    returning id`, [nonce,actor.id])).rows[0].id;
  const recipient = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic vendor contact',$1,$1) returning id,ref", [actor.id])).rows[0];
  const first = 'ab000000-0000-4000-8000-000000000001';
  const second = 'ab000000-0000-4000-8000-000000000002';
  await c.query(`insert into capture_post_call_candidate(id,session_id,idempotency_key,batch_hash,item_index,kind,deal_id,
      assignee_slug,action_description,due_on,evidence_quote,confidence,created_at)
    values($1,$2,'synthetic-home','synthetic',0,'assigned_action',$3,'dell','Call the synthetic lender',null,
      'Dell will call the lender',0.9,'2026-10-20T12:00:00Z')`, [first,session,deal]);
  await c.query(`insert into capture_post_call_candidate(id,session_id,idempotency_key,batch_hash,item_index,kind,deal_id,
      recipient_party_id,recipient_ref,email_subject,body_sha256,evidence_quote,confidence,created_at)
    values($1,$2,'synthetic-home','synthetic',1,'email_draft',$3,$4,$5,'Synthetic vendor follow-up',$6,
      'Draft a vendor follow-up',0.9,'2026-10-20T12:00:00Z')`, [second,session,deal,recipient.id,recipient.ref,'a'.repeat(64)]);
  const read = async () => {
    await c.query('begin read only');
    try {
      await c.query('set local role carr_writer');
      let queries = 0;
      const result = await executeRegisteredTool({ query(...args) { queries++; return c.query(...args); } }, actor, 'read-post-call-followups', {});
      assert.equal(queries, 1);
      return result;
    } finally { await c.query('rollback'); }
  };
  await t.test('task and email metadata retain exact refs and local-body hash in stable order', async () => {
    const result = await read();
    for (const candidate of result.candidates) candidate.created_at = new Date(candidate.created_at).toISOString();
    assert.deepEqual(result, { schema_version:'post-call-followups.v1', send:false, candidates:[
      { id:first, session_id:session, kind:'assigned_action', deal_id:deal, deal_name:'Synthetic coastal purchase', contact_name:null,
        payload:{deal_id:deal,assignee:'dell',action:'Call the synthetic lender',due_on:null},
        evidence_quote:'Dell will call the lender',created_at:'2026-10-20T12:00:00.000Z' },
      { id:second, session_id:session, kind:'email_draft', deal_id:deal, deal_name:'Synthetic coastal purchase', contact_name:'Synthetic vendor contact',
        payload:{deal_id:deal,recipient_party_id:recipient.id,recipient_ref:recipient.ref,subject:'Synthetic vendor follow-up',body_sha256:'a'.repeat(64)},
        evidence_quote:'Draft a vendor follow-up',created_at:'2026-10-20T12:00:00.000Z' },
    ] });
    assert.equal((await c.query('select count(*)::int as count from capture_post_call_report where session_id=$1',[session])).rows[0].count,0);
  });
  await t.test('live recording, failed, completed, expired and legacy sessions offer no follow-ups', async () => {
    for (const state of ['recording','transcribing','failed','done']) {
      await c.query('update capture_session set state=$2 where id=$1',[session,state]);
      assert.deepEqual((await read()).candidates,[],state);
    }
    await c.query("update capture_session set state='distilling',expires_at=now()-interval '1 second' where id=$1",[session]);
    assert.deepEqual((await read()).candidates,[]);
    await c.query("update capture_session set expires_at=now()+interval '1 hour',post_call=false where id=$1",[session]);
    assert.deepEqual((await read()).candidates,[]);
    await c.query('update capture_session set post_call=true where id=$1',[session]);
    assert.equal((await read()).candidates.length,2);
  });
  await t.test('parked, terminal and inactive-prospect deals offer no follow-ups', async () => {
    for (const patch of ["operating_state='parked',parking_reason='client_paused',parking_note='Synthetic pause',parked_at=now(),parked_by=(select id from actor where slug='joe')",
      "phase='closed'", "outcome='lost'", "closed_on='2026-10-20'", "invoiced_on='2026-10-20'"]) {
      try {
        await c.query(`update deal set ${patch} where id=$1`,[deal]);
        assert.deepEqual((await read()).candidates,[],patch);
      } finally {
        await c.query("update deal set phase='research',outcome=null,closed_on=null,invoiced_on=null,operating_state='active',parking_reason=null,parking_note=null,parked_at=null,parked_by=null where id=$1",[deal]);
      }
    }
    await c.query("insert into client_status(slug,label) values('prospect','Synthetic prospect') on conflict do nothing");
    await c.query("update client set status='prospect',prospect_engagement_status='inactive' where id=(select client_id from deal where id=$1)",[deal]);
    assert.deepEqual((await read()).candidates,[]);
    await c.query("update client set status='fixture_active' where id=(select client_id from deal where id=$1)",[deal]);
  });
  await t.test('human dispositions remove only the reviewed candidate', async () => {
    await command('resolve-post-call-candidate',{candidate_id:first,accept:false});
    assert.deepEqual((await read()).candidates.map(row=>row.id),[second]);
    const accepted = await command('resolve-post-call-candidate',{candidate_id:second,accept:true});
    assert.equal(accepted.local_only,true);
    assert.equal(accepted.send,false);
    assert.deepEqual((await read()).candidates,[]);
  });
  await t.test('one thousand pending candidates fit; one more refuses the whole snapshot', async () => {
    await c.query(`insert into capture_post_call_candidate(session_id,idempotency_key,batch_hash,item_index,kind,deal_id,
        assignee_slug,action_description,evidence_quote,confidence)
      select $1,'synthetic-limit','synthetic',n,'assigned_action',$2,'joe','Synthetic follow-up','Call the vendor',0.5
      from generate_series(1,1000) n`,[session,deal]);
    assert.equal((await read()).candidates.length,1000);
    await c.query(`insert into capture_post_call_candidate(session_id,idempotency_key,batch_hash,item_index,kind,deal_id,
        assignee_slug,action_description,evidence_quote,confidence)
      values($1,'synthetic-limit','synthetic',1001,'assigned_action',$2,'joe','Synthetic overflow','Call the vendor',0.5)`,[session,deal]);
    await assert.rejects(read,e=>e.payload.error==='post_call_followups_limit_exceeded' && e.payload.limit===1000);
  });
});
