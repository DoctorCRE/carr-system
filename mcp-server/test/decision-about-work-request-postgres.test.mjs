// Test decision subject validation and Work Request handling in PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';
import { publicError } from '../src/public-errors.ts';

let fixture, admin, database, c, actor, connection;
before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
    assert.equal(url.search, '', 'fixture connection must not override its loopback host');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    const name = `decision_about_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${name}" template template0`);
    database = name;
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'decision-about-', pgCtl: `${bin}/pg_ctl` });
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
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

async function command(name, args = {}, identity = actor, role = null, client = c) {
  await client.query('begin');
  try {
    if (role) await client.query(`set local role ${role}`);
    const result = await executeRegisteredTool(client, identity, name, args);
    await client.query('commit');
    return result;
  } catch (error) { await client.query('rollback'); throw error; }
}

test('log-decision refuses Work Request about refs without writing a decision or replay', async () => {
  const before = (await c.query('select count(*) from event')).rows[0].count;
  for (const about of ['WR-000508', ['WR-000508'], 'WR-AI-002', 'WR-SIEP-26']) {
    const idempotency_key = randomUUID();
    await assert.rejects(command('log-decision', {
      idempotency_key, title: 'Synthetic settled decision',
      rationale: 'Synthetic rationale', about,
    }, actor, 'carr_writer'), error => {
      assert.ok(['unsupported_decision_about', 'decision_about_work_request_not_found'].includes(error.payload?.error));
      assert.equal(error.payload.field, 'about');
      return true;
    });
    assert.equal((await c.query('select count(*) from tool_call where idempotency_key=$1', [idempotency_key])).rows[0].count, '0');
  }
  assert.equal((await c.query('select count(*) from event')).rows[0].count, before);
});

test('decline-work-request accepts an unstarted WR-AI program ref', async () => {
  await c.query(`insert into ops.work_request
    (ref,title,state,requester_actor,owner_actor,organization_tenant_id,program_key,program_ordinal)
    values ('WR-AI-999','Synthetic program request','ready','joe','joe','carr-internal',
            'carr-ai-engineering-suite-v1',999)`);
  const out = await command('decline-work-request', {
    idempotency_key: randomUUID(), human_ref: 'WR-AI-999', base_version: 1,
    exit_reason: 'Synthetic obsolete request',
  }, actor, 'carr_writer');
  assert.equal(out.ok, true);
  assert.equal(out.state, 'declined');
  assert.equal(out.human_ref, 'WR-AI-999');
  assert.equal(out.version, 2);
});

test('log-decision still attaches supported subjects and refuses a mixed list atomically', async () => {
  const party = (await c.query(`insert into party(kind,name,ref,created_by,updated_by)
    values('person','Synthetic decision subject','P-999508',$1,$1) returning id,ref`, [actor.id])).rows[0];
  const args = { title: 'Synthetic subject decision', rationale: 'Synthetic rationale' };
  const mixedKey = randomUUID();
  await assert.rejects(command('log-decision', {
    ...args, idempotency_key: mixedKey, about: [party.ref, 'WR-000508'],
  }, actor, 'carr_writer'), error => ['unsupported_decision_about', 'decision_about_work_request_not_found'].includes(error.payload?.error));
  assert.equal((await c.query('select count(*) from event where idempotency_key=$1', [mixedKey])).rows[0].count, '0');
  const out = await command('log-decision', {
    ...args, idempotency_key: randomUUID(), about: party.ref,
  }, actor, 'carr_writer');
  assert.equal(out.ok, true);
  assert.deepEqual(out.about, [{ type: 'party', id: party.id, ref: party.ref }]);
});


test('decision about Work Requests returns public errors and leaves mixed writes unchanged', async () => {
  const doc = (await c.query(`insert into doctrine_document(slug,title,content_class,visibility) values('about-test','Synthetic','reference','shared') returning id`)).rows[0];
  const section = (await c.query(`insert into doctrine_section(document_id,section_key,title,ordinal,status,current_version) values($1,'origin','Synthetic',1,'active',1) returning id`, [doc.id])).rows[0];
  const revision = (await c.query(`insert into doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash) values($1,1,$2,'{}','Synthetic',$3) returning id`, [section.id,actor.id,'b'.repeat(64)])).rows[0];
  await c.query(`insert into ops.work_request (ref,title,requester_actor,owner_actor,capture_idempotency_key,organization_tenant_id,doctrine_section_id,doctrine_revision_id,sourced_capture_sequence,origin_ref)
    values ('WR-999123','Synthetic request','joe','joe',$1,'carr-internal',$2,$3,999123,'doctrine:about-test#origin')`, [randomUUID(),section.id,revision.id]);
  await c.query(`insert into ops.work_request (ref,title,state,requester_actor,owner_actor,organization_tenant_id,program_key,program_ordinal)
    values ('WR-AI-998','Synthetic program','ready','joe','joe','carr-internal','carr-ai-engineering-suite-v1',998)`);
  const party = (await c.query(`insert into party(kind,name,ref,created_by,updated_by)
    values('person','Synthetic lead party','P-999509',$1,$1) returning id`, [actor.id])).rows[0];
  const lead = (await c.query(`insert into lead(party_id,registry_ref,stage,created_by,updated_by) values($1,'L-999509','new',$2,$2) returning registry_ref as ref`, [party.id, actor.id])).rows[0];
  const decision = await command('log-decision', {idempotency_key: randomUUID(), title: 'Synthetic original', rationale: 'Synthetic rationale'}, actor, 'carr_writer');
  for (const verb of ['log-decision', 'update-decision']) {
    for (const [about, code] of [['WR-999123','unsupported_decision_about'], ['WR-AI-998','unsupported_decision_about'], ['WR-999999999999','decision_about_work_request_not_found'], [[lead.ref, 'WR-999123'],'unsupported_decision_about']]) {
      const before = (await c.query('select count(*) from event')).rows[0].count;
      const args = verb === 'log-decision'
        ? {title:'Synthetic decision', rationale:'Synthetic rationale', about}
        : {decision_id: decision.decision_id, title:'Synthetic changed', about};
      await assert.rejects(command(verb, {...args, idempotency_key: randomUUID()}, actor, 'carr_writer'), error => {
        const result = publicError(error, {inputSchema: TOOLS[verb].inputSchema});
        assert.equal(result.error, code);
        assert.match(result.hint, /about/i);
        return true;
      });
      assert.equal((await c.query('select count(*) from event')).rows[0].count, before);
      const original = (await c.query('select new_value from event where subject_id=$1 and verb=$2', [decision.decision_id, 'log-decision'])).rows[0];
      assert.equal(original.new_value.title, 'Synthetic original');
    }
  }
});
