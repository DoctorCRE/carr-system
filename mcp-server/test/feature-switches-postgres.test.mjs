// Test feature switch reads and scheduled health on PostgreSQL.
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
import { organizationTenantForActor } from '../src/identity.js';

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

test('Feature switch reads and scheduled health on disposable PostgreSQL', { skip: !bin && !process.env.CARR_CI_DATABASE_URL && 'PostgreSQL unavailable' }, async t => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let postgresFixture, dir;
  let admin;
  let beforeCluster;
  const clusterState = async () => ({
    roles: (await admin.query("select rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,rolconfig from pg_roles order by rolname")).rows,
    databases: (await admin.query("select datname from pg_database order by datname")).rows,
    memberships: (await admin.query("select roleid,member,grantor,admin_option,inherit_option,set_option from pg_auth_members order by roleid,member,grantor")).rows,
  });
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
      beforeCluster = await clusterState();
      postgresFixture = await acquireDisposablePostgres({ prefix: 'feature-switches-', service: true });
      connection = postgresFixture.connection;
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
    const seeded = (await c.query('select * from ops.write_feature_switch($1,$2::jsonb)',
      ['create',JSON.stringify({tenant:organizationTenantForActor(actor),name:input.name,description:input.description,
        default_enabled:true,audience:'team',owner:'claude',expected_removal_on:'2000-01-01'})])).rows[0];
    const read = await invoke('list-feature-switches',{name:input.name}, { ...actor,slug:'dell' });
    assert.equal(read.schema,'feature-switches.v1');
    assert.equal(read.switches[0].available,true);
    await requireFeature(c,{...actor,slug:'dell'},input.name);
    const health=await invoke('check-feature-switches',{idempotency_key:randomUUID()});
    assert.equal(health.overdue.length,1);
    assert.equal(health.overdue[0].owner,'claude');
    assert.match(health.overdue[0].line,/on breach:.*owner claude.*retire.*verify.*auto-clear/);
    const again=await invoke('check-feature-switches',{idempotency_key:randomUUID()});
    assert.equal(again.overdue[0].loop_id,health.overdue[0].loop_id);
    const current=(await c.query('select version from feature_switch where id=$1',[seeded.id])).rows[0];
    await c.query('select * from ops.write_feature_switch($1,$2::jsonb)',
      ['revise',JSON.stringify({id:seeded.id,base_version:Number(current.version),
        description:input.description,default_enabled:true,audience:'team',owner:'claude',
        expected_removal_on:'2000-01-01',retired:true})]);
    const cleared=await invoke('check-feature-switches',{idempotency_key:randomUUID()});
    assert.equal(cleared.overdue.length,0);
    assert.deepEqual(cleared.cleared,[health.overdue[0].loop_id]);
    assert.equal((await invoke('list-feature-switches',{name:input.name})).switches[0].available,false);
    await assert.rejects(requireFeature(c,{...actor,slug:'dell'},input.name), /feature_disabled/);
  } finally {
    const errors = [];
    for (const cleanup of [() => c?.end(), () => postgresFixture?.close(), async () => {
      if (beforeCluster) assert.deepEqual(await clusterState(), beforeCluster, 'CI-service roles, attributes, memberships and database lists are unchanged after the suite');
    }, () => admin?.end(), releaseBudget]) {
      try { await cleanup(); } catch (error) { errors.push(error); }
    }
    if (errors.length) throw new AggregateError(errors, 'feature-switches fixture cleanup failed');
  }
});
