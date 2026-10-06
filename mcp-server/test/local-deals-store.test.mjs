import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';
import { acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, existsSync } from 'node:fs';
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
  const dir = ciDsn ? null : mkdtempSync('/tmp/local-deals-');
  let admin;
  let database;
  let running = false;
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
      execFileSync(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale'], { stdio: 'pipe' });
      execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h ''`, '-w', 'start'], { stdio: 'pipe' });
      running = true;
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
    for (const name of ['actor', 'party', 'client', 'deal', 'deal_phase', 'deal_participant', 'next_action', 'deal_note', 'national_account_owner', 'deal_market_assignment', 'deal_review_item', 'deal_review_session', 'event', 'tool_call', 'deal_conflict', 'critical_date', 'lease', 'activity', 'premises', 'negotiation_round', 'document', 'commission', 'capture_post_call_action', 'building', 'space', 'premises_space']) {
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
          if (name === 'get-deal-room') {
            assert.equal(prior.schema_version,'deal-timeline.v1');
            assert.equal(prior.lease,null);
            delete prior.schema_version;
            delete prior.lease;
          }
          const oldRows = rowsOf(baseline[name]);
          rowsOf(prior).forEach((row, index) => invoiceFields.forEach(field => {
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
      assert.deepEqual((await TOOLS['deal-room-board'].handler(c, actor, {})).deals, []);
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
      if (running) execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-m', 'immediate', '-w', 'stop'], { stdio: 'pipe' });
    } finally {
      await releaseBudget();
    }
  }
});
