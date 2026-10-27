// Test Local Deals PostgreSQL operations, evidence and regressions.
import { readMigration } from './migration-source.testhelper.mjs';
import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';
import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { CHECKLIST_SOURCES } from '../src/deal-checklist.ts';
import { readCommandCenterSummary } from '../src/workspace-command-center.js';

const root = fileURLToPath(new URL('../../', import.meta.url));
const schema = readFileSync(path.join(root, 'db/schema.sql'), 'utf8');
let bin;
for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
  try {
    const candidate = execFileSync(config, ['--bindir'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    if (existsSync(path.join(candidate, 'postgres'))) { bin = candidate; break; }
  } catch { /* Try the other supported PostgreSQL installations. */ }
}
const id = n => `aa000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const actor = { id: id(1), slug: 'joe', human: true, via: 'dealroom-cookie', client_id: 'dealroom-pwa' };

test('Local Deals PostgreSQL caller and evidence regressions', { skip: !bin && !process.env.CARR_CI_DATABASE_URL && 'PostgreSQL unavailable' }, async t => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let postgresFixture, dir;
  let admin;
  let database;
  let c;
  // A provided CI cluster is owned and budgeted by its caller.
  const releaseBudget = ciDsn ? async () => {} : await acquirePostgresFixtureGroup();
  try {
    let connection;
    if (ciDsn) {
      const url = new URL(ciDsn);
      assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
      assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
      admin = new pg.Client({ connectionString: ciDsn });
      await admin.connect();
      const name = `local_deals_${randomUUID().replaceAll('-', '')}`;
      await admin.query(`create database "${name}" template template0`);
      database = name;
      url.pathname = `/${database}`;
      connection = { connectionString: url.href };
    } else {
      postgresFixture = await acquireDisposablePostgres({ prefix: 'local-deals-', pgCtl: path.join(bin, 'pg_ctl'), dataName: '.' });
      dir = postgresFixture.root;
      await postgresFixture.run(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale']);
      await postgresFixture.run(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h ''`, '-w', 'start']);
      connection = { host: dir, user: 'fixture', database: 'postgres' };
    }
    c = new pg.Client({ ...connection,
      // Preserve PostgreSQL microseconds, as the production HTTP driver does.
      types: { getTypeParser: (oid, format) => oid === 1184 ? value => value : pg.types.getTypeParser(oid, format) },
    });
    await c.connect();
    if (process.env.CARR_CI_DATABASE_URL) {
      const isolated = (await c.query('select current_database() name')).rows[0].name;
      assert.match(isolated, /^local_deals_/);
      assert.notEqual(isolated, new URL(process.env.CARR_CI_DATABASE_URL).pathname.slice(1));
      assert.equal((await c.query("select to_regclass('public.deal') existing")).rows[0].existing, null);
    }
    // Roles belong to the cluster; an isolated database does not provide them.
    // Preserve existing shared-cluster roles and their attributes.
    await c.query(`do $$ begin
      begin create role carr_reader; exception when duplicate_object then null; end;
      begin create role carr_writer; exception when duplicate_object then null; end;
    end $$;`);
    // Use the committed table definitions and caller views, without production data.
    for (const name of ['actor', 'party', 'vendor', 'client', 'deal', 'deal_phase', 'deal_participant', 'next_action', 'deal_note', 'national_account_owner', 'deal_market_assignment', 'deal_review_item', 'deal_review_session', 'event', 'tool_call', 'deal_conflict', 'critical_date', 'lease', 'activity', 'premises', 'negotiation_round', 'document', 'commission', 'capture_post_call_action', 'capture_post_call_candidate', 'building', 'space', 'premises_space', 'lead', 'lead_stage', 'lead_lane', 'lead_stage_move', 'record_flag']) {
      const table = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))?.[0];
      assert.ok(table, name);
      await c.query(table);
      if (name === "party") await c.query("alter table party drop column if exists vendor_assignment_version");
    }
    await c.query('alter table activity add column if not exists email_source jsonb');
    for (const table of ['actor','party','deal']) await c.query(`alter table ${table} add primary key(id)`);
    await c.query(readMigration('0951_deal_vendor_assignments.sql').split('-- Current catalog reseal')[0]);
    const touch = schema.match(/CREATE FUNCTION public\.trg_touch_row\(\) RETURNS trigger[\s\S]*?end \$\$;/)?.[0];
    assert.ok(touch);
    await c.query(touch);
    await c.query('create trigger vendor_touch before update on vendor for each row execute function trg_touch_row()');
    await restoreEventIdentity(c, schema);
    await c.query('create trigger client_touch before update on client for each row execute function trg_touch_row()');
    await c.query('create trigger lead_touch before update on lead for each row execute function trg_touch_row()');
    await c.query('create sequence ref_lead_seq start 900000');
    await c.query('create sequence ref_client_seq start 900000');
    await c.query('alter table tool_call add primary key(idempotency_key);');
    await c.query("create view v_last_touch as select null::text subject_type, null::uuid subject_id, null::date last_touch where false;");
    for (const name of ['v_client_account', 'v_deal_board', 'v_deal_room_board', 'v_open_deal_room_board', 'v_deal_room_account', 'v_deal_room_event', 'v_deal_room_session', 'v_deal_reconciliation_read', 'v_deal_room_note', 'v_deal_room_critical_date', 'v_deal_room_action', 'v_deal_room_activity', 'v_deal_room_participant', 'v_deal_room_premises', 'v_deal_room_negotiation', 'v_deal_room_document', 'v_deal_room_phase_change', 'v_deal_room_current_lease', 'v_invoice_tracker', 'v_client_engagement_status', 'v_lead_board', 'v_lead_board_stage', 'v_lead_client_link', 'v_lead_stage_transition', 'v_lead_prospect_engagement', 'v_lead_workspace_lifecycle', 'v_lead_workspace', 'v_lead_workspace_detail', 'v_lead_workspace_correspondence', 'v_lead_workspace_stage_history']) {
      const view = schema.match(new RegExp(`CREATE VIEW public\\.${name} AS[\\s\\S]*?;`))?.[0];
      assert.ok(view, name);
      await c.query(view);
      await c.query(`grant select on ${name} to carr_reader,carr_writer`);
    }
    await c.query("create table doctrine_section(id uuid primary key, status text, body_hash text)");
    await c.query("grant select on doctrine_section to carr_reader,carr_writer");
    await c.query(readFileSync(path.join(root, 'migrations/0916_deal_phase_start_dates.sql'), 'utf8'));
    await c.query(readMigration('1004_phase_start_undo.sql'));
    await c.query(readFileSync(path.join(root, 'migrations/0923_deal_status_next_step.sql'), 'utf8'));
    await c.query("create view v_ref_index as select 'deal'::text subject_type, id subject_id from deal union all select 'vendor',id from vendor union all select 'lead', id from lead;");
    await c.query("grant select on v_ref_index to carr_reader");
    await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'joe','Synthetic Partner','human',true)", [actor.id]);
    for (const [slug, sort] of [['research', 1], ['negotiation', 2], ['legal', 3], ['closed', 4]]) {
      await c.query('insert into deal_phase(slug,label,sort) values($1,$1,$2)', [slug, sort]);
    }
    for (const [slug, sort] of [['nurture_drip',50],['opportunity',60],['archived',100],['do_not_contact',110]])
      await c.query('insert into lead_stage(slug,label,sort) values($1,$1,$2)', [slug,sort]);
    const fixture = async national => {
      await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Practice',$2,$2)", [id(2), actor.id]);
      await c.query('insert into client(id,party_id,created_by,updated_by) values($1,$2,$3,$3)', [id(3), id(2), actor.id]);
      if (national) {
        await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Account',$2,$2)", [id(5), actor.id]);
        await c.query('update party set org_id=$1 where id=$2', [id(5), id(2)]);
        await c.query("insert into client(id,party_id,client_type,created_by,updated_by) values($1,$2,'national_account',$3,$3)", [id(6), id(5), actor.id]);
      }
      await c.query("insert into deal(id,client_id,name,deal_type,phase,owner,attention,next_date,created_by,updated_by) values($1,$2,'Synthetic Assignment','renewal','research','joe',true,current_date-1,$3,$3)", [id(4), id(3), actor.id]);
    };
    const transaction = async fn => {
      await c.query('begin');
      try { await fn(); } finally { await c.query('rollback'); }
    };

    await t.test('deal detail survives missing checklist storage and recovers after migration 0922', async t => {
      const warning = t.mock.method(console, 'warn', () => {});
      const read = async () => {
        await c.query('set role carr_reader');
        try { return await TOOLS['get-deal-room'].handler(c, actor, {deal:id(4)}); }
        finally { await c.query('reset role'); }
      };
      await fixture(false);
      for (const source of CHECKLIST_SOURCES) await c.query("insert into doctrine_section(id,status,body_hash) values($1,'active',$2)", [source.section_id, source.content_hash]);
      try {
        const before = await read();
        assert.equal(before.deal_id, id(4));
        assert.equal(before.name, 'Synthetic Assignment');
        assert.equal(before.schema_version, 'deal-timeline.v1');
        assert.deepEqual(before.checklist, {
          schema_version: 'deal-checklist.v1', template_state: 'unavailable', phases: [], due_items: [],
        });
        assert.deepEqual(await read(), before, 'repeated reads preserve the same detail');
        assert.equal(warning.mock.callCount(), 1, 'missing storage logs once across reads');
        assert.match(warning.mock.calls[0].arguments.join(' '), /checklist.*unavailable/i);
        await c.query(readFileSync(path.join(root, 'migrations/0922_deal_checklist_commissions.sql'), 'utf8'));
        const after = await read();
        assert.equal(after.checklist.template_state, 'current');
        assert.equal(after.checklist.phases.length, 6);
        const {checklist: beforeChecklist, ...beforeDetail} = before;
        const {checklist: afterChecklist, ...afterDetail} = after;
        assert.deepEqual(beforeDetail, afterDetail, 'every existing detail field survives the missing migration');
        await c.query('revoke select on v_deal_room_commission from carr_reader');
        await assert.rejects(read(), error => error.code === '42501', 'other database errors still propagate');
        assert.equal(warning.mock.callCount(), 1, 'other errors are not logged as unavailable storage');
      } finally {
        await c.query(readFileSync(path.join(root, 'migrations/0922_deal_checklist_commissions.sql'), 'utf8').replace('create view', 'create or replace view'));
        await c.query('delete from deal where id=$1', [id(4)]);
        await c.query('delete from client where id=$1', [id(3)]);
        await c.query('delete from party where id=$1', [id(2)]);
        await c.query('delete from doctrine_section');
      }
    });
    const accept = async (deal, owner, due) => {
      const candidate = randomUUID();
      await c.query(`insert into capture_post_call_candidate
        (id,session_id,idempotency_key,batch_hash,item_index,kind,deal_id,assignee_slug,action_description,due_on,evidence_quote,confidence)
        values ($1,$2,$3,$4,0,'assigned_action',$5,$6,'Review call terms',$7,'Review call terms',1)`,
        [candidate, randomUUID(), randomUUID(), 'a'.repeat(64), deal, owner, due]);
      const ref = (await TOOLS['resolve-post-call-candidate'].handler(c, actor, {
        idempotency_key: randomUUID(), candidate_id: candidate, accept: true,
      })).ref;
      // These singleton regressions model actions accepted before structured
      // items existed. New structured captures have their own isolation proof.
      await c.query("update capture_post_call_action set item_title=null,item_bullets='[]'::jsonb,source_ref=null where id=$1",[ref]);
      return ref;
    };

    await t.test('deal detail preserves held actions and exposes hold dates for both action sources', async () => transaction(async () => {
      await fixture(false);
      const task = await TOOLS['set-next-action'].handler(c, actor, {
        idempotency_key: randomUUID(), ref: id(4), description: 'Review synthetic terms', due_on: '2026-01-01',
      });
      await c.query("update next_action set hold_until='2027-01-01' where id=$1", [task.next_action_id]);
      const postCall = await accept(id(4), 'joe', '2026-01-02');
      await c.query('set role carr_reader');
      try {
        const room = await TOOLS['get-deal-room'].handler(c, actor, { deal: id(4) });
        const held = room.next_actions.find(row => row.id === task.next_action_id);
        assert.equal(held.hold_until, '2027-01-01');
        assert.equal(held.status, 'open', 'a hold must preserve the recorded lifecycle state');
        assert.equal(room.next_actions.find(row => row.id === postCall).hold_until, null);
      } finally { await c.query('reset role'); }
    }));

    await t.test('partner status update supersedes an overdue step and flags the next-step gap', async () => transaction(async () => {
      await fixture(true);
      const yesterday = (await c.query("select (current_date-1)::text as day")).rows[0].day;
      const prior = await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Request revised terms', next_date: yesterday,
      });
      const update = await TOOLS['add-deal-note'].handler(c, actor, {
        idempotency_key: randomUUID(), deal: id(4), text: 'Revised terms received for review',
      });
      const board = await TOOLS['deal-room-board'].handler(c, actor, {workspace: 'all'});
      assert.equal(board.deals[0].next_date, null, 'the superseded date must stop driving overdue');
      assert.equal(board.deals[0].next_step, null, 'the prior step belongs only in history');
      assert.equal(board.deals[0].next_step_required, true);
      assert.equal(board.deals[0].attention, true, 'manual attention survives');
      assert.equal(Number(board.accounts[0].overdue_deals), 0);
      assert.equal(update.next_step_required, true);
      assert.match(update.prompt, /next step.*date/i);
      const followup = await TOOLS['add-deal-note'].handler(c, actor, {
        idempotency_key: randomUUID(), deal: id(4), text: 'Further context with no new plan yet',
      });
      assert.equal(followup.next_step_required, true, 'the plan gap stays visible on later updates');
      assert.match(followup.prompt, /next step.*date/i);

      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'dropped');
      assert.ok(room.thread.some(n => n.id === prior.next_step_id && n.text === 'Request revised terms'));
      assert.ok(room.thread.some(n => n.id === update.note_id));
      assert.ok(room.events.some(e => e.verb === 'add-deal-note' && e.field === 'next_date' &&
        e.old_value.next_date === yesterday && e.new_value.next_date === null));
    }));

    await t.test('set-next-action records an undated action without an unchanged empty date event', async () => transaction(async () => {
      await fixture(false);
      await c.query('update deal set next_date=null where id=$1', [id(4)]);
      const action = await executeRegisteredTool(c, actor, 'set-next-action', {
        idempotency_key: randomUUID(), ref: id(4), description: 'Review synthetic terms',
      });
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_date, null);
      assert.equal(room.next_actions.find(a => a.id === action.next_action_id).status, 'open');
      assert.equal(room.events.filter(e => e.verb === 'set-next-action' && e.field === 'next_date').length, 0);
      assert.equal(room.events.filter(e => e.verb === 'set-next-action' && e.new_value?.next_action === 'Review synthetic terms').length, 1);
    }));

    await t.test('set-next-action records only changed dates and clears a replaced owner action date', async () => transaction(async () => {
      await fixture(false);
      await c.query('update deal set next_date=null where id=$1', [id(4)]);
      const dates = (await c.query("select (current_date+7)::text as first, (current_date+14)::text as second")).rows[0];
      const steps = [
        { due: dates.first, old: null, count: 1 },
        { due: dates.first, old: null, count: 1 },
        { due: dates.second, old: dates.first, count: 2 },
        { due: null, old: dates.second, count: 3 },
      ];
      let previous;
      for (const [index, step] of steps.entries()) {
        const action = await executeRegisteredTool(c, actor, 'set-next-action', {
          idempotency_key: randomUUID(), ref: id(4), description: `Review synthetic terms ${index}`,
          ...(step.due ? { due_on: step.due } : {}),
        });
        const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
        assert.equal(room.next_date, step.due);
        const events = room.events.filter(e => e.verb === 'set-next-action' && e.field === 'next_date');
        assert.equal(events.length, step.count);
        assert.ok(events.some(e => e.old_value.next_date === step.old && e.new_value.next_date === step.due));
        const current = room.next_actions.find(a => a.id === action.next_action_id);
        assert.equal(current.status, 'open');
        assert.equal(current.due_on, step.due);
        if (previous) assert.equal(room.next_actions.find(a => a.id === previous).status, 'dropped');
        previous = action.next_action_id;
      }
    }));

    await t.test('set-next-action keeps the current step and its date together', async () => transaction(async () => {
      await fixture(false);
      const dates = (await c.query("select (current_date-1)::text as past, (current_date+7)::text as future")).rows[0];
      await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Request revised terms', next_date: dates.past,
      });
      await TOOLS['set-next-action'].handler(c, actor, {
        idempotency_key: randomUUID(), ref: id(4), description: 'Review the revised terms', due_on: dates.future,
      });
      const board = await TOOLS['deal-room-board'].handler(c, actor, {});
      assert.equal(board.deals[0].next_date, dates.future);
      assert.equal(board.deals[0].next_step, 'Review the revised terms');
      assert.equal(board.deals[0].next_step_required, false);
    }));

    await t.test('a dated status update records the new plan once and retains attributed history', async () => transaction(async () => {
      await fixture(false);
      const dates = (await c.query("select (current_date-1)::text as past, (current_date+7)::text as future")).rows[0];
      const prior = await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Request revised terms', next_date: dates.past,
      });
      const args = {idempotency_key: randomUUID(), deal: id(4), text: 'Revised terms received',
        next_step: 'Review revised terms', next_date: dates.future};
      const update = await TOOLS['add-deal-note'].handler(c, actor, args);
      const replay = await TOOLS['add-deal-note'].handler(c, actor, args);
      assert.equal(replay.replayed, true);
      assert.equal(replay.note_id, update.note_id);
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_date, dates.future);
      assert.equal(room.next_step, 'Review revised terms');
      assert.equal(room.next_step_required, false);
      assert.equal(room.thread.filter(n => n.text === 'Revised terms received').length, 1);
      assert.equal(room.thread.filter(n => n.kind === 'next_step').length, 2);
      assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'dropped');
      assert.equal(room.next_actions.find(a => a.id === update.next_action_id).status, 'open');
    }));

    await t.test('status notes preserve due-today, future, and other-partner actions', async () => transaction(async () => {
      await fixture(false);
      const dates = (await c.query("select current_date::text as today, (current_date+7)::text as future, (current_date-1)::text as past")).rows[0];
      for (const day of [dates.today, dates.future]) {
        const step = await TOOLS['set-next-step'].handler(c, actor, {
          base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
          idempotency_key: randomUUID(), deal: id(4), text: 'Review revised terms', next_date: day,
        });
        await TOOLS['add-deal-note'].handler(c, actor, {
          idempotency_key: randomUUID(), deal: id(4), text: 'Additional context before review',
        });
        const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
        assert.equal(room.next_date, day);
        assert.equal(room.next_actions.find(a => a.id === step.next_action_id).status, 'open');
      }
      const dell = {...actor, id: id(7), slug: 'dell'};
      await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'dell','Synthetic Partner Two','human',true)", [dell.id]);
      const independent = await TOOLS['set-next-step'].handler(c, dell, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Independent follow-up', next_date: dates.past,
      });
      await TOOLS['add-deal-note'].handler(c, actor, {
        idempotency_key: randomUUID(), deal: id(4), text: 'Context from the other partner',
      });
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_actions.find(a => a.id === independent.next_action_id).status, 'open');
      assert.equal(room.next_date, dates.past);
    }));

    await t.test('a partner phase update supersedes the prior overdue action', async () => transaction(async () => {
      await fixture(false);
      const past = (await c.query("select (current_date-1)::text as day")).rows[0].day;
      await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Complete terms review', next_date: past,
      });
      const current = await TOOLS['read-deal-reconciliation'].handler(c, actor, {deal: id(4)});
      const update = await TOOLS['update-deal'].handler(c, actor, {
        idempotency_key: randomUUID(), deal: id(4), base_version: current.base_version, fields: {phase: 'legal'},
      });
      const board = await TOOLS['deal-room-board'].handler(c, actor, {});
      assert.equal(board.deals[0].next_date, null);
      assert.equal(board.deals[0].next_step_required, true);
      assert.equal(update.next_step_required, true);
    }));

    await t.test('Deal Room phase patches supersede overdue actions once with attributed history', async () => transaction(async () => {
      await fixture(false);
      const past = (await c.query("select (current_date-1)::text as day")).rows[0].day;
      const prior = await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Complete terms review', next_date: past,
      });
      const args = {idempotency_key: randomUUID(), deal: id(4), field: 'phase', value: 'legal', base_event_id: null};
      const update = await TOOLS['patch-deal-field'].handler(c, actor, args);
      const replay = await TOOLS['patch-deal-field'].handler(c, actor, args);
      assert.equal(replay.replayed, true);
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_date, null, 'phase patches must clear the expired displayed date');
      assert.equal(room.next_step, null);
      assert.equal(update.next_step_required, true);
      assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'dropped');
      const events = room.events.filter(e => e.verb === 'patch-deal-field' && e.field === 'next_date');
      assert.equal(events.length, 1);
      assert.equal(events[0].actor, 'joe');
      assert.ok(events[0].recorded_at);
      assert.deepEqual(events[0].new_value.superseded_action_ids, [prior.next_action_id]);
    }));

    for (const writer of ['add-deal-note', 'set-next-step', 'set-next-action', 'patch-deal-field', 'update-deal'])
      await t.test(`${writer} supersedes accepted overdue Call Mode actions without completing them`, async () => transaction(async () => {
        await fixture(false);
        const dates = (await c.query("select (current_date-1)::text as past, current_date::text as today, (current_date+7)::text as future")).rows[0];
        const dell = {...actor, id: id(7), slug: 'dell'};
        await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'dell','Synthetic Partner Two','human',true)", [dell.id]);
        await c.query("insert into deal(id,client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,$2,'Other Synthetic Assignment','renewal','research','joe',$3,$3)", [id(9), id(3), actor.id]);
        await TOOLS['set-next-step'].handler(c, actor, {
          base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
          idempotency_key: randomUUID(), deal: id(4), text: 'Request terms', next_date: dates.past,
        });
        const expired = await accept(id(4), 'joe', dates.past);
        const preserved = [await accept(id(4), 'joe', dates.today), await accept(id(4), 'joe', dates.future),
          await accept(id(4), 'dell', dates.past)];
        const otherDeal = await accept(id(9), 'joe', dates.past);
        const args = {idempotency_key: randomUUID(), deal: id(4)};
        if (writer === 'add-deal-note') Object.assign(args, {text: 'Terms received', next_step: 'Review revised terms', next_date: dates.future});
        if (writer === 'set-next-step') Object.assign(args, {base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version, text: 'Review revised terms', next_date: dates.future});
        if (writer === 'set-next-action') Object.assign(args, {ref: id(4), description: 'Review revised terms', due_on: dates.future});
        if (writer === 'patch-deal-field') Object.assign(args, {field: 'phase', value: 'legal', base_event_id: null});
        if (writer === 'update-deal') Object.assign(args, {fields: {phase: 'legal'}, base_version:
          (await TOOLS['read-deal-reconciliation'].handler(c, actor, {deal: id(4)})).base_version});
        const update = await TOOLS[writer].handler(c, actor, args);
        if (writer === 'set-next-action') assert.ok(update.dropped_post_call_action_ids.includes(expired),
          'replacement receipts must include expired Call Mode actions');
        assert.equal((await TOOLS[writer].handler(c, actor, args)).replayed, true);
        const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
        assert.equal(room.next_actions.find(a => a.id === expired).status, 'dropped', 'expired accepted actions must be superseded');
        // set-next-action explicitly replaces all of this owner's actions, including future ones.
        for (const action of writer === 'set-next-action' ? preserved.slice(2) : preserved)
          assert.equal(room.next_actions.find(a => a.id === action).status, 'open');
        const other = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(9)});
        assert.equal(other.next_actions.find(a => a.id === otherDeal).status, 'open');
        const events = room.events.filter(e => e.verb === writer && e.field === 'next_action' && e.new_value.superseded_action_ids?.includes(expired));
        assert.equal(events.length, 1, 'supersession must retain one attributed event on retry');
        assert.equal(events[0].actor, 'joe');
        assert.ok(events[0].recorded_at);
        assert.equal(room.attention, true);
      }));

    await t.test('explicit completion and replacement preserve each partner Call Mode history', async () => transaction(async () => {
      await fixture(false);
      const past = (await c.query("select (current_date-1)::text as day")).rows[0].day;
      await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'dell','Synthetic Partner Two','human',true)", [id(7)]);
      const completedId = await accept(id(4), 'joe', past);
      const independent = await accept(id(4), 'dell', past);
      const completed = await TOOLS['complete-action'].handler(c, actor, {
        idempotency_key: randomUUID(), ref: id(4), outcome: 'Reviewed call terms',
      });
      assert.equal(completed.count, 1);
      assert.equal(completed.completed[0].source, 'post_call_action');
      const replacedId = await accept(id(4), 'joe', past);
      const replacement = await TOOLS['set-next-action'].handler(c, actor, {
        idempotency_key: randomUUID(), ref: id(4), description: 'Review updated terms', due_on: past,
      });
      assert.deepEqual(replacement.dropped_post_call_action_ids, [replacedId]);
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_actions.find(a => a.id === completedId).status, 'done');
      assert.equal(room.next_actions.find(a => a.id === replacedId).status, 'dropped');
      assert.equal(room.next_actions.find(a => a.id === independent).status, 'open');
      assert.equal(room.next_actions.find(a => a.id === replacement.next_action_id).description, 'Review updated terms');
    }));

    for (const operation of ['unchanged patch', 'undo', 'conflict resolution'])
      await t.test(`${operation} applies phase supersession only after a real change`, async () => transaction(async () => {
        await fixture(false);
        const past = (await c.query("select (current_date-1)::text as day")).rows[0].day;
        const automatic = operation === 'unchanged patch' ? null : await TOOLS['patch-deal-field'].handler(c,
          {...actor, human: false, via: 'mcp', client_id: 'agent'}, {
            idempotency_key: randomUUID(), deal: id(4), field: 'phase', value: 'legal', base_event_id: null,
          });
        const prior = await TOOLS['set-next-step'].handler(c, actor, {
          base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
          idempotency_key: randomUUID(), deal: id(4), text: 'Review terms', next_date: past,
        });
        if (operation === 'unchanged patch') await TOOLS['patch-deal-field'].handler(c, actor, {
          idempotency_key: randomUUID(), deal: id(4), field: 'phase', value: 'research', base_event_id: null,
        });
        if (operation === 'undo') await TOOLS['revert-deal-field'].handler(c, actor, {
          idempotency_key: randomUUID(), event_id: automatic.event_id,
        });
        if (operation === 'conflict resolution') {
          const conflict = await TOOLS['patch-deal-field'].handler(c, actor, {
            idempotency_key: randomUUID(), deal: id(4), field: 'phase', value: 'negotiation', base_event_id: null,
          });
          assert.equal(conflict.ok, false);
          const unchanged = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
          assert.equal(unchanged.next_actions.find(a => a.id === prior.next_action_id).status, 'open');
          await TOOLS['resolve-conflict'].handler(c, actor, {
            idempotency_key: randomUUID(), conflict_id: conflict.conflict.id, winner: 'b',
          });
        }
        const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
        assert.equal(room.next_date, operation === 'unchanged patch' ? past : null);
        assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status,
          operation === 'unchanged patch' ? 'open' : 'dropped');
      }));

    await t.test('native sponsor updates audit expired Call Mode actions behind a future current plan', async () => transaction(async () => {
      await fixture(false);
      const dates = (await c.query("select (current_date-1)::text as past, (current_date+7)::text as future")).rows[0];
      await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Review revised terms', next_date: dates.future,
      });
      const expired = await accept(id(4), 'joe', dates.past);
      const native = {id: id(8), slug: 'codex', human: false, native_agent_verified: true, sponsoring_human_slug: 'joe'};
      await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'codex','Synthetic Runtime','automation',true)", [native.id]);
      await TOOLS['add-deal-note'].handler(c, native, {
        idempotency_key: randomUUID(), deal: id(4), text: 'Partner supplied updated terms',
      });
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_date, dates.future);
      assert.equal(room.next_step, 'Review revised terms');
      assert.equal(room.next_actions.find(a => a.id === expired).status, 'dropped');
      assert.ok(room.events.some(e => e.actor === 'codex' && e.field === 'next_action' &&
        e.new_value.superseded_action_ids.includes(expired) && e.recorded_at));
    }));

    await t.test('a verified native partner update supersedes the sponsor action with runtime attribution', async () => transaction(async () => {
      await fixture(false);
      const past = (await c.query("select (current_date-1)::text as day")).rows[0].day;
      const prior = await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Request revised terms', next_date: past,
      });
      const native = {id: id(8), slug: 'codex', human: false, native_agent_verified: true, sponsoring_human_slug: 'joe'};
      await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'codex','Synthetic Runtime','automation',true)", [native.id]);
      await TOOLS['add-deal-note'].handler(c, native, {
        idempotency_key: randomUUID(), deal: id(4), text: 'Revised terms received from partner',
      });
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_date, null);
      assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'dropped');
      assert.ok(room.events.some(e => e.verb === 'add-deal-note' && e.field === 'next_date' && e.actor === 'codex'));
    }));

    for (const reschedule of ['future', 'past']) await t.test(`status updates respect a ${reschedule} date reschedule`, async () => transaction(async () => {
      await fixture(false);
      const dates = (await c.query("select (current_date-1)::text as past, (current_date+7)::text as future")).rows[0];
      const prior = await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Review terms', next_date: dates[reschedule === 'future' ? 'past' : 'future'],
      });
      const board = await TOOLS['deal-room-board'].handler(c, actor, {});
      await TOOLS['patch-deal-field'].handler(c, actor, {
        idempotency_key: randomUUID(), deal: id(4), field: 'next_date', value: dates[reschedule],
        base_event_id: board.deals[0].field_base.next_date.id,
      });
      await TOOLS['add-deal-note'].handler(c, actor, {
        idempotency_key: randomUUID(), deal: id(4), text: 'Partner progress update',
      });
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_date, reschedule === 'future' ? dates.future : null);
      assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status,
        reschedule === 'future' ? 'open' : 'dropped');
    }));

    await t.test('a remaining partner plan supplies the next step and date consistently', async () => transaction(async () => {
      await fixture(false);
      const dates = (await c.query("select (current_date-1)::text as past, (current_date+7)::text as future")).rows[0];
      const dell = {...actor, id: id(7), slug: 'dell'};
      await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'dell','Synthetic Partner Two','human',true)", [dell.id]);
      await TOOLS['set-next-step'].handler(c, dell, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Review proposed terms', next_date: dates.future,
      });
      await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Request proposed terms', next_date: dates.past,
      });
      const update = await TOOLS['add-deal-note'].handler(c, actor, {
        idempotency_key: randomUUID(), deal: id(4), text: 'Proposed terms received',
      });
      const board = await TOOLS['deal-room-board'].handler(c, actor, {});
      assert.equal(board.deals[0].next_step, 'Review proposed terms');
      assert.equal(board.deals[0].next_date, dates.future);
      assert.equal(board.deals[0].next_step_required, false);
      assert.equal(update.next_step_required, false);
      assert.equal(update.prompt, undefined);
    }));

    await t.test('incomplete replacement plans are refused before changing status history', async () => transaction(async () => {
      await fixture(false);
      const past = (await c.query("select (current_date-1)::text as day")).rows[0].day;
      const prior = await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Request terms', next_date: past,
      });
      for (const fields of [{next_step: 'Review terms'}, {next_date: '2026-02-30'}, {next_date: past}]) {
        await assert.rejects(TOOLS['add-deal-note'].handler(c, actor, {
          idempotency_key: randomUUID(), deal: id(4), text: 'Status update', ...fields,
        }), e => e.payload?.error === 'next_step_and_date_required');
      }
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'open');
      assert.equal(room.thread.length, 1);
      assert.equal(room.next_date, past);
    }));

    await t.test('overdue migration replay preserves inactive prospect board membership and retained history', async () => transaction(async () => {
      await fixture(false);
      await c.query("update client set status='prospect' where id=$1", [id(3)]);
      const prior = await TOOLS['set-next-step'].handler(c, actor, {
        base_version: (await c.query('select version from deal where id=$1', [id(4)])).rows[0].version,
        idempotency_key: randomUUID(), deal: id(4), text: 'Synthetic retained plan', next_date: '2027-01-01',
      });
      await executeRegisteredTool(c, actor, 'set-prospect-engagement-status', {
        client_id: id(3), status: 'inactive', base_version: 2,
        reason: 'Synthetic deferral', idempotency_key: 'synthetic-overdue-inactive',
      });
      await c.query(readFileSync(path.join(root, 'migrations/0923_deal_status_next_step.sql'), 'utf8'));
      await c.query('set local role carr_reader');
      assert.deepEqual((await TOOLS['deal-room-board'].handler(c, actor, {})).deals, []);
      assert.deepEqual((await TOOLS['deal-board'].handler(c)).deals, []);
      const room = await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)});
      assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'open');
      assert.ok(room.thread.some(n => n.id === prior.next_step_id));
    }));

    const assignmentFixture = async () => {
      await fixture(false);
      await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Bank',$2,$2)", [id(20), actor.id]);
      await c.query("insert into party(id,kind,name,org_id,created_by,updated_by) values($1,'person','Synthetic Banker',$2,$3,$3)", [id(21), id(20), actor.id]);
      await c.query("insert into vendor(id,party_id,category,created_by,updated_by) values($1,$2,'banker',$3,$3),($4,$2,'banker',$3,$3)", [id(22),id(21),actor.id,id(23)]);
      return {deal_id:id(4),role:'lender',company_party_id:id(20),contact_party_id:id(21)};
    };
    await t.test('quoted activity retains human provenance on each vendor assignment event', async () => transaction(async () => {
      const {deal_id,...entry} = await assignmentFixture();
      const quote = 'Synthetic partner says this banker is the lender';
      const key = randomUUID();
      await TOOLS['log-activity'].handler(c,actor,{ref:deal_id,kind:'call',summary:'Synthetic sourced assignment',human_quote:quote,idempotency_key:key,
        vendor_assignments:[{...entry,vendor_id:id(22),base_version:1,assignment_base_version:1,note:'Keep this note'},
          {...entry,role:'attorney',vendor_id:id(23),base_version:1,assignment_base_version:1}]});
      const events = (await c.query('select field,cause,human_quote,idempotency_key,via,client_id from event where idempotency_key=$1 order by mutation_order',[key])).rows;
      assert.equal(events.filter(e=>e.field==='vendor_assignment').length,2);
      assert.equal(events.length,3, 'two role assertions and their activity share the same source');
      for (const event of events) {
        assert.equal(event.cause,'human_stated');
        assert.equal(event.human_quote,quote);
        assert.equal(event.via,actor.via);
        assert.equal(event.client_id,actor.client_id);
      }
    }));

    await t.test('invoiced deal detail retains vendor cards and agrees with the closed board', async () => transaction(async () => {
      const {deal_id,...entry} = await assignmentFixture();
      await TOOLS['update-vendor'].handler(c,actor,{vendor:id(22),base_version:1,assignment_base_version:1,idempotency_key:randomUUID(),fields:{deal_assignments:[{...entry,deal_id}]}});
      const before = await TOOLS['get-deal-room'].handler(c,actor,{deal:deal_id});
      assert.equal(before.vendors.length,1);
      await c.query("update deal set phase='closed',outcome='won',closed_on=current_date,invoiced_on=current_date where id=$1",[deal_id]);
      const detail = await TOOLS['get-deal-room'].handler(c,actor,{deal:deal_id});
      assert.deepEqual(detail.vendors,before.vendors);
      const board = await TOOLS['deal-room-board'].handler(c,actor,{});
      assert.deepEqual(board.closed_deals.find(d=>d.id===deal_id).vendors,detail.vendors);
      await c.query('update deal_vendor_assignment set active=false where deal_id=$1',[deal_id]);
      const empty = await TOOLS['get-deal-room'].handler(c,actor,{deal:deal_id});
      assert.deepEqual(empty.vendors,[]);
    }));

    await t.test('retired company assignment permits explicit contact reassignment while active duplicates refuse', async () => transaction(async () => {
      const entry = await assignmentFixture();
      await c.query("insert into vendor(id,party_id,category,created_by,updated_by) values($1,$2,'banker',$3,$3)", [id(24),id(20),actor.id]);
      const update = (vendor,base_version,assignment_base_version,active=true) => TOOLS['update-vendor'].handler(c,actor,{
        vendor,base_version,assignment_base_version,idempotency_key:randomUUID(),fields:{deal_assignments:[{...entry,active}]},
      });
      await update(id(24),1,1);
      await c.query('savepoint active_duplicate');
      await assert.rejects(update(id(22),1,1),e=>e.code==='23505');
      await c.query('rollback to savepoint active_duplicate');
      await update(id(24),2,2,false);
      await update(id(22),1,1);
      const cards = (await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)})).vendors;
      assert.equal(cards.length,1);
      assert.equal(cards[0].party_id,id(21));
      assert.equal((await c.query('select active from deal_vendor_assignment where party_id=$1',[id(20)])).rows[0].active,false);
    }));

    for (const scenario of ['duplicate vendor calls','company and contact vendor writes']) {
      await t.test(`concurrent ${scenario} finish without a database deadlock`, {timeout:10000}, async () => {
        const entry = await assignmentFixture();
        await c.query("insert into vendor(id,party_id,category,created_by,updated_by) values($1,$2,'banker',$3,$3)", [id(24),id(20),actor.id]);
        const peers = [new pg.Client(connection),new pg.Client(connection)];
        let release;
        const paused = new Promise(resolve => { release=resolve; });
        let reached;
        const ready = new Promise(resolve => { reached=resolve; });
        try {
          await Promise.all(peers.map(peer=>peer.connect()));
          for (const peer of peers) await peer.query("begin; set local deadlock_timeout='50ms'; set local statement_timeout='5s'");
          const query = peers[0].query.bind(peers[0]);
          let held = false;
          peers[0].query = async (sql,values) => {
            const result = await query(sql,values);
            const pause = scenario==='duplicate vendor calls' ? /for update/i : /for update of p|select id from public.party/i;
            if (!held && pause.test(sql)) { held=true; reached(); await paused; }
            return result;
          };
          const run = async (peer,index) => {
            try {
              let result;
              if (scenario==='duplicate vendor calls') {
                const {deal_id,...fields} = entry;
                result = await TOOLS['log-activity'].handler(peer,actor,{ref:deal_id,kind:'call',summary:'Synthetic concurrent assignment',idempotency_key:randomUUID(),
                  vendor_assignments:[22+index,23-index].map((n,i)=>({...fields,role:i?'attorney':'lender',vendor_id:id(n),base_version:1,assignment_base_version:1}))});
              } else {
                result = await TOOLS['update-vendor'].handler(peer,actor,{vendor:id(index?22:24),base_version:1,assignment_base_version:1,idempotency_key:randomUUID(),
                  fields:{deal_assignments:[{...entry,role:index?'attorney':'lender'}]}});
              }
              await peer.query('commit');
              return result;
            } catch (error) { await peer.query('rollback'); return {error}; }
          };
          const first = run(peers[0],0);
          await ready;
          const second = run(peers[1],1);
          let blocked=false;
          for (let attempt=0;attempt<200;attempt++) {
            blocked=(await c.query('select cardinality(pg_blocking_pids($1))>0 blocked',[peers[1].processID])).rows[0].blocked;
            if(blocked) break;
            await new Promise(resolve=>setTimeout(resolve,5));
          }
          release();
          const results = await Promise.all([first,second]);
          assert.equal(blocked,true,'second writer reaches the contested database locks');
          assert.ok(results.some(result=>result.ok),'one concurrent writer succeeds');
          for (const result of results) if(result.error) assert.equal(result.error.payload?.error,'version_conflict',result.error.code || result.error.message);
        } finally {
          release?.();
          await Promise.all(peers.map(peer=>peer.end()));
          await c.query('truncate deal_vendor_assignment,activity,event,tool_call,vendor,deal,client,party');
        }
      });
    }

    await t.test('stale duplicate vendor rows cannot overwrite shared party assignments', async () => transaction(async () => {
      const entry = await assignmentFixture();
      const update = (vendor, note) => TOOLS['update-vendor'].handler(c,actor,{
        vendor,base_version:1,assignment_base_version:1,idempotency_key:randomUUID(),fields:{deal_assignments:[{...entry,note}]},
      });
      await update(id(22),'New assignment');
      await c.query('savepoint stale_duplicate');
      await assert.rejects(update(id(23),'Stale overwrite'),e=>e.payload?.error==='version_conflict');
      await c.query('rollback to savepoint stale_duplicate');
      const context = await TOOLS['vendor-assignment-context'].handler(c,actor,{vendor:id(23)});
      assert.equal(context.vendor.assignment_version,2);
      assert.equal((await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)})).vendors[0].note,'New assignment');
    }));

    await t.test('one call assigns multiple roles using one observed vendor and party version', async () => transaction(async () => {
      const {deal_id,...entry} = await assignmentFixture();
      const call = {ref:deal_id,kind:'call',summary:'Synthetic multiple roles',idempotency_key:randomUUID(),vendor_assignments:
        ['lender','attorney'].map(role=>({...entry,role,vendor_id:id(22),base_version:1,assignment_base_version:1}))};
      const result = await TOOLS['log-activity'].handler(c,actor,call);
      assert.equal(result.vendor_assignments.length,2);
      const context = await TOOLS['vendor-assignment-context'].handler(c,actor,{vendor:id(22)});
      assert.equal(context.vendor.base_version,2);
      assert.equal(context.vendor.assignment_version,2);
      assert.deepEqual((await TOOLS['get-deal-room'].handler(c,actor,{deal:deal_id})).vendors.map(v=>v.role),['attorney','lender']);
      assert.equal((await TOOLS['log-activity'].handler(c,actor,call)).replayed,true);
    }));

    await t.test('duplicate vendor rows share one version in a call and cannot repeat the same party role', async () => transaction(async () => {
      const {deal_id,...entry} = await assignmentFixture();
      const call = roles => ({ref:deal_id,kind:'call',summary:'Synthetic duplicate rows',idempotency_key:randomUUID(),vendor_assignments:
        roles.map((role,index)=>({...entry,role,vendor_id:id(22+index),base_version:1,assignment_base_version:1}))});
      await c.query('savepoint repeated_role');
      await assert.rejects(TOOLS['log-activity'].handler(c,actor,call(['lender','lender'])),e=>e.payload?.error==='deal_assignment_invalid');
      await c.query('rollback to savepoint repeated_role');
      await TOOLS['log-activity'].handler(c,actor,call(['lender','attorney']));
      for (const vendor of [id(22),id(23)]) {
        const context = (await TOOLS['vendor-assignment-context'].handler(c,actor,{vendor})).vendor;
        assert.equal(context.base_version,2);
        assert.equal(context.assignment_version,2);
      }
    }));

    await t.test('assignment roles reject generic professional and accept the seven specific responsibilities', async () => transaction(async () => {
      const entry = await assignmentFixture();
      const request = role => ({vendor:id(22),base_version:1,assignment_base_version:1,idempotency_key:randomUUID(),fields:{deal_assignments:[{...entry,role}]}});
      await assert.rejects(TOOLS['update-vendor'].handler(c,actor,request('professional')),e=>e.payload?.error==='deal_assignment_invalid');
      for (const role of ['lender','contractor','architect','attorney','title','inspector','landlord_rep']) {
        await c.query('savepoint specific_role');
        assert.equal((await TOOLS['update-vendor'].handler(c,actor,request(role))).ok,true);
        await c.query('rollback to savepoint specific_role');
      }
    }));

    await t.test('vendor assignments survive duplicate vendor rows and show role, company and contact on both deal reads', async () => transaction(async () => {
      await fixture(false);
      await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Bank',$2,$2)", [id(20), actor.id]);
      await c.query("insert into party(id,kind,name,org_id,email,created_by,updated_by) values($1,'person','Synthetic Banker',$2,'banker@example.invalid',$3,$3)", [id(21), id(20), actor.id]);
      await c.query("insert into vendor(id,party_id,category,vendor_ref,created_by,updated_by) values($1,$2,'banker','V-DEMO-1',$3,$3),($4,$2,'banker','V-DEMO-2',$3,$3)", [id(22), id(21), actor.id, id(23)]);
      const context = await TOOLS['vendor-assignment-context'].handler(c,actor,{vendor:id(22)});
      assert.equal(context.vendor.base_version,1);
      assert.equal(context.vendor.company.id,id(20));
      assert.equal(context.vendor.contacts[0].id,id(21));
      const assignment = { deal_id: id(4), role: 'lender', company_party_id: id(20), contact_party_id: id(21) };
      await TOOLS['update-vendor'].handler(c, actor, {vendor:id(22),base_version:1,assignment_base_version:1,idempotency_key:randomUUID(),fields:{deal_assignments:[assignment]}});
      const detail = await TOOLS['get-deal-room'].handler(c, actor, {deal:id(4)});
      assert.equal(detail.vendors.length,1);
      assert.equal(detail.vendors[0].role,'lender');
      assert.deepEqual(detail.vendors[0].company,{id:id(20),name:'Synthetic Bank'});
      assert.deepEqual(detail.vendors[0].contact,{id:id(21),name:'Synthetic Banker',email:'banker@example.invalid',phone:null});
      const board = await TOOLS['deal-room-board'].handler(c, actor, {});
      assert.deepEqual(board.deals[0].vendors,detail.vendors);
      const call = {ref:id(4),kind:'call',summary:'Synthetic financing update',idempotency_key:randomUUID(),
        vendor_assignments:[{vendor_id:id(23),base_version:1,assignment_base_version:2,role:'lender',company_party_id:id(20),contact_party_id:id(21),note:'Application received'}]};
      const result = await TOOLS['log-activity'].handler(c,actor,call);
      assert.equal(result.vendor_assignments.length,1);
      assert.equal((await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)})).vendors[0].note,'Application received');
      assert.equal((await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)})).events.find(e=>e.field==='vendor_assignment' && e.new_value?.vendor_assignment?.note==='Application received')?.verb,'log-activity');
      assert.equal((await TOOLS['log-activity'].handler(c,actor,call)).replayed,true);
      const request = (fields,base_version=2) => ({vendor:id(22),base_version,assignment_base_version:base_version+1,idempotency_key:randomUUID(),fields});
      for (const change of [{role:'vendor'},{role:'worked'},{role:'other'},{role:'Lender'},{deal_id:'vague deal'},
        {contact_party_id:id(20)},{company_party_id:id(2)},{active:'yes'},{unexpected:'value'}]) {
        await c.query('savepoint invalid_assignment');
        await assert.rejects(TOOLS['update-vendor'].handler(c,actor,request({deal_assignments:[{...assignment,...change}]})),e=>e.payload?.error==='deal_assignment_invalid');
        await c.query('rollback to savepoint invalid_assignment');
      }
      await c.query('update party set org_id=null where id=$1',[id(21)]);
      const retire = request({deal_assignments:[{...assignment,active:false}]});
      await TOOLS['update-vendor'].handler(c,actor,retire);
      assert.deepEqual((await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)})).vendors,[]);
      await c.query('update party set org_id=$1 where id=$2',[id(20),id(21)]);
      await TOOLS['update-vendor'].handler(c,actor,request({deal_assignments:[assignment]},3));
      await c.query('savepoint atomic_call');
      await assert.rejects(TOOLS['log-activity'].handler(c,actor,{...call,idempotency_key:randomUUID(),vendor_assignments:[
        {...call.vendor_assignments[0],vendor_id:id(22),base_version:4,assignment_base_version:5,note:'Must roll back'},
        {...call.vendor_assignments[0],base_version:2,assignment_base_version:5,contact_party_id:id(20)},
      ]}),e=>e.payload?.error==='deal_assignment_invalid');
      await c.query('rollback to savepoint atomic_call');
      const afterFailedCall = await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)});
      assert.equal(afterFailedCall.vendors[0].note,'Application received');
      assert.equal(afterFailedCall.activities.length,1);
      await c.query('savepoint stale_call');
      await assert.rejects(TOOLS['log-activity'].handler(c,actor,{...call,idempotency_key:randomUUID()}),e=>e.payload?.error==='version_conflict');
      await c.query('rollback to savepoint stale_call');
      assert.equal((await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)})).events.filter(e=>e.field==='vendor_assignment').length,4);
      await c.query('update vendor set merged_into=$1 where id=$2',[id(22),id(23)]);
      assert.equal((await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)})).vendors.length,1,'retiring a duplicate vendor row preserves the party assignment');
      await c.query('update vendor set merged_into=null where id=$1',[id(23)]);
      await c.query('set local role carr_reader');
      assert.deepEqual(await TOOLS['vendor-duplicate-report'].handler(c,actor,{}),{
        dry_run:true,counts:{live_vendor_rows:2,same_party_groups:1,extra_vendor_rows:1,email_match_groups:0,name_company_match_groups:0},
      });
      await c.query('reset role');
      await c.query("insert into party(id,kind,name,org_id,email,created_by,updated_by) values($1,'person','Synthetic Banker',$2,' BANKER@example.invalid ',$3,$3)",[id(24),id(20),actor.id]);
      await c.query("insert into vendor(id,party_id,category,created_by,updated_by) values($1,$2,'banker',$3,$3)",[id(25),id(24),actor.id]);
      await c.query('set local role carr_reader');
      const report = await TOOLS['vendor-duplicate-report'].handler(c,actor,{});
      assert.deepEqual(report.counts,{live_vendor_rows:3,same_party_groups:1,extra_vendor_rows:1,email_match_groups:1,name_company_match_groups:1});
      await c.query('reset role');
      for (const retirement of ['update vendor set merged_into=$1 where id=$2','update party set merged_into=$1 where id=$2','update party set deleted_at=now() where $1::uuid is not null and id=$2']) {
        await c.query('savepoint retired');
        await c.query(retirement,retirement.startsWith('update vendor')?[id(22),id(25)]:[id(21),id(24)]);
        await c.query('set local role carr_reader');
        assert.deepEqual((await TOOLS['vendor-duplicate-report'].handler(c,actor,{})).counts,{live_vendor_rows:2,same_party_groups:1,extra_vendor_rows:1,email_match_groups:0,name_company_match_groups:0});
        await c.query('rollback to savepoint retired');
      }
    }));

    await t.test('migration preserves every existing board prospect and audits the active backfill', async () => transaction(async () => {
      await fixture(true);
      await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'system','Synthetic System','system',true)",[id(99)]);
      await c.query("update client set status='prospect'");
      await c.query("insert into deal(id,client_id,name,deal_type,phase,operating_state,parking_reason,parking_note,parked_at,parked_by,created_by,updated_by) values($1,$2,'Synthetic Parked Assignment','renewal','research','parked','other','Synthetic hold',now(),$3,$3,$3)",[id(31),id(3),actor.id]);
      await c.query('drop view v_client_engagement_status, v_lead_prospect_engagement');
      await c.query('alter table client drop column prospect_engagement_status cascade');
      const previousBoard = readFileSync(path.join(root,'migrations/0782_deal_invoice_read_fields.sql'),'utf8')
        .match(/create or replace view v_deal_room_board as[\s\S]*?;/)[0];
      await c.query(previousBoard);
      const before = (await c.query('select * from v_deal_room_board order by id')).rows;
      assert.deepEqual(before.map(d=>d.id),[id(4),id(31)]);
      const clientsBefore = (await c.query('select id,status,version,updated_by from client order by id')).rows;
      await c.query(readFileSync(path.join(root,'migrations/0920_prospect_engagement_status.sql'),'utf8'));
      assert.deepEqual((await c.query('select * from v_deal_room_board order by id')).rows,before);
      assert.deepEqual((await c.query('select id,status,version,updated_by from client order by id')).rows,clientsBefore);
      assert.deepEqual((await c.query('select prospect_engagement_status from client order by id')).rows,
        [{prospect_engagement_status:'active'},{prospect_engagement_status:'active'}]);
      assert.deepEqual((await c.query(`select subject_id,actor_id,cause,field,old_value,new_value,idempotency_key
        from event where verb='backfill-prospect-engagement-status' order by subject_id`)).rows,
        [id(3),id(6)].map(subject_id=>({subject_id,actor_id:id(99),cause:'import_migration',
          field:'prospect_engagement_status',old_value:null,new_value:{prospect_engagement_status:'active'},
          idempotency_key:`0915:prospect-engagement:${subject_id}`})));
    }));

    await t.test('0915 forward reverse forward preserves inactive choices and all business records', async () => transaction(async () => {
      await fixture(false);
      await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'system','Synthetic System','system',true)",[id(99)]);
      await c.query("update client set status='prospect' where id=$1",[id(3)]);
      await c.query("insert into lead(id,registry_ref,party_id,client_id,stage,notes,score,created_by,updated_by) values($1,'L-900001',$2,$3,'opportunity','Synthetic retained plans',72,$4,$4)",[id(30),id(2),id(3),actor.id]);
      await c.query("insert into deal_note(id,deal_id,kind,text,actor_id) values($1,$2,'note','Synthetic retained history',$3)",[id(35),id(4),actor.id]);
      await c.query('drop view v_client_engagement_status,v_lead_prospect_engagement');
      await c.query('alter table client drop column prospect_engagement_status cascade');
      const forward = readFileSync(path.join(root,'migrations/0920_prospect_engagement_status.sql'),'utf8');
      await c.query(forward);
      await executeRegisteredTool(c,actor,'set-prospect-engagement-status',{
        client_id:id(3),status:'inactive',base_version:2,reason:'Synthetic deferral',idempotency_key:'synthetic-roundtrip'});
      const records = async () => Object.fromEntries(await Promise.all(
        ['party','client','deal','deal_note','lead','event','tool_call'].map(async table =>
          [table,(await c.query(`select * from ${table} order by 1`)).rows])));
      const before = await records();
      assert.deepEqual((await c.query('select id from v_deal_room_board')).rows,[]);
      await c.query(readFileSync(path.join(root,'ops/sql/reverse-0915-prospect-engagement.sql'),'utf8'));
      assert.deepEqual((await c.query('select id from v_deal_room_board')).rows,[{id:id(4)}]);
      assert.deepEqual((await c.query("select to_regclass('public.v_client_engagement_status') as view")).rows,[{view:null}]);
      assert.deepEqual(await records(),before);
      await c.query(forward);
      assert.deepEqual(await records(),before);
      assert.deepEqual((await c.query('select id from v_deal_room_board')).rows,[]);
      await c.query('set local role carr_reader');
      assert.equal((await TOOLS['lead-board'].handler(c,actor,{workspace:'leads',lead_id:id(30)})).detail.notes,'Synthetic retained plans');
      assert.equal((await c.query('select prospect_engagement_status from v_client_engagement_status')).rows[0].prospect_engagement_status,'inactive');
    }));

    await t.test('pending migration installs active-only board membership and reader grants on the previous schema', async () => transaction(async () => {
      await c.query('drop view v_client_engagement_status, v_lead_prospect_engagement');
      await c.query('alter table client drop column prospect_engagement_status cascade');
      await c.query(readFileSync(path.join(root,'migrations/0920_prospect_engagement_status.sql'),'utf8'));
      await c.query(schema.match(/CREATE VIEW public\.v_open_deal_room_board AS[\s\S]*?;/)[0]);
      await c.query('grant select on v_open_deal_room_board to carr_reader,carr_writer');
      await c.query(schema.match(/CREATE VIEW public\.v_deal_room_account AS[\s\S]*?;/)[0]);
      await fixture(false);
      await c.query("update client set status='prospect' where id=$1",[id(3)]);
      assert.equal((await TOOLS['deal-room-board'].handler(c,actor,{})).deals[0].prospect_engagement_status,'active');
      await c.query("update client set prospect_engagement_status='inactive' where id=$1",[id(3)]);
      assert.deepEqual((await TOOLS['deal-room-board'].handler(c,actor,{})).deals,[]);
      await c.query('set local role carr_reader');
      assert.deepEqual((await c.query('select * from v_lead_prospect_engagement')).rows,[]);
    }));

    await t.test('a new prospect created over a lead starts active and appears on the board', async () => transaction(async () => {
      await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Intake',$2,$2)",[id(2),actor.id]);
      const lead = await executeRegisteredTool(c,actor,'new-lead',{
        party_id:id(2),stage:'opportunity',idempotency_key:'synthetic-intake-lead'});
      const client = await executeRegisteredTool(c,actor,'new-client',{
        party_id:id(2),status:'prospect',acquisition_source:'other',idempotency_key:'synthetic-intake-client',
        research_evidence:{sources:[{url:'https://example.com/synthetic',observed_at:'2026-01-01T00:00:00Z'}],
          field_evidence:{practice_name:[0],address:[0],phone:[0],specialty:[0],practitioners:[0],hours:[0]},discrepancies:[]}});
      await c.query("insert into deal(id,client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,$2,'Synthetic Intake Assignment','renewal','research','joe',$3,$3)",[id(4),client.client_id,actor.id]);
      const board = (await TOOLS['deal-room-board'].handler(c,actor,{})).deals;
      assert.deepEqual(board.map(d=>[d.id,d.client_id,d.prospect_engagement_status]),[[id(4),client.client_id,'active']]);
      assert.equal((await TOOLS['lead-board'].handler(c,actor,{workspace:'leads'})).leads[0].id,lead.lead_id);
    }));

    await t.test('automation cannot mark a prospect inactive without partner authority', async () => transaction(async () => {
      await fixture(false);
      await c.query("update client set status='prospect' where id=$1",[id(3)]);
      await assert.rejects(TOOLS['set-prospect-engagement-status'].handler(c,{...actor,human:false,slug:'automation'}, {
        client_id:id(3),status:'inactive',base_version:2,reason:'Synthetic automated inference',idempotency_key:'synthetic-automation'
      }),e=>e.payload?.error==='human_confirmation_required');
      assert.equal((await TOOLS['deal-room-board'].handler(c,actor,{})).deals.length,1);
      assert.deepEqual((await c.query('select * from lead')).rows,[]);
      assert.deepEqual((await c.query('select * from event')).rows,[]);
    }));

    await t.test('inactive prospects return to Leads and active prospects return to their existing deals', async () => transaction(async () => {
      await fixture(false);
      await c.query("update client set status='prospect',prospect_engagement_status='active' where id=$1", [id(3)]);
      const before = (await TOOLS['deal-room-board'].handler(c,actor,{})).deals[0];
      const args = {client_id:id(3),status:'inactive',base_version:before.client_version,
        reason:'Synthetic pipeline review',idempotency_key:'synthetic-inactive'};
      const inactive = await executeRegisteredTool(c,actor,'set-prospect-engagement-status',args);
      assert.deepEqual((await TOOLS['deal-room-board'].handler(c,actor,{})).deals,[]);
      const leads = (await TOOLS['lead-board'].handler(c,actor,{workspace:'leads'})).leads;
      assert.equal(leads.length,1);
      assert.equal(leads[0].stage,'nurture_drip');
      assert.equal(leads[0].client_id,id(3));
      assert.equal(leads[0].prospect_engagement_status,'inactive');
      assert.equal(leads[0].prospect_client_version,inactive.client_version);
      assert.deepEqual(await executeRegisteredTool(c,actor,'set-prospect-engagement-status',args),{replayed:true,...inactive});
      const active = await executeRegisteredTool(c,actor,'set-prospect-engagement-status',{
        ...args,status:'active',base_version:inactive.client_version,idempotency_key:'synthetic-active'});
      assert.deepEqual((await TOOLS['deal-room-board'].handler(c,actor,{})).deals.map(d=>d.id),[id(4)]);
      const after = (await TOOLS['lead-board'].handler(c,actor,{workspace:'leads'})).leads;
      assert.equal(after.length,1);
      assert.equal(after[0].id,leads[0].id);
      assert.equal(after[0].stage,'opportunity');
      assert.equal(after[0].prospect_engagement_status,'active');
      assert.equal(after[0].prospect_client_version,active.client_version);
      assert.deepEqual((await c.query("select old_value->>'prospect_engagement_status' as old,new_value->>'prospect_engagement_status' as new from event where subject_type='client' order by mutation_order")).rows,
        [{old:'active',new:'inactive'},{old:'inactive',new:'active'}]);
      assert.deepEqual((await c.query("select new_value->>'stage' as stage from event where verb='update-lead' order by mutation_order")).rows,[{stage:'opportunity'}]);
    }));

    await t.test('inactive prospect detail reads back authoritative engagement and version as carr_reader', async () => transaction(async () => {
      await fixture(false);
      await c.query("update client set status='prospect' where id=$1",[id(3)]);
      const result = await executeRegisteredTool(c,actor,'set-prospect-engagement-status',{
        client_id:id(3),status:'inactive',base_version:2,reason:'Synthetic lost confirmation',idempotency_key:'synthetic-readback'});
      await c.query('set local role carr_reader');
      const detail = await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)});
      assert.equal(detail.client_id,id(3));
      assert.equal(detail.client_status,'prospect');
      assert.equal(detail.prospect_engagement_status,'inactive');
      assert.equal(detail.client_version,result.client_version);
    }));

    await t.test('inactive prospect accounts leave the portfolio summaries', async () => transaction(async () => {
      await fixture(true);
      await c.query("update client set status='prospect',prospect_engagement_status='inactive' where id=$1",[id(6)]);
      assert.deepEqual((await TOOLS['deal-room-board'].handler(c,actor,{})).accounts,[]);
    }));

    await t.test('existing linked lead, multiple deals and parking survive both engagement directions', async () => transaction(async () => {
      await fixture(false);
      await c.query("update client set status='prospect' where id=$1",[id(3)]);
      await c.query("insert into lead(id,registry_ref,party_id,client_id,stage,notes,score,created_by,updated_by) values($1,'L-900001',$2,$3,'opportunity','Synthetic retained history',72,$4,$4)",[id(30),id(2),id(3),actor.id]);
      await c.query("insert into deal(id,client_id,name,deal_type,phase,operating_state,parking_reason,parking_note,parked_at,parked_by,created_by,updated_by) values($1,$2,'Synthetic Parked Assignment','renewal','research','parked','other','Synthetic hold',now(),$3,$3,$3)",[id(31),id(3),actor.id]);
      const inactive = await executeRegisteredTool(c,actor,'set-prospect-engagement-status',{
        client_id:id(3),status:'inactive',base_version:2,reason:'Synthetic deferral',idempotency_key:'synthetic-linked-inactive'});
      await c.query('set local role carr_reader');
      assert.deepEqual((await TOOLS['deal-board'].handler(c)).deals,[]);
      const leads = (await TOOLS['lead-board'].handler(c,actor,{workspace:'leads',lead_id:id(30)}));
      assert.equal(leads.detail.notes,'Synthetic retained history');
      assert.equal(leads.detail.stage,'nurture_drip');
      assert.equal(leads.leads.length,1);
      await c.query('reset role');
      await executeRegisteredTool(c,actor,'set-prospect-engagement-status',{
        client_id:id(3),status:'active',base_version:inactive.client_version,reason:'Synthetic renewed interest',idempotency_key:'synthetic-linked-active'});
      const board = (await TOOLS['deal-room-board'].handler(c,actor,{})).deals;
      assert.equal(board.length,2);
      assert.equal(board.find(d=>d.id===id(31)).operating_state,'parked');
      assert.equal(board.find(d=>d.id===id(31)).parking_note,'Synthetic hold');
      assert.equal((await TOOLS['lead-board'].handler(c,actor,{workspace:'leads'})).leads[0].id,id(30));
    }));

    await t.test('engagement transition refuses a changed signed-in partner', async () => transaction(async () => {
      await fixture(false);
      await c.query("update client set status='prospect' where id=$1",[id(3)]);
      await assert.rejects(executeRegisteredTool(c,actor,'set-prospect-engagement-status',{
        client_id:id(3),status:'inactive',base_version:2,expected_actor:'dell',reason:'Synthetic review',idempotency_key:'synthetic-actor-switch'
      }),e=>e.payload?.error==='account_changed');
      assert.equal((await TOOLS['deal-room-board'].handler(c,actor,{})).deals.length,1);
    }));

    await t.test('reactivation refuses a do-not-contact party without creating a lead or audit event', async () => transaction(async () => {
      await fixture(false);
      await c.query("update client set status='prospect',prospect_engagement_status='inactive' where id=$1",[id(3)]);
      await c.query("update party set contact_state='do_not_contact' where id=$1",[id(2)]);
      await assert.rejects(executeRegisteredTool(c,actor,'set-prospect-engagement-status',{
        client_id:id(3),status:'active',base_version:2,reason:'Synthetic review',idempotency_key:'synthetic-protected-party'
      }),e=>e.payload?.error==='prospect_party_protected');
      assert.equal((await c.query('select count(*)::int n from lead')).rows[0].n,0);
      assert.equal((await c.query('select count(*)::int n from event')).rows[0].n,0);
    }));

    await t.test('engagement updates reject stale versions, invalid status, empty reasons and merged clients', async () => transaction(async () => {
      await fixture(true);
      const args = {client_id:id(3),status:'active',base_version:1,reason:'Synthetic review',idempotency_key:'synthetic-invalid'};
      await assert.rejects(executeRegisteredTool(c,actor,'set-prospect-engagement-status',{...args,status:'parked'}), error=>error.payload?.error==='value_not_in_declared_vocabulary');
      await assert.rejects(executeRegisteredTool(c,actor,'set-prospect-engagement-status',{...args,reason:' '}), error=>error.payload?.error==='engagement_reason_required');
      await c.query('update client set merged_into=$2 where id=$1',[id(3),id(6)]);
      await assert.rejects(executeRegisteredTool(c,actor,'set-prospect-engagement-status',args), error=>error.payload?.error==='version_conflict');
      await assert.rejects(executeRegisteredTool(c,actor,'set-prospect-engagement-status',{...args,base_version:2}), error=>error.payload?.error==='client_merged_away');
      assert.equal((await c.query("select count(*)::int count from event where subject_type='client'")).rows[0].count,0);
    }));

    await t.test('national-account Lost history uses client ID when client_ref is null', async () => transaction(async () => {
      await fixture(true);
      await c.query("update deal set phase='closed',outcome='lost' where id=$1", [id(4)]);
      await c.query('set local role carr_reader');
      const team = await TOOLS['deal-room-board'].handler(c, actor, {workspace:'team'});
      const account = await TOOLS['deal-room-board'].handler(c, actor, {workspace:'national_account',account_client_id:id(6)});
      assert.deepEqual(team.lost_deals, [], 'account history never leaks into team history');
      assert.deepEqual(account.lost_deals.map(d => [d.id,d.client_ref,d.workspace_kind,d.account_client_id]), [[id(4),null,'national_account',id(6)]]);
    }));

    await t.test('board and detail expose first phase start dates from occurrence history', async () => transaction(async () => {
      await fixture(false);
      for (const [n, phase, occurred, recorded] of [
        [31,'research','2026-09-01T04:00:00Z','2026-09-05T12:00:00Z'],
        [32,'negotiation','2026-09-20T15:00:00Z','2026-09-21T12:00:00Z'],
        [33,'research','2026-10-01T15:00:00Z','2026-10-02T12:00:00Z'],
      ]) await c.query(`insert into event(id,actor_id,verb,subject_type,subject_id,field,new_value,occurred_at,recorded_at,cause)
        values($1,$2,'patch-deal-field','deal',$3,'phase',$4,$5,$6,'human_stated')`,
        [id(n),actor.id,id(4),JSON.stringify({phase}),occurred,recorded]);
      await c.query('set local role carr_reader');
      const board = await TOOLS['deal-room-board'].handler(c,actor,{workspace:'team'});
      const detail = await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)});
      for (const row of [board.deals[0],detail]) assert.deepEqual(row.phase_started_on, {
        research:'2026-08-31',negotiation:'2026-09-20',
      });
      assert.equal((await c.query("select has_table_privilege('carr_reader','event','select') allowed")).rows[0].allowed,false);
      await c.query('reset role');
      await c.query('delete from event where subject_id=$1',[id(4)]);
      await c.query('set local role carr_reader');
      assert.deepEqual((await TOOLS['deal-room-board'].handler(c,actor,{workspace:'team'})).deals[0].phase_started_on,{});
    }));

    await t.test('undo removes the call phase start and a later move supplies its own date', async () => transaction(async () => {
      await fixture(false);
      const move = await TOOLS['patch-deal-field'].handler(c, actor, {
        idempotency_key: id(180), deal: id(4), field: 'phase', value: 'negotiation', base_event_id: null,
        occurred_on: '2026-09-01',
      });
      const undo = await TOOLS['revert-deal-field'].handler(c, actor, { idempotency_key: id(181), event_id: move.event_id });
      const undone = await TOOLS['deal-room-board'].handler(c, actor, {workspace:'team'});
      assert.equal(undone.deals[0].phase_started_on.negotiation, undefined);
      const restored = await TOOLS['revert-deal-field'].handler(c, actor, { idempotency_key: id(183), event_id: undo.event_id });
      assert.equal((await TOOLS['deal-room-board'].handler(c, actor, {workspace:'team'})).deals[0].phase_started_on.negotiation, '2026-09-01');
      const undoneAgain = await TOOLS['revert-deal-field'].handler(c, actor, { idempotency_key: id(184), event_id: restored.event_id });
      await TOOLS['patch-deal-field'].handler(c, actor, {
        idempotency_key: id(182), deal: id(4), field: 'phase', value: 'negotiation', base_event_id: undoneAgain.event_id,
        occurred_on: '2026-10-01',
      });
      const board = await TOOLS['deal-room-board'].handler(c, actor, {workspace:'team'});
      assert.equal(board.deals[0].phase_started_on.negotiation, '2026-10-01');
    }));

    await t.test('repeating the current phase through the handler does not create a phase start', async () => transaction(async () => {
      await fixture(false);
      const written = await TOOLS['patch-deal-field'].handler(c, actor, {
        idempotency_key: id(34), deal: id(4), field: 'phase', value: 'research', base_event_id: null,
      });
      assert.equal(written.ok, true);
      await c.query('set local role carr_reader');
      const board = await TOOLS['deal-room-board'].handler(c, actor, {workspace:'team'});
      const detail = await TOOLS['get-deal-room'].handler(c, actor, {deal:id(4)});
      assert.deepEqual(board.deals[0].phase_started_on, {});
      assert.deepEqual(detail.phase_started_on, {});
    }));

    const invoiceFields = ['invoiced_on', 'closed_on', 'lane', 'outcome'];
    const readInvoiceDeals = async () => ({
      'deal-board': await TOOLS['deal-board'].handler(c),
      'deal-room-board': await TOOLS['deal-room-board'].handler(c, actor, {workspace: 'team'}),
      'get-deal-room': await TOOLS['get-deal-room'].handler(c, actor, {deal: id(4)}),
      'read-deal-reconciliation': await TOOLS['read-deal-reconciliation'].handler(c, actor, {deal: id(4)}),
    });
    const rowsOf = result => result.deals || [result];
    const snapshotPath = path.join(root, 'mcp-server/test/fixtures/deal-invoice-reads-before.json');
    for (const outcome of [null, 'won']) await t.test(
      outcome ? 'invoice reads preserve closed metadata and keep the deal in history'
        : 'invoice reads return four date and status fields and preserve prior snapshots',
      async () => transaction(async () => {
        await fixture(false);
        await c.query("update deal set next_date='2026-10-03',phase=case when $1::text is null then 'research' else 'closed' end,outcome=$1,closed_on=case when $1::text is null then null else date '2026-10-01' end,lane='territory',won_value=120000 where id=$2", [outcome, id(4)]);
        await c.query("insert into commission(id,deal_id,gross_amount,status,created_by,updated_by) values($1,$2,12000,'invoiced',$3,$3)", [id(7), id(4), actor.id]);
        await c.query('set local role carr_reader');
        const reads = await readInvoiceDeals();
        const baseline = JSON.parse(readFileSync(snapshotPath, 'utf8'))[outcome || 'open'];
        for (const [name, result] of Object.entries(reads)) {
          if (name === 'deal-board' && outcome) {
            assert.deepEqual(result.deals, [], 'closed deals leave the open pipeline');
            continue;
          }
          if (name === 'deal-room-board' && outcome) {
            assert.deepEqual(result.deals, [], 'closed deals leave the working list');
            assert.deepEqual(result.closed_deals.map(row => Object.fromEntries(
              ['id','outcome','closed_on','invoiced_on'].map(field => [field,row[field]]))),
            [{ id:id(4), outcome:'won', closed_on:'2026-10-01', invoiced_on:null }]);
            continue;
          }
          assert.deepEqual(rowsOf(result).map(row => Object.fromEntries(invoiceFields.map(field => [field, row[field]]))), [{
            invoiced_on: null, closed_on: outcome ? '2026-10-01' : null, lane: 'territory', outcome,
          }], name);
          const prior = structuredClone(result);
          if (name === 'deal-room-board') {
            assert.equal(result.deals[0].base_version, reads['read-deal-reconciliation'].base_version,
              'the displayed board values and replacement writes share the Deal version');
            delete prior.closed_deals; delete prior.lost_deals;
          }
          if (name === 'get-deal-room') {
            assert.equal(prior.schema_version,'deal-timeline.v1');
            assert.equal(prior.lease,null);
            assert.equal(prior.client_id,id(3));
            delete prior.client_id;
            delete prior.schema_version;
            delete prior.lease;
            delete prior.checklist;
          }
          rowsOf(prior).forEach(row => {
            for (const field of ['phase_started_on','short_name','items','item_schema_version']) delete row[field];
          });
          const oldRows = rowsOf(baseline[name]);
          rowsOf(prior).forEach(row => { for (const field of ['client_status','client_version','prospect_engagement_status']) delete row[field]; });
          rowsOf(prior).forEach((row, index) => [...invoiceFields, 'vendors', 'next_step_required', 'base_version'].forEach(field => {
            if (!Object.hasOwn(oldRows[index], field)) delete row[field];
          }));
          assert.deepEqual(prior, baseline[name], `${name}: additive response only`);
        }
        await c.query('reset role');
        assert.deepEqual((await c.query('select d.won_value::text, c.gross_amount::text from deal d join commission c on c.deal_id=d.id where d.id=$1', [id(4)])).rows, [{won_value: '120000.00', gross_amount: '12000.00'}], 'client benefit and commission stay separate');
      }));

    await t.test('reconciliation returns a populated invoice date and national lane after board removal', async () => transaction(async () => {
      await fixture(false);
      await c.query("update deal set phase='closed',outcome='won',closed_on='2026-10-01',invoiced_on='2026-10-02',lane='national' where id=$1", [id(4)]);
      await c.query('set local role carr_reader');
      const result = await TOOLS['read-deal-reconciliation'].handler(c, actor, {deal: id(4)});
      assert.deepEqual(Object.fromEntries(invoiceFields.map(field => [field, result[field]])), {
        invoiced_on: '2026-10-02', closed_on: '2026-10-01', lane: 'national', outcome: 'won',
      });
      assert.deepEqual((await TOOLS['deal-board'].handler(c)).deals, []);
      const archived = await TOOLS['deal-room-board'].handler(c, actor, {});
      assert.deepEqual(archived.deals, []);
      assert.equal(archived.closed_deals.length, 1);
      const detail=await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)});
      assert.equal(detail.deal_id,id(4));
      assert.equal(detail.client_id,id(3));
      assert.deepEqual(Object.fromEntries(['id','name','type','closed_on','invoiced_on'].map(key => [key, archived.closed_deals[0][key]])), {
        id:id(4), name:'Synthetic Assignment', type:'renewal', closed_on:'2026-10-01', invoiced_on:'2026-10-02',
      });
    }));

    await t.test('all invoice reads preserve national lane and separate client benefit from commission', async () => transaction(async () => {
      await fixture(false);
      await c.query("update deal set phase='closed',outcome='won',closed_on='2026-10-01',lane='national',won_value=120000 where id=$1", [id(4)]);
      await c.query("insert into commission(id,deal_id,gross_amount,status,created_by,updated_by) values($1,$2,12000,'invoiced',$3,$3)", [id(7), id(4), actor.id]);
      await c.query('set local role carr_reader');
      for (const [name, result] of Object.entries(await readInvoiceDeals())) {
        for (const row of rowsOf(result)) {
          assert.equal(row.lane, 'national', name);
          assert.equal(row.invoiced_on, null, name);
          assert.equal(Object.hasOwn(row, 'won_value'), false, name);
          assert.equal(Object.hasOwn(row, 'gross_amount'), false, name);
          assert.equal(Object.values(row).some(value => [12000, 120000, '12000.00', '120000.00'].includes(value)), false, `${name}: no combined money field`);
        }
      }
      await c.query('reset role');
      assert.deepEqual((await c.query('select d.won_value::text, c.gross_amount::text from deal d join commission c on c.deal_id=d.id where d.id=$1', [id(4)])).rows, [{won_value: '120000.00', gross_amount: '12000.00'}]);
    }));

    for (const linked of [false, true]) await t.test(
      linked ? 'find returns invoice fields in deals reached through the client link'
        : 'find returns invoice fields in name-matched deals',
      async () => transaction(async () => {
        await fixture(false);
        await c.query("update client set roster_ref='C-SYN-1' where id=$1", [id(3)]);
        await c.query("update deal set phase='closed',outcome='won',closed_on='2026-10-01',lane='national' where id=$1", [id(4)]);
        // Other find domains are empty synthetic adapters. Its deal SELECTs
        // still execute on PostgreSQL with the reader's granted views.
        const finder = {query: async (sql, args) => {
          if (sql.includes('from v_deal_board')) return c.query(sql, args);
          if (sql.includes("subject_type in ('lead','client','vendor')")) return {rows: [{
            name: 'Synthetic Contact', ref: 'L-SYN-1', kind: 'lead', merged: false,
          }]};
          if (sql.includes('from v_lead_client_best')) return {rows: [{
            lead_ref: 'L-SYN-1', client_ref: 'C-SYN-1', link_basis: 'conversion',
          }]};
          return {rows: []};
        }};
        for (const invoiced of [null, '2026-10-02']) {
          await c.query('update deal set invoiced_on=$1 where id=$2', [invoiced, id(4)]);
          await c.query('set local role carr_reader');
          const result = await TOOLS.find.handler(finder, actor, {query: linked ? 'Synthetic Contact' : 'Synthetic Assignment'});
          const rows = linked ? result.deals_via_link : result.deals;
          assert.deepEqual(rows.map(row => Object.fromEntries(invoiceFields.map(field => [field, row[field]]))), [{
            invoiced_on: invoiced, closed_on: '2026-10-01', lane: 'national', outcome: 'won',
          }]);
          assert.deepEqual(rows.map(row => Object.fromEntries(Object.entries(row).filter(([field]) => !invoiceFields.includes(field)))), [{
            name: 'Synthetic Assignment', phase: 'closed', owner: null, client_ref: 'C-SYN-1',
          }], 'existing search projection remains unchanged');
          assert.deepEqual(linked ? result.deals : result.deals_via_link, [], 'name/link deduplication remains unchanged');
          await c.query('reset role');
        }
      }));

    await t.test('checklist requires receipt evidence for every installment on an open uninvoiced deal', async () => transaction(async () => {
      await fixture(false);
      for (const source of CHECKLIST_SOURCES) await c.query("insert into doctrine_section(id,status,body_hash) values($1,'active',$2)", [source.section_id, source.content_hash]);
      const readReceipt = async () => {
        await c.query('set local role carr_reader');
        try {
          const detail = await TOOLS['get-deal-room'].handler(c, actor, {deal:id(4)});
          assert.equal(detail.invoiced_on, null);
          return detail.checklist.phases.flatMap(phase => phase.items).find(item => item.id === 'commission_received');
        } finally { await c.query('reset role'); }
      };
      assert.equal((await readReceipt()).status, 'not_recorded', 'no installments prove no receipt');
      await c.query("insert into commission(id,deal_id,gross_amount,status,received_on,created_by) values($1,$2,100,'received','2026-10-19',$3),($4,$2,100,'expected',null,$3)", [id(31),id(4),actor.id,id(32)]);
      assert.equal((await readReceipt()).status, 'not_recorded', 'an expected installment cannot disappear from completion');
      await c.query("update commission set status='invoiced' where id=$1", [id(32)]);
      assert.equal((await readReceipt()).status, 'not_recorded', 'an invoiced installment remains unpaid');
      await c.query("update commission set status='received' where id=$1", [id(32)]);
      assert.equal((await readReceipt()).status, 'not_recorded', 'received status alone is not dated receipt evidence');
      await c.query("update commission set received_on='2026-10-20' where id=$1", [id(32)]);
      const receipt = await readReceipt();
      assert.equal(receipt.status, 'complete');
      assert.deepEqual(receipt.evidence_ref.split(', ').sort(), [`commission:${id(31)}`, `commission:${id(32)}`]);
      assert.equal((await c.query("select has_table_privilege('carr_reader','commission','select') allowed")).rows[0].allowed, false);
    }));

    await t.test('timeline reader returns exact current lease and excludes unverified history', async () => transaction(async () => {
      await fixture(false);
      for (const source of CHECKLIST_SOURCES) await c.query("insert into doctrine_section(id,status,body_hash) values($1,'active',$2)", [source.section_id, source.content_hash]);
      await c.query("insert into lease(id,deal_id,status,executed_on,commencement_on,expiration_on,evidence_kind,evidence_ref,source,created_by) values($1,$2,'current','2026-10-01','2026-11-01','2031-10-31','executed_lease','Synthetic clause 3','Synthetic abstract',$3)", [id(21),id(4),actor.id]);
      await c.query("insert into lease(id,deal_id,status,expiration_on,created_by) values($1,$2,'legacy_unverified','2040-01-01',$3)", [id(22),id(4),actor.id]);
      await c.query('set local role carr_reader');
      assert.equal((await c.query("select has_table_privilege('carr_reader','lease','select') as allowed")).rows[0].allowed,false);
      const read = await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)});
      assert.equal(read.schema_version,'deal-timeline.v1');
      assert.equal(read.checklist.template_state,'current');
      assert.equal(read.checklist.phases.flatMap(p=>p.items).find(i=>i.id==='executed_lease').status,'complete');
      assert.equal(read.lease.id,id(21));
      assert.equal(read.lease.commencement_on,'2026-11-01');
      assert.equal(read.lease.expiration_on,'2031-10-31');
      assert.equal(Object.hasOwn(read.lease,'rent_start_on'),false);
      await c.query('reset role');
      await c.query("update lease set status='superseded' where id=$1",[id(21)]);
      await c.query('set local role carr_reader');
      const superseded = await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)});
      assert.equal(superseded.lease,null);
      assert.equal(superseded.checklist.phases.flatMap(p=>p.items).find(i=>i.id==='executed_lease').status,'not_recorded');
    }));

    await t.test('actual legacy handler works as carr_reader, including an empty board', async () => transaction(async () => {
      assert.equal((await c.query("select has_table_privilege('carr_reader','deal','select') as allowed")).rows[0].allowed, false);
      await c.query('set local role carr_reader');
      assert.deepEqual(await TOOLS['deal-board'].handler(c), { deals: [] });
      await c.query('reset role');
      await fixture(false);
      await c.query("update deal set operating_state='parked',parking_reason='other',parking_note='Synthetic pause',parked_at=now(),parked_by=$1 where id=$2", [actor.id, id(4)]);
      await c.query('set local role carr_reader');
      const row = (await TOOLS['deal-board'].handler(c)).deals[0];
      assert.equal(row.id, id(4));
      assert.equal(row.client_name, 'Synthetic Practice');
      assert.equal(row.phase_sort, 1);
      assert.equal(row.operating_state, 'parked');
      assert.equal(row.parking_note, 'Synthetic pause');
      assert.equal(row.invoiced_on, null);
      await c.query('reset role');
      await c.query('update deal set invoiced_on=current_date where id=$1', [id(4)]);
      await c.query('set local role carr_reader');
      assert.deepEqual(await TOOLS['deal-board'].handler(c), { deals: [] });
    }));

    await t.test('human conflict picker replaces an automatic badge through existing write handlers', async () => transaction(async () => {
      await fixture(false);
      const automatic = await TOOLS['patch-deal-field'].handler(c, { ...actor, via: 'mcp', client_id: 'agent' }, {
        idempotency_key: id(10), deal: id(4), field: 'phase', value: 'negotiation', base_event_id: null,
      });
      assert.equal((await c.query('select automatic from v_deal_room_phase_change where deal_id=$1', [id(4)])).rows[0].automatic, true);
      const conflict = await TOOLS['patch-deal-field'].handler(c, actor, {
        idempotency_key: id(11), deal: id(4), field: 'phase', value: 'legal', base_event_id: null,
      });
      assert.equal(conflict.ok, false);
      assert.equal(conflict.conflict.event_a, automatic.event_id);
      const resolved = await TOOLS['resolve-conflict'].handler(c, actor, { idempotency_key: id(12), conflict_id: conflict.conflict.id, winner: 'b' });
      assert.equal(resolved.ok, true);
      const event = (await c.query('select verb,cause,via,client_id from event where id=$1', [resolved.event_id])).rows[0];
      assert.deepEqual(event, { verb: 'resolve-conflict', cause: 'automation_job', via: 'dealroom-cookie', client_id: 'dealroom-pwa' });
      const evidence = (await c.query('select * from v_deal_room_phase_change where deal_id=$1', [id(4)])).rows[0];
      assert.equal(evidence.event_id, resolved.event_id);
      assert.equal(evidence.phase, 'legal');
      assert.equal(evidence.automatic, false);
    }));

    await t.test('a patch based on the latest field event is not a conflict with itself', async () => transaction(async () => {
      await fixture(false);
      // This file reads timestamptz as text; the Worker's Neon driver returns a
      // millisecond Date, which is the precision this case must survive.
      const worker = { query: async (...args) => {
        const result = await c.query(...args);
        const dated = (result.fields || []).filter(field => field.dataTypeID === 1184).map(field => field.name);
        for (const row of result.rows) for (const name of dated) if (row[name] !== null) row[name] = new Date(row[name]);
        return result;
      } };
      const written = await TOOLS['patch-deal-field'].handler(worker, actor, {
        idempotency_key: id(20), deal: id(4), field: 'phase', value: 'negotiation', base_event_id: null,
      });
      assert.equal(written.ok, true);
      // A base with sub-millisecond digits, fixed rather than left to clock_timestamp().
      const columns = (await c.query(`select column_name from information_schema.columns
        where table_schema='public' and table_name='event' and is_generated='NEVER'
          and coalesce(identity_generation, '') <> 'ALWAYS'
          and column_name not in ('id', 'idempotency_key', 'recorded_at')`)).rows.map(row => `"${row.column_name}"`).join(', ');
      const first = { event_id: (await c.query(`insert into event (id, idempotency_key, recorded_at, ${columns})
          select gen_random_uuid(), $2::text,
            case when date_trunc('milliseconds', recorded_at) + interval '749 microseconds' > recorded_at
              then date_trunc('milliseconds', recorded_at) + interval '749 microseconds'
              else date_trunc('milliseconds', recorded_at) + interval '1749 microseconds' end, ${columns}
          from event where id=$1 returning id`, [written.event_id, id(23)])).rows[0].id };
      assert.equal((await c.query(`select extract(microseconds from recorded_at)::bigint % 1000 as sub_ms
        from event where id=$1`, [first.event_id])).rows[0].sub_ms, '749');
      const second = await TOOLS['patch-deal-field'].handler(worker, actor, {
        idempotency_key: id(21), deal: id(4), field: 'phase', value: 'legal', base_event_id: first.event_id,
      });
      assert.equal(second.ok, true, 'sub-millisecond digits must not make the base newer than itself');
      const stale = await TOOLS['patch-deal-field'].handler(worker, actor, {
        idempotency_key: id(22), deal: id(4), field: 'phase', value: 'research', base_event_id: written.event_id,
      });
      assert.equal(stale.ok, false);
      assert.notEqual(stale.conflict.event_a, written.event_id);
    }));

    await t.test('account counters agree with the open board across outcome and parking states', async () => transaction(async () => {
      await fixture(true);
      for (const outcome of [null, 'won', 'lost', 'paused']) {
        for (const parked of [false, true]) {
          for (const invoiced of [false, true]) {
            await c.query(`update deal set outcome=$1,invoiced_on=case when $2 then current_date else null end,
              operating_state=case when $3 then 'parked' else 'active' end,
              parking_reason=case when $3 then 'other' else null end,
              parked_at=case when $3 then now() else null end,parked_by=case when $3 then $4::uuid else null end where id=$5`, [outcome, invoiced, parked, actor.id, id(4)]);
            await c.query('set local role carr_reader');
            const board = await TOOLS['deal-room-board'].handler(c, actor, { workspace: 'national_account', account_client_id: id(6) });
            const eligible = outcome === null && !invoiced;
            assert.equal(board.deals.length, eligible ? 1 : 0);
            const account = board.accounts.find(row => row.account_client_id === id(6));
            const active = eligible && !parked ? 1 : 0;
            for (const field of ['open_deals', 'attention_deals', 'overdue_deals', 'stale_deals']) {
              assert.equal(Number(account[field]), active, `${field}: ${outcome}/${parked}/${invoiced}`);
            }
            assert.equal(Number(account.parked_deals), eligible && parked ? 1 : 0);
            await c.query('reset role');
          }
        }
      }
    }));

    await t.test('Command Center counts the same active team population as its destination board', async () => transaction(async () => {
      await fixture(false);
      for (const outcome of [null, 'won', 'lost', 'paused']) {
        await c.query('update deal set outcome=$1 where id=$2', [outcome, id(4)]);
        await c.query('set local role carr_reader');
        const client = { query: (sql, params) => sql.includes('from ops.work_request')
          ? { rows: [{ needs_viewer: 0, doc_at_work: 0, changed_count: 0, legacy_unscoped_held: 0, legacy_unscoped_recent: 0 }] }
          : c.query(sql, params) };
        const summary = await readCommandCenterSummary({ client, actor, correlationId: 'synthetic' });
        const board = await TOOLS['deal-room-board'].handler(c, actor, { workspace: 'team' });
        assert.equal(summary.metrics[0].active_deals, board.deals.filter(row => row.operating_state === 'active').length);
        assert.equal(summary.metrics[0].flagged_deals, board.deals.filter(row => row.operating_state === 'active' && row.attention).length);
        await c.query('reset role');
      }
    }));

    await t.test('canonical SQL proof rejects missing evidence and wrong undo identity/value', async () => {
      const definition = (await c.query("select pg_get_viewdef('v_deal_room_phase_change'::regclass,true) as sql")).rows[0].sql.replace(/;\s*$/, '');
      const runProof = () => execFileSync(bin ? path.join(bin, 'psql') : 'psql', ['-X', '-h', c.connectionParameters.host, '-p', String(c.connectionParameters.port), '-U', c.connectionParameters.user, '-d', c.connectionParameters.database, '-v', 'ON_ERROR_STOP=1', '-f', path.join(root, 'mcp-server/test/local-deals-postgres.sql')], { stdio: 'pipe', env: { ...process.env, PGPASSWORD: c.connectionParameters.password || '' } });
      runProof();
      const columns = ['deal_id', 'event_id', 'prior_phase', 'phase', 'automatic', 'reason', 'evidence_date', 'recorded_at'];
      const mutations = [
        { select: '*', where: 'false', error: /Phase identity, evidence date or reason lost/ },
        ...[['event_id', 'uuid'], ['prior_phase', 'text'], ['phase', 'text'], ['automatic', 'boolean'], ['reason', 'text'], ['evidence_date', 'text'], ['recorded_at', 'text']]
          .map(([field, type]) => ({ select: columns.map(column => column === field ? `null::${type} as ${column}` : column).join(','), error: /Phase identity, evidence date or reason lost/ })),
        { select: '*', where: "recorded_at::date <> '2026-10-05'::date", error: /Manual correction retained automatic badge/ },
        { select: '*', where: "recorded_at::date <> '2026-10-07'::date", error: /Undo identity, value or classification lost/ },
        ...[['event_id', `'${id(99)}'::uuid`], ['phase', "'research'::text"], ['automatic', 'true']]
          .map(([field, value]) => ({
            select: columns.map(column => column === field ? `case when recorded_at::date='2026-10-07'::date then ${value} else ${column} end as ${column}` : column).join(','),
            error: /Undo identity, value or classification lost/,
          })),
      ];
      try {
        for (const mutation of mutations) {
          await c.query(`create or replace view v_deal_room_phase_change as select ${mutation.select} from (${definition}) evidence where ${mutation.where || 'true'}`);
          assert.throws(runProof, mutation.error);
        }
      } finally {
        await c.query(`create or replace view v_deal_room_phase_change as ${definition}`);
      }
    });
  } finally {
    try {
      if (c) await c.end();
      if (admin) {
        try { if (database) await admin.query(`drop database "${database}"`); }
        finally { await admin.end(); }
      }
    } finally {
      try { await postgresFixture?.close(); } finally { await releaseBudget(); }
    }
  }
});
