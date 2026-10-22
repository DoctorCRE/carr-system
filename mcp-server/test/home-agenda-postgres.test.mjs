import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import pg from 'pg';
import { executeRegisteredTool } from '../src/tools.js';
import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
let bin;
try { bin = execFileSync('pg_config', ['--bindir'], { encoding: 'utf8' }).trim(); } catch {}
if (!bin || !existsSync(path.join(bin, 'postgres'))) {
  bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin', '/usr/lib/postgresql/16/bin'].find(p => existsSync(path.join(p, 'postgres')));
}
const id = n => `aa000000-0000-4000-8000-${String(n).padStart(12, '0')}`;

test('Home agenda real SQL preserves eligibility, nullable dates, holds and reader grants, and refuses every overflow', { skip: !bin && 'PostgreSQL binaries unavailable' }, async t => {
  let fixture, c;
  const releaseBudget = await acquirePostgresFixtureGroup();
  try {
    fixture = await acquireDisposablePostgres({ prefix: 'home-agenda-', pgCtl: path.join(bin, 'pg_ctl'), dataName: '.' });
    const dir = fixture.root;
    await fixture.run(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await fixture.run(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h ''`, '-w', 'start']);
    c = new pg.Client({ host: dir, user: 'fixture', database: 'postgres' });
    await c.connect();
    await c.query(`
      create role carr_reader;
      create table board_source (id uuid primary key, name text, phase text default 'search', owner text default 'joe',
        client_id uuid default 'aa000000-0000-4000-8000-000000000100', type text default 'lease', attention boolean default false, next_step text, next_date date,
        last_touch timestamptz, last_review_at timestamptz, workspace_kind text default 'team', account_client_id uuid,
        operating_state text default 'active', outcome text, closed_on date, invoiced_on date);
      create view v_deal_room_board as select * from board_source;
      create view v_client_engagement_status as select 'aa000000-0000-4000-8000-000000000100'::uuid as client_id, 'prospect'::text as client_status, 'active'::text as prospect_engagement_status;
      create table actor (id uuid primary key, slug text);
      create table next_action (id uuid primary key, subject_type text, subject_id uuid, owner_id uuid,
        description text, due_on date, status text, updated_at timestamptz, hold_until date);
      create table capture_post_call_action (id uuid primary key, deal_id uuid, owner_id uuid,
        description text, due_on date, status text, updated_at timestamptz);
      create table dates_source (id uuid primary key, deal_id uuid, kind text, due_on date, note text, status text);
      create view v_deal_room_critical_date as select * from dates_source;
      create view v_deal_room_action as
        select n.id, n.subject_id as deal_id, a.slug as owner, n.description, n.due_on, n.status, n.updated_at
        from next_action n join actor a on a.id=n.owner_id where n.subject_type='deal';
      grant usage on schema public to carr_reader;
      grant select on v_deal_room_board, v_client_engagement_status, v_deal_room_critical_date, v_deal_room_action to carr_reader;
    `);
    await c.query(readFileSync(new URL('../../migrations/0927_deal_action_hold_date.sql', import.meta.url), 'utf8'));
    await c.query("insert into actor values ($1,'dell')", [id(100)]);
    const read = async () => {
      let queries = 0;
      const client = { query(...args) { queries++; return c.query(...args); } };
      const result = await executeRegisteredTool(client, { slug: 'joe' }, 'read-home-agenda', {});
      assert.equal(queries, 1, 'the registered runtime path uses one SQL snapshot');
      return result;
    };
    await t.test('same active scope includes both owners and national accounts; excludes all parked/closed markers', async t => {
      await c.query('begin');
      t.after(() => c.query('rollback'));
      await c.query(`insert into board_source(id,name) select ('aa000000-0000-4000-8000-' || lpad(n::text,12,'0'))::uuid, 'Synthetic ' || n from generate_series(1,8) n`);
      await c.query("update board_source set owner='dell', workspace_kind='national_account', account_client_id=$1 where id=$2", [id(100), id(2)]);
      for (const [n, set] of [[3, "operating_state='parked'"], [4, "phase='closed'"], [5, "outcome='lost'"], [6, "closed_on='2026-10-20'"], [7, "invoiced_on='2026-10-20'"], [8, "operating_state='parked', phase='closed'"]]) {
        await c.query(`update board_source set ${set} where id=$1`, [id(n)]);
      }
      await c.query("insert into dates_source values ($1,$2,'option',null,null,'settled'), ($3,$2,'lease','2026-10-22','Synthetic date','open')", [id(101), id(2), id(102)]);
      await c.query("insert into next_action values ($1,'deal',$2,$3,'Held synthetic action',null,'open',now(),'2026-10-25'),($4,'deal',$2,$3,'Done synthetic action','2026-10-20','done',now(),null)", [id(103), id(2), id(100), id(104)]);
      await c.query("insert into capture_post_call_action values ($1,$2,$3,'Synthetic captured action','2026-10-23','open',now())", [id(105), id(2), id(100)]);
      await c.query('set role carr_reader');
      const result = await read();
      assert.deepEqual(result.deals.map(d => d.id), [id(1), id(2)]);
      const account = result.deals[1];
      assert.equal(account.owner, 'dell');
      assert.equal(account.client_status, 'prospect');
      assert.equal(account.prospect_engagement_status, 'active');
      assert.equal(account.workspace_kind, 'national_account');
      assert.equal(account.account_client_id, id(100));
      assert.deepEqual(account.critical_dates.map(d => [d.id,d.due_on,d.status]), [[id(102),'2026-10-22','open'],[id(101),null,'settled']]);
      assert.deepEqual(account.next_actions.map(a => [a.id,a.due_on,a.hold_until]), [[id(103),null,'2026-10-25'],[id(105),'2026-10-23',null]]);
      assert.deepEqual(result.deals[0].critical_dates, []);
      assert.deepEqual(result.deals[0].next_actions, []);
      await assert.rejects(c.query('select * from next_action'), e => e.code === '42501');
    });
    for (const kind of ['deals', 'critical_dates', 'open_actions']) await t.test(`${kind} limit is inclusive and overflow refuses rather than truncates`, async t => {
      await c.query('begin');
      t.after(() => c.query('rollback'));
      await c.query("insert into board_source(id,name) values ($1,'Synthetic limit')", [id(1)]);
      if (kind === 'deals') {
        await c.query("insert into board_source(id,name) select ('aa000000-0000-4000-8000-' || lpad(n::text,12,'0'))::uuid, 'Synthetic limit' from generate_series(2,1000) n");
      } else if (kind === 'critical_dates') {
        await c.query("insert into dates_source(id,deal_id,kind,status) select ('bb000000-0000-4000-8000-' || lpad(n::text,12,'0'))::uuid,$1,'option','open' from generate_series(1,10000) n", [id(1)]);
      } else {
        await c.query("insert into next_action(id,subject_type,subject_id,owner_id,description,status) select ('cc000000-0000-4000-8000-' || lpad(n::text,12,'0'))::uuid,'deal',$1,$2,'Synthetic limit','open' from generate_series(1,10000) n", [id(1),id(100)]);
      }
      const result = await read();
      assert.equal(kind === 'deals' ? result.deals.length : result.deals[0][kind === 'open_actions' ? 'next_actions' : kind].length, kind === 'deals' ? 1000 : 10000);
      if (kind === 'deals') await c.query("insert into board_source(id,name) values ($1,'Synthetic overflow')", [id(1001)]);
      else if (kind === 'critical_dates') await c.query("insert into dates_source(id,deal_id,kind,status) values ($1,$2,'option','open')", [id(20001),id(1)]);
      else await c.query("insert into next_action(id,subject_type,subject_id,owner_id,description,status) values ($1,'deal',$2,$3,'Synthetic overflow','open')", [id(20002),id(1),id(100)]);
      await assert.rejects(read, e => e.payload.error === 'agenda_limit_exceeded');
    });
  } finally {
    try { await c?.end(); } finally {
      try { await fixture?.close(); } finally { await releaseBudget(); }
    }
  }
});
