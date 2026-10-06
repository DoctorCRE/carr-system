import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';
import { acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, mkdirSync, renameSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';

const root = fileURLToPath(new URL('../../', import.meta.url));
let bin;
try { bin = execFileSync('pg_config', ['--bindir'], { encoding: 'utf8' }).trim(); }
catch { bin = null; }
if (!bin || !existsSync(path.join(bin,'postgres'))) {
  for (const pgConfig of ['/opt/homebrew/opt/postgresql@17/bin/pg_config','/usr/lib/postgresql/17/bin/pg_config']) {
    if (existsSync(pgConfig)) {
      bin = execFileSync(pgConfig,['--bindir'],{ encoding:'utf8' }).trim();
      break;
    }
  }
}
if (!bin || !existsSync(path.join(bin,'postgres'))) bin = null;
const uuid = n => `aa000000-0000-4000-8000-${String(n).padStart(12,'0')}`;

test('SQL catchup store binds identity, time, coverage and late commits', { skip: !bin && 'local PostgreSQL binaries unavailable' }, async () => {
  const migration = readFileSync(path.join(root, 'migrations/0765_doc_whats_new.sql'), 'utf8');
  const dir = mkdtempSync('/tmp/doc-catchup-');
  let running = false;
  const clients = [];
  const releaseBudget = await acquirePostgresFixtureGroup();
  try {
    execFileSync(path.join(bin,'initdb'), ['-D',dir,'-U','fixture','--auth=trust','--no-locale'], { stdio: 'pipe' });
    // Hosted Postgres uses UTC; the catchup date contract uses America/Chicago.
    execFileSync(path.join(bin,'pg_ctl'), ['-D',dir,'-l',path.join(dir,'server.log'),'-o',`-k ${dir} -h '' -c timezone=UTC`,'-w','start'], { stdio: 'pipe' });
    running = true;
    const connect = async () => {
      const c = new pg.Client({ host: dir, user: 'fixture', database: 'postgres' });
      await c.connect(); clients.push(c);
      // CURRENT_DATE fixtures use the same business day as the feature, even
      // when the PostgreSQL cluster and CI host default to UTC.
      await c.query("set time zone 'America/Chicago'");
      return c;
    };
    const c = await connect();
    await c.query('create schema ops; create role carr_writer; create role carr_authority; create role carr_reader; grant usage on schema ops to carr_writer,carr_authority,carr_reader;');
    const schema = readFileSync(path.join(root,'db/schema.sql'),'utf8');
    const authorityFunction = schema.match(/CREATE FUNCTION ops.authority_login_slug\([^\n]+\)[\s\S]*?\n\$\$;/)?.[0];
    assert.ok(authorityFunction);
    await c.query(authorityFunction);
    for (const name of ['public.actor','public.party','public.client','public.lead','public.vendor','public.deal','public.deal_participant','public.event','public.activity','public.next_action','public.critical_date','public.tool_call','ops.release','ops.release_slice_member','ops.doc_conversation','ops.doc_conversation_grant','ops.doc_conversation_turn','ops.doc_suggestion','ops.doc_suggestion_scan']) {
      const table = schema.match(new RegExp(`CREATE TABLE ${name.replaceAll('.','\\.')} \\([\\s\\S]*?\\n\\);`))?.[0];
      assert.ok(table, name);
      await c.query(table);
    }
    await restoreEventIdentity(c, schema);
    await c.query('alter table public.actor add primary key(id);');
    const actorFunction = schema.match(/CREATE FUNCTION ops.portfolio_writer_actor_id\(\)[\s\S]*?\n\$\$;/)?.[0];
    assert.ok(actorFunction);
    await c.query(actorFunction);
    const releaseFunction = schema.match(/CREATE FUNCTION ops.list_shipped_releases\([^\n]+\)[\s\S]*?\n\$\$;/)?.[0];
    assert.ok(releaseFunction);
    await c.query(releaseFunction);
    await c.query('grant execute on function ops.list_shipped_releases(timestamptz) to carr_writer; grant select,insert on public.tool_call to carr_writer; alter table public.tool_call add primary key(idempotency_key);');
    const touchFunction = schema.match(/CREATE FUNCTION public.trg_touch_row\(\)[\s\S]*?end \$\$;/)?.[0];
    assert.ok(touchFunction);
    await c.query(touchFunction);
    await c.query('create trigger next_action_touch before update on public.next_action for each row execute function public.trg_touch_row();');
    await c.query("create view public.v_ref_index as select 'client'::text subject_type,id subject_id from public.client; grant select on public.v_ref_index to carr_writer; grant select,update on public.next_action to carr_writer; grant insert on public.event to carr_writer;");
    await c.query('create function ops.doc_suggestion_visible(uuid,uuid) returns boolean language sql as $$ select exists(select 1 from ops.doc_conversation c where c.id=$1 and c.created_by_actor=$2) $$;');
    await c.query(migration);
    await c.query(readFileSync(path.join(root,'migrations/0766_doc_whats_new_repair.sql'),'utf8'));
    await c.query("insert into actor(id,slug,display_name,kind,active) values ($1,'joe','Partner A','human',true),($2,'dell','Partner B','human',true)",[uuid(1),uuid(2)]);
    const identity = async slug => {
      await c.query('reset role');
      await c.query("select set_config('carr.acting_actor_slug',$1,false),set_config('carr.verified_human_actor_slug',$1,false),set_config('carr.sponsoring_human_slug',$1,false)",[slug]);
      await c.query('set role carr_writer');
    };
    const context = async () => (await c.query('select ops.whats_new_context(false) as r')).rows[0].r;
    const section = async (name, ctx) => (await c.query('select ops.whats_new_section($1,$2,$3,$4) as r',[name,ctx.since,ctx.high_water,ctx.previous_snapshot])).rows[0].r;
    await identity('joe');
    const first = await context();
    assert.equal(Date.parse(first.high_water)-Date.parse(first.since),86400000);
    assert.equal(first.first_call,true);
    for (const name of ['deal_changes','lead_changes','next_actions','critical_dates','partner_activity','new_leads','doc_suggestions','shipped_releases']) assert.equal((await section(name,first)).state,'empty',name);
    await c.query('select ops.mark_whats_new_seen($1,$2)',[first.high_water,first.snapshot]);
    assert.equal((await context()).since,first.high_water);
    const actor = { id:uuid(1),slug:'joe',human:true,via:'oauth-google' };
    const invoke = async args => {
      await c.query('begin');
      try { const r=await executeRegisteredTool(c,actor,'whats-new',args); await c.query('commit'); return r; }
      catch(e) { await c.query('rollback'); throw e; }
    };
    const ack = { mark_seen:true,idempotency_key:uuid(50) };
    const lost = await invoke(ack);
    assert.equal(lost.marked_seen,true);
    const retry = await invoke(ack);
    assert.deepEqual(retry,{ replayed:true,...lost });
    assert.equal((await context()).since,lost.high_water);
    await identity('dell');
    assert.equal((await context()).first_call,true);
    await assert.rejects(c.query('select * from ops.doc_whats_new_watermark'), /permission denied/);
    await c.query('reset role');
    // Fixtures use the snapshot's actual table definitions, never production data.
    await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Practice',$2,$2)",[uuid(3),uuid(1)]);
    await c.query("insert into client(id,party_id,status,created_by,updated_by) values($1,$2,'active',$3,$3)",[uuid(4),uuid(3),uuid(1)]);
    await c.query("insert into deal(id,client_id,name,deal_type,phase,created_by,updated_by) values($1,$2,'Synthetic Deal','lease','search',$3,$3)",[uuid(5),uuid(4),uuid(1)]);
    await c.query("insert into lead(id,registry_ref,party_id,stage,created_by,updated_by,owner_id) values($1,'L-SYNTHETIC',$2,'new',$3,$3,$3)",[uuid(6),uuid(3),uuid(1)]);
    await c.query("insert into event(id,occurred_at,actor_id,verb,subject_type,subject_id,field,new_value,cause) values($1,now(),$2,'update-deal','deal',$3,'phase','\"review\"','human_stated'),($4,now(),$2,'update-lead','lead',$5,'stage','\"contacted\"','human_stated')",[uuid(7),uuid(2),uuid(5),uuid(8),uuid(6)]);
    await c.query("insert into activity(id,occurred_at,actor_id,kind,summary,deal_id) values($1,now(),$2,'call','Reviewed the synthetic terms',$3)",[uuid(9),uuid(2),uuid(5)]);
    await c.query("insert into next_action(id,subject_type,subject_id,owner_id,description,due_on,created_by,updated_by) values($1,'deal',$2,$3,'Review synthetic terms',current_date,$3,$3)",[uuid(10),uuid(5),uuid(1)]);
    await c.query("insert into critical_date(id,deal_id,kind,due_on,source,created_by) values($1,$2,'option_window',current_date+7,'synthetic',$3)",[uuid(11),uuid(5),uuid(1)]);
    await identity('joe');
    const next = await context();
    for (const name of ['deal_changes','lead_changes','next_actions','critical_dates','partner_activity','new_leads']) {
      const s = await section(name,next);
      assert.equal(s.state,'ready',name);
      for (const item of s.items) { assert.ok(item.ref); assert.ok(item.text); }
    }
    assert.match((await section('partner_activity',next)).items[0].text,/Partner B/);
    await c.query('reset role');
    await c.query("insert into vendor(id,party_id,category,created_by,updated_by) values($1,$2,'synthetic',$3,$3)", [uuid(61),uuid(3),uuid(1)]);
    for (const [column, id, group] of [['lead_id',uuid(6),'lead'],['client_id',uuid(4),'client'],['vendor_id',uuid(61),'vendor']]) {
      await c.query(`insert into activity(id,occurred_at,actor_id,kind,summary,${column}) values($1,now(),$2,'call',$3,$4)`, [uuid(62+['lead','client','vendor'].indexOf(group)),uuid(2),`Synthetic ${group} summary`,id]);
    }
    await identity('joe');
    const touches = await section('partner_activity',await context());
    for (const [type,id] of [['lead',uuid(6)],['client',uuid(4)],['vendor',uuid(61)]]) {
      const item = touches.items.find(i => i.group_ref === `${type}:${id}`);
      assert.ok(item, `${type} activity must retain its subject context`);
      assert.match(item.text,new RegExp(`Partner B: Synthetic ${type} summary`));
      assert.equal(item.group_name,'Synthetic Practice');
    }
    assert.deepEqual(await invoke(ack),{ replayed:true,...lost },'new data never replaces a lost-response replay');
    await c.query('reset role');
    await c.query("insert into ops.release(service_id,release_key,environment,state,git_sha,maker_actor,source_kind,source_ref,artifact_digest,dependency_lock_digest,test_evidence_ref,security_evidence_ref,maker_verification_ref,plan_hash,readiness_receipt_id,ready_at,ended_at) values($1,'synthetic-release','production','complete',$2,'fixture','operator','synthetic','synthetic','synthetic','synthetic','synthetic','synthetic','synthetic',$3,now(),now())",[uuid(31),'1'.repeat(40),uuid(32)]);
    await identity('joe');
    assert.equal((await section('shipped_releases',await context())).state,'ready');
    const shipped = await invoke({});
    assert.ok(shipped.groups.some(g => g.items.some(i => i.ref === 'release:synthetic-release')));
    await c.query('reset role');
    await c.query("insert into ops.doc_conversation(id,title,created_by_actor) values($1,'Synthetic conversation',$2)",[uuid(20),uuid(1)]);
    await c.query("insert into ops.doc_conversation_turn(id,conversation_id,sequence,role,body,msg_id,origin_actor) values(1,$1,0,'human','Synthetic obligation',$2,'joe')",[uuid(20),uuid(22)]);
    await c.query("insert into ops.doc_suggestion(id,conversation_id,obligation_key,material_facts,source_sequence,original_text,polished_text,contributor,source_at) values($1,$2,'synthetic-review','{\"synthetic\":true}',0,'Synthetic obligation','Review the synthetic terms','joe',now())",[uuid(21),uuid(20)]);
    await identity('joe');
    const unscanned = await section('doc_suggestions',await context());
    assert.equal(unscanned.state,'unavailable');
    assert.equal(unscanned.items.length,1,'known suggestions remain visible when producer coverage is unknown');
    await c.query('reset role');
    await c.query('insert into ops.doc_suggestion_scan(idempotency_key,conversation_id,through_sequence) values($1,$2,0)',[uuid(23),uuid(20)]);
    await identity('joe');
    assert.equal((await section('doc_suggestions',await context())).state,'ready');
    await c.query('reset role');
    // Chicago midnight is still the previous day in Los Angeles. Neither the
    // database session's current_date nor the test runner's clock defines it.
    const originalZone = (await c.query('show TimeZone')).rows[0].TimeZone;
    try {
      for (const [day, threshold, criticalDue] of [
        ['2026-10-02','2026-10-02T05:00:00.000Z','2026-10-16'],
        ['2026-03-08','2026-03-08T06:00:00.000Z','2026-03-22'],
        ['2026-03-09','2026-03-09T05:00:00.000Z','2026-03-23'],
        ['2026-11-01','2026-11-01T05:00:00.000Z','2026-11-15'],
        ['2026-11-02','2026-11-02T06:00:00.000Z','2026-11-16'],
      ]) {
        await c.query('reset role');
        const oldWrite = new Date(Date.parse(threshold)-3*86400000).toISOString();
        // Insertion bypasses the update trigger that refreshes updated_at.
        await c.query('delete from next_action where id=$1',[uuid(10)]);
        await c.query("insert into next_action(id,subject_type,subject_id,owner_id,description,due_on,created_by,updated_by,created_at,updated_at) values($1,'deal',$2,$3,'Review synthetic terms',$4,$3,$3,$5,$5)",[uuid(10),uuid(5),uuid(1),day,oldWrite]);
        await c.query("update critical_date set created_at=$2,updated_at=$2,due_on=$3 where id=$1",[uuid(11),oldWrite,criticalDue]);
        await c.query("update ops.doc_suggestion set suggested_at=$2,disposition='snoozed',snoozed_material_version=material_version,snoozed_until=$3 where id=$1",[uuid(21),oldWrite,day]);
        await identity('joe');
        const thresholdContext = { since:new Date(Date.parse(threshold)-86400000).toISOString(),high_water:threshold,previous_snapshot:null };
        for (const zone of ['UTC','America/Chicago','America/Los_Angeles']) {
          await c.query("select set_config('TimeZone',$1,false)",[zone]);
          for (const name of ['next_actions','critical_dates','doc_suggestions']) {
            const before = await section(name,{ ...thresholdContext,high_water:new Date(Date.parse(threshold)-1).toISOString() });
            assert.equal(before.state,'empty',`${name} before Chicago midnight (${zone}, ${day})`);
            for (const high_water of [threshold,new Date(Date.parse(threshold)+13*3600000).toISOString()]) {
              const crossed = await section(name,{ ...thresholdContext,high_water });
              assert.equal(crossed.state,'ready',`${name} after Chicago midnight (${zone}, ${high_water})`);
              assert.equal(crossed.items.length,1);
              assert.equal(Date.parse(crossed.items[0].at),Date.parse(threshold));
            }
            assert.equal((await section(name,{ ...thresholdContext,since:threshold })).state,'empty',
              `${name} threshold is not repeated (${zone}, ${day})`);
          }
        }
      }
    } finally {
      await c.query("select set_config('TimeZone',$1,false)",[originalZone]);
    }
    assert.equal((await c.query('show TimeZone')).rows[0].TimeZone,originalZone);
    await identity('dell');
    assert.equal((await section('new_leads',await context())).state,'empty');
    await c.query('reset role');
    await c.query('alter table ops.doc_suggestion rename to unavailable_suggestions');
    await identity('joe');
    await assert.rejects(section('doc_suggestions',await context()), /does not exist/);
    const partial = await invoke({ mark_seen:true,idempotency_key:uuid(51) });
    assert.equal(partial.sections.doc_suggestions.state,'unavailable');
    assert.equal(partial.marked_seen,false);
    assert.equal((await context()).since,lost.high_water);
    // A transaction started before acknowledgement can commit afterwards.
    const late = await connect();
    await late.query('begin');
    await late.query("insert into event(id,occurred_at,actor_id,verb,subject_type,subject_id,field,cause) values($1,now(),$2,'update-deal','deal',$3,'phase','human_stated')",[uuid(12),uuid(2),uuid(5)]);
    const beforeCommit = await context();
    await c.query('select ops.mark_whats_new_seen($1,$2)',[beforeCommit.high_water,beforeCommit.snapshot]);
    await late.query('commit');
    const afterCommit = await section('deal_changes',await context());
    assert.ok(afterCommit.items.some(i => i.ref === `event:${uuid(12)}`),'late commit must not fall behind the timestamp watermark');
    // An unseen creation survives a later tuple rewrite before catch-up.
    await late.query('begin');
    await late.query("insert into lead(id,registry_ref,party_id,stage,created_by,updated_by,owner_id) values($1,'L-LATE',$2,'new',$3,$3,$3)", [uuid(60),uuid(3),uuid(1)]);
    const beforeLeadCommit = await context();
    await c.query('select ops.mark_whats_new_seen($1,$2)', [beforeLeadCommit.high_water,beforeLeadCommit.snapshot]);
    await late.query('commit');
    await late.query("update lead set stage='contacted',updated_at=clock_timestamp() where id=$1", [uuid(60)]);
    const lateLeads = await section('new_leads',await context());
    assert.ok(lateLeads.items.some(i => i.ref === 'L-LATE'), 'late creation must survive a later update');
    const consumed = await context();
    await c.query('select ops.mark_whats_new_seen($1,$2)', [consumed.high_water,consumed.snapshot]);
    await late.query("update lead set updated_at=clock_timestamp() where id=$1", [uuid(60)]);
    assert.equal((await section('new_leads',await context())).items.some(i => i.ref === 'L-LATE'),false, 'an already seen lead update is not a new creation');
    await c.query('reset role');
    await c.query("insert into next_action(id,subject_type,subject_id,owner_id,description,created_by,updated_by) values($1,'client',$2,$3,'Complete synthetic client work',$3,$3)",[uuid(70),uuid(4),uuid(1)]);
    await identity('joe');
    const beforeCompletion = await context();
    await c.query('select ops.mark_whats_new_seen($1,$2)',[beforeCompletion.high_water,beforeCompletion.snapshot]);
    await c.query('begin');
    const completed = await executeRegisteredTool(c,actor,'complete-action',{ref:uuid(4),idempotency_key:uuid(71)});
    await c.query('commit');
    assert.equal(completed.count,1);
    const completionAnswer = await invoke({});
    assert.ok(completionAnswer.sections.next_actions.items.some(i => i.ref === `next-action:${uuid(70)}` && i.sentence.includes('was completed')));
    const completedSeen = await context();
    await c.query('select ops.mark_whats_new_seen($1,$2)',[completedSeen.high_water,completedSeen.snapshot]);
    for (const status of ['done','dropped']) {
      await c.query('reset role');
      await c.query('update next_action set status=$1,updated_at=clock_timestamp() where id=$2',[status,uuid(10)]);
      await identity('joe');
      const actions = await section('next_actions',await context());
      assert.ok(actions.items.some(i => i.ref === `next-action:${uuid(10)}` && i.text.includes(status === 'done' ? 'completed' : 'dropped')),status);
      assert.ok(actions.items.every(i => !i.text.includes(' is due ')), 'closed work must not sound pending');
      const seen = await context();
      await c.query('select ops.mark_whats_new_seen($1,$2)',[seen.high_water,seen.snapshot]);
      assert.equal((await section('next_actions',await context())).state,'empty','acknowledged closure is not repeated');
    }
    for (const status of ['passed','cleared']) {
      await c.query('reset role');
      await c.query('update critical_date set status=$1,updated_at=clock_timestamp() where id=$2',[status,uuid(11)]);
      await identity('joe');
      const dates = await section('critical_dates',await context());
      assert.ok(dates.items.some(i => i.ref === `critical-date:${uuid(11)}` && i.text.includes(status)),status);
      assert.ok(dates.items.every(i => !i.text.includes(' due ')), 'closed date must not sound pending');
      const seen = await context();
      await c.query('select ops.mark_whats_new_seen($1,$2)',[seen.high_water,seen.snapshot]);
      assert.equal((await section('critical_dates',await context())).state,'empty');
    }
  } finally {
    try {
      for (const c of clients) await c.end();
      if (running) execFileSync(path.join(bin,'pg_ctl'), ['-D',dir,'-m','fast','-w','stop'], { stdio:'pipe' });
      mkdirSync('/tmp/_to_delete',{ recursive:true });
      renameSync(dir,path.join('/tmp/_to_delete',path.basename(dir)));
    } finally {
      await releaseBudget();
    }
  }
});
