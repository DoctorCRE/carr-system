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
import { TOOLS } from '../src/tools.js';
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
    for (const name of ['actor', 'party', 'client', 'deal', 'deal_phase', 'deal_participant', 'next_action', 'deal_note', 'national_account_owner', 'deal_market_assignment', 'deal_review_item', 'deal_review_session', 'event', 'tool_call', 'deal_conflict', 'critical_date', 'lease', 'activity', 'premises', 'negotiation_round', 'document', 'commission', 'capture_post_call_action', 'capture_post_call_candidate', 'building', 'space', 'premises_space']) {
      const table = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))?.[0];
      assert.ok(table, name);
      await c.query(table);
    }
    await restoreEventIdentity(c, schema);
    await c.query('alter table tool_call add primary key(idempotency_key);');
    await c.query("create view v_last_touch as select null::text subject_type, null::uuid subject_id, null::date last_touch where false;");
    for (const name of ['v_client_account', 'v_deal_board', 'v_deal_room_board', 'v_deal_room_account', 'v_deal_room_event', 'v_deal_room_session', 'v_deal_reconciliation_read', 'v_deal_room_note', 'v_deal_room_critical_date', 'v_deal_room_action', 'v_deal_room_activity', 'v_deal_room_participant', 'v_deal_room_premises', 'v_deal_room_negotiation', 'v_deal_room_document', 'v_deal_room_phase_change', 'v_deal_room_current_lease']) {
      const view = schema.match(new RegExp(`CREATE VIEW public\\.${name} AS[\\s\\S]*?;`))?.[0];
      assert.ok(view, name);
      await c.query(view);
      await c.query(`grant select on ${name} to carr_reader,carr_writer`);
    }
    await c.query(readFileSync(path.join(root, 'migrations/0917_deal_status_next_step.sql'), 'utf8'));
    await c.query("create view v_ref_index as select 'deal'::text subject_type, id subject_id from deal;");
    await c.query("grant select on v_ref_index to carr_reader");
    await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'joe','Synthetic Partner','human',true)", [actor.id]);
    for (const [slug, sort] of [['research', 1], ['negotiation', 2], ['legal', 3], ['closed', 4]]) {
      await c.query('insert into deal_phase(slug,label,sort) values($1,$1,$2)', [slug, sort]);
    }
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

    const accept = async (deal, owner, due) => {
      const candidate = randomUUID();
      await c.query(`insert into capture_post_call_candidate
        (id,session_id,idempotency_key,batch_hash,item_index,kind,deal_id,assignee_slug,action_description,due_on,evidence_quote,confidence)
        values ($1,$2,$3,$4,0,'assigned_action',$5,$6,'Review call terms',$7,'Review call terms',1)`,
        [candidate, randomUUID(), randomUUID(), 'a'.repeat(64), deal, owner, due]);
      return (await TOOLS['resolve-post-call-candidate'].handler(c, actor, {
        idempotency_key: randomUUID(), candidate_id: candidate, accept: true,
      })).ref;
    };

    await t.test('partner status update supersedes an overdue step and flags the next-step gap', async () => transaction(async () => {
      await fixture(true);
      const yesterday = (await c.query("select (current_date-1)::text as day")).rows[0].day;
      const prior = await TOOLS['set-next-step'].handler(c, actor, {
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

    await t.test('set-next-action keeps the current step and its date together', async () => transaction(async () => {
      await fixture(false);
      const dates = (await c.query("select (current_date-1)::text as past, (current_date+7)::text as future")).rows[0];
      await TOOLS['set-next-step'].handler(c, actor, {
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
          idempotency_key: randomUUID(), deal: id(4), text: 'Request terms', next_date: dates.past,
        });
        const expired = await accept(id(4), 'joe', dates.past);
        const preserved = [await accept(id(4), 'joe', dates.today), await accept(id(4), 'joe', dates.future),
          await accept(id(4), 'dell', dates.past)];
        const otherDeal = await accept(id(9), 'joe', dates.past);
        const args = {idempotency_key: randomUUID(), deal: id(4)};
        if (writer === 'add-deal-note') Object.assign(args, {text: 'Terms received', next_step: 'Review revised terms', next_date: dates.future});
        if (writer === 'set-next-step') Object.assign(args, {text: 'Review revised terms', next_date: dates.future});
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
        idempotency_key: randomUUID(), deal: id(4), text: 'Review proposed terms', next_date: dates.future,
      });
      await TOOLS['set-next-step'].handler(c, actor, {
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

    await t.test('national-account Lost history uses client ID when client_ref is null', async () => transaction(async () => {
      await fixture(true);
      await c.query("update deal set phase='closed',outcome='lost' where id=$1", [id(4)]);
      await c.query('set local role carr_reader');
      const team = await TOOLS['deal-room-board'].handler(c, actor, {workspace:'team'});
      const account = await TOOLS['deal-room-board'].handler(c, actor, {workspace:'national_account',account_client_id:id(6)});
      assert.deepEqual(team.lost_deals, [], 'account history never leaks into team history');
      assert.deepEqual(account.lost_deals.map(d => [d.id,d.client_ref,d.workspace_kind,d.account_client_id]), [[id(4),null,'national_account',id(6)]]);
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
      outcome ? 'invoice reads return null invoiced_on on a closed deal and preserve prior snapshots'
        : 'invoice reads return four date and status fields and preserve prior snapshots',
      async () => transaction(async () => {
        await fixture(false);
        await c.query("update deal set next_date='2026-10-03',phase=case when $1::text is null then 'research' else 'closed' end,outcome=$1,closed_on=case when $1::text is null then null else date '2026-10-01' end,lane='territory',won_value=120000 where id=$2", [outcome, id(4)]);
        await c.query("insert into commission(id,deal_id,gross_amount,status,created_by,updated_by) values($1,$2,12000,'invoiced',$3,$3)", [id(7), id(4), actor.id]);
        await c.query('set local role carr_reader');
        const reads = await readInvoiceDeals();
        const baseline = JSON.parse(readFileSync(snapshotPath, 'utf8'))[outcome || 'open'];
        for (const [name, result] of Object.entries(reads)) {
          assert.deepEqual(rowsOf(result).map(row => Object.fromEntries(invoiceFields.map(field => [field, row[field]]))), [{
            invoiced_on: null, closed_on: outcome ? '2026-10-01' : null, lane: 'territory', outcome,
          }], name);
          const prior = structuredClone(result);
          if (name === 'deal-room-board') { delete prior.closed_deals; delete prior.lost_deals; }
          if (name === 'get-deal-room') {
            assert.equal(prior.schema_version,'deal-timeline.v1');
            assert.equal(prior.lease,null);
            assert.equal(prior.client_id,id(3));
            delete prior.client_id;
            delete prior.schema_version;
            delete prior.lease;
          }
          const oldRows = rowsOf(baseline[name]);
          rowsOf(prior).forEach((row, index) => [...invoiceFields, 'next_step_required'].forEach(field => {
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

    await t.test('timeline reader returns exact current lease and excludes unverified history', async () => transaction(async () => {
      await fixture(false);
      await c.query("insert into lease(id,deal_id,status,executed_on,commencement_on,expiration_on,evidence_kind,evidence_ref,source,created_by) values($1,$2,'current','2026-10-01','2026-11-01','2031-10-31','executed_lease','Synthetic clause 3','Synthetic abstract',$3)", [id(21),id(4),actor.id]);
      await c.query("insert into lease(id,deal_id,status,expiration_on,created_by) values($1,$2,'legacy_unverified','2040-01-01',$3)", [id(22),id(4),actor.id]);
      await c.query('set local role carr_reader');
      assert.equal((await c.query("select has_table_privilege('carr_reader','lease','select') as allowed")).rows[0].allowed,false);
      const read = await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)});
      assert.equal(read.schema_version,'deal-timeline.v1');
      assert.equal(read.lease.id,id(21));
      assert.equal(read.lease.commencement_on,'2026-11-01');
      assert.equal(read.lease.expiration_on,'2031-10-31');
      assert.equal(Object.hasOwn(read.lease,'rent_start_on'),false);
      await c.query('reset role');
      await c.query("update lease set status='superseded' where id=$1",[id(21)]);
      await c.query('set local role carr_reader');
      assert.equal((await TOOLS['get-deal-room'].handler(c,actor,{deal:id(4)})).lease,null);
    }));

    await t.test('actual legacy handler works as carr_reader, including an empty board', async () => transaction(async () => {
      assert.equal((await c.query("select has_table_privilege('carr_reader','deal','select') as allowed")).rows[0].allowed, false);
      await c.query('set local role carr_reader');
      assert.deepEqual(await TOOLS['deal-board'].handler(c), { deals: [] });
      await c.query('reset role');
      await fixture(false);
      await c.query("update deal set outcome='won',phase='closed',operating_state='parked',parking_reason='other',parking_note='Synthetic pause',parked_at=now(),parked_by=$1 where id=$2", [actor.id, id(4)]);
      await c.query('set local role carr_reader');
      const row = (await TOOLS['deal-board'].handler(c)).deals[0];
      assert.equal(row.id, id(4));
      assert.equal(row.client_name, 'Synthetic Practice');
      assert.equal(row.phase_sort, 4);
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

    await t.test('account counters agree with the invoice-eligible board across outcome and parking states', async () => transaction(async () => {
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
            assert.equal(board.deals.length, invoiced ? 0 : 1);
            const account = board.accounts.find(row => row.account_client_id === id(6));
            const active = !invoiced && !parked ? 1 : 0;
            for (const field of ['open_deals', 'attention_deals', 'overdue_deals', 'stale_deals']) {
              assert.equal(Number(account[field]), active, `${field}: ${outcome}/${parked}/${invoiced}`);
            }
            assert.equal(Number(account.parked_deals), !invoiced && parked ? 1 : 0);
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
