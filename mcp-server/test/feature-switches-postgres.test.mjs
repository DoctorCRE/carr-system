import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { TOOLS } from '../src/tools.js';
import { requireFeature } from '../src/feature-switches.js';

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

test('Feature switch record lifecycle on disposable PostgreSQL', { skip: !bin && !process.env.CARR_CI_DATABASE_URL && 'PostgreSQL unavailable' }, async t => {
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
      const name = `feature_switches_${randomUUID().replaceAll('-', '')}`;
      await admin.query(`create database "${name}" template template0`);
      database = name;
      url.pathname = `/${database}`;
      connection = { connectionString: url.href };
    } else {
      postgresFixture = await acquireDisposablePostgres({ prefix: 'feature-switches-', pgCtl: path.join(bin, 'pg_ctl'), dataName: '.' });
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
      assert.match(isolated, /^feature_switches_/);
      assert.notEqual(isolated, new URL(process.env.CARR_CI_DATABASE_URL).pathname.slice(1));
      assert.equal((await c.query("select to_regclass('public.deal') existing")).rows[0].existing, null);
    }
    // Roles belong to the cluster; an isolated database does not provide them.
    // Preserve existing shared-cluster roles and their attributes.
    await c.query(`do $$ begin
      begin create role carr_reader; exception when duplicate_object then null; end;
      begin create role carr_writer; exception when duplicate_object then null; end;
    end $$;`);
    for (const name of ['actor','event','tool_call','loop_item','loop_block','loop_domain']) {
      await c.query(schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))[0]);
    }
    await restoreEventIdentity(c, schema);
    await c.query('alter table tool_call add primary key(idempotency_key)');
    // This fixture tests registration with the existing guard, whose behavior
    // is covered by the canonical reference-monitor database acceptance gate.
    await c.query(`create schema ops;
      create function ops.scac_reference_monitor_guard() returns trigger
      language plpgsql as $$ begin return new; end $$;`);
    await c.query(readFileSync(path.join(root,'migrations/0902_feature_switches.sql'),'utf8'));
    const guards = (await c.query(`select tgname,tgtype,tgenabled from pg_trigger
      where tgrelid='public.feature_switch'::regclass
      and tgfoid='ops.scac_reference_monitor_guard()'::regprocedure order by tgname`)).rows;
    assert.deepEqual(guards.map(row=>[row.tgname,row.tgtype,row.tgenabled]),[
      ['scac_reference_monitor_guard_row',31,'O'],
      ['scac_reference_monitor_guard_truncate',34,'O'],
    ],'switch writes must register row and truncate guards with the existing reference monitor');
    await c.query('grant usage on schema ops to carr_writer');
    for (const privilege of ['insert','update','delete','truncate']) {
      assert.equal((await c.query(`select has_table_privilege('carr_writer','public.feature_switch',$1) allowed`,[privilege])).rows[0].allowed,
        false,`carr_writer must hold no table-level ${privilege} on feature_switch`);
    }
    const writer = (await c.query(`select p.prosecdef,p.proconfig,pg_get_userbyid(p.proowner) owner,
      has_function_privilege('carr_writer',p.oid,'execute') runtime,has_function_privilege('public',p.oid,'execute') everyone
      from pg_proc p where p.pronamespace='ops'::regnamespace and p.proname='write_feature_switch'`)).rows;
    assert.equal(writer.length,1,'switch writes must go through one registered ops.write_feature_switch function');
    assert.equal(writer[0].prosecdef,true);
    assert.equal(writer[0].runtime,true);
    assert.equal(writer[0].everyone,false);
    assert.deepEqual(writer[0].proconfig,['search_path=pg_catalog, ops, pg_temp'],'search_path must pin trusted schemas only, never public');
    await c.query('grant select,insert on event,tool_call to carr_writer');
    await c.query('grant select on actor to carr_writer');
    await c.query("insert into actor(id,slug,display_name,kind) values($1,'joe','Synthetic Joe','human')",[actor.id]);
    const invoke = async (verb,args,as=actor) => {
      await c.query('begin');
      try {
        await c.query('set local role carr_writer');
        const result = await TOOLS[verb].handler(c,as,args);
        await c.query('commit'); return result;
      } catch (error) { await c.query('rollback'); throw error; }
    };
    await c.query("grant select,insert,update on loop_item,loop_block,loop_domain to carr_writer");
    await c.query("insert into loop_domain(slug,label,sort) values('system','System',1)");
    await c.query("insert into loop_block(id,kind,block_key,rel_path,seq,col_order,renders_closed,created_by,updated_by) values(gen_random_uuid(),'open_loop','backlog','synthetic',1,array[]::text[],false,$1,$1)",[actor.id]);
    const input = { idempotency_key:randomUUID(), name:'synthetic-feature', description:'Show a synthetic test control',
      base_version:0, default_enabled:false, audience:'joe', owner:'claude', expected_removal_on:'2099-01-01' };
    await t.test('invalid switch input rolls back without records or audit writes', async () => {
      const counts = {};
      for (const table of ['feature_switch','event','tool_call']) {
        counts[table] = (await c.query(`select count(*)::integer n from ${table}`)).rows[0].n;
      }
      const invalid = [
        {name:'Invalid Name'}, {audience:'public'}, {owner:'unregistered'},
        {expected_removal_on:'2099-02-29'}, {description:'   '}, {description:'x'.repeat(1001)},
        {default_enabled:'false'}, {retired:'false'}, {base_version:-1}, {base_version:0.5},
      ];
      for (const patch of invalid) {
        await assert.rejects(invoke('set-feature-switch',{...input,...patch,idempotency_key:randomUUID()}),
          error => error.payload.error === 'feature_switch_input_invalid', JSON.stringify(patch));
      }
      for (const patch of [{name:'Invalid Name'}, {audience:'public'}, {enabled:'true'}, {base_version:-1}]) {
        await assert.rejects(invoke('flip-feature-switch',{...input,enabled:true,...patch,idempotency_key:randomUUID()}),
          error => error.payload.error === 'feature_switch_input_invalid', JSON.stringify(patch));
      }
      await assert.rejects(invoke('set-feature-switch',{...input,retired:true,idempotency_key:randomUUID()}),
        error => error.payload.error === 'feature_switch_cannot_create_retired');
      await assert.rejects(invoke('flip-feature-switch',{name:input.name,base_version:0,enabled:true,idempotency_key:randomUUID()}),
        error => error.payload.error === 'feature_switch_version_conflict' && error.payload.current_version === 0);
      for (const table of ['feature_switch','event','tool_call']) {
        assert.equal((await c.query(`select count(*)::integer n from ${table}`)).rows[0].n,counts[table],table);
      }
    });
    const created = await invoke('set-feature-switch',input);
    assert.equal(created.switch.version,1);
    assert.ok(created.switch.created_at);
    const flip = { idempotency_key:randomUUID(), name:input.name, base_version:1, enabled:true, audience:'team' };
    const changed = await invoke('flip-feature-switch',flip);
    assert.equal(changed.switch.version,2);
    assert.equal((await invoke('flip-feature-switch',flip)).replayed,true);
    await assert.rejects(invoke('flip-feature-switch',{ ...flip,idempotency_key:randomUUID() }), /feature_switch_version_conflict/);
    const read = await invoke('list-feature-switches',{name:input.name}, { ...actor,slug:'dell' });
    assert.equal(read.schema,'feature-switches.v1');
    assert.equal(read.switches[0].available,true);
    assert.equal(read.history.length,2);
    const events = (await c.query('select mutation_order from event order by mutation_order')).rows;
    assert.equal(events.length, 2);
    assert.ok(BigInt(events[0].mutation_order) > 0n);
    assert.ok(BigInt(events[1].mutation_order) > BigInt(events[0].mutation_order));
    assert.equal(read.history[0].actor,'joe');
    assert.equal(read.history[0].before.enabled,null);
    assert.equal(read.history[0].after.enabled,true);
    assert.ok(read.history[0].recorded_at);
    await requireFeature(c,{...actor,slug:'dell'},input.name);
    await invoke('flip-feature-switch',{...flip, idempotency_key:randomUUID(),base_version:2,enabled:false});
    await assert.rejects(requireFeature(c,{...actor,slug:'dell'},input.name), /feature_disabled/);
    await invoke('set-feature-switch',{...input,idempotency_key:randomUUID(),base_version:3,expected_removal_on:'2000-01-01'});
    const health=await invoke('check-feature-switches',{idempotency_key:randomUUID()});
    assert.equal(health.overdue.length,1);
    assert.equal(health.overdue[0].owner,'claude');
    assert.match(health.overdue[0].line,/on breach:.*owner claude.*retire.*verify.*auto-clear/);
    const again=await invoke('check-feature-switches',{idempotency_key:randomUUID()});
    assert.equal(again.overdue[0].loop_id,health.overdue[0].loop_id);
    const retired = await invoke('set-feature-switch',{ ...input,idempotency_key:randomUUID(),base_version:4,retired:true });
    assert.ok(retired.switch.retired_at);
    const cleared=await invoke('check-feature-switches',{idempotency_key:randomUUID()});
    assert.equal(cleared.overdue.length,0);
    assert.deepEqual(cleared.cleared,[health.overdue[0].loop_id]);
    assert.equal((await invoke('list-feature-switches',{name:input.name})).switches[0].available,false);
    await t.test('retired switches refuse flips and may be explicitly restored', async () => {
      await assert.rejects(invoke('flip-feature-switch',{...flip,base_version:5,idempotency_key:randomUUID()}),
        error => error.payload.error === 'feature_switch_retired');
      assert.equal((await invoke('list-feature-switches',{name:input.name})).switches[0].version,5);
      const restored=await invoke('set-feature-switch',{...input,base_version:5,retired:false,idempotency_key:randomUUID()});
      assert.equal(restored.switch.retired_at,null);
      assert.equal(restored.switch.version,6);
      const turnedOn=await invoke('flip-feature-switch',{...flip,base_version:6,idempotency_key:randomUUID()});
      assert.equal(turnedOn.switch.enabled,true);
      assert.equal((await invoke('list-feature-switches',{name:input.name})).switches[0].available,true);
    });
  } finally {
    if(c) await c.end();
    if(admin) await admin.end();
    try { await postgresFixture?.close(); }
    finally { await releaseBudget(); }
  }
});
