import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

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
      assert.equal(error.payload?.error, 'unsupported_decision_about');
      assert.equal(error.payload.field, 'about');
      assert.equal(error.payload.ref, Array.isArray(about) ? about[0] : about);
      assert.match(error.payload.hint, /Work Request/);
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
  }, actor, 'carr_writer'), error => error.payload?.error === 'unsupported_decision_about');
  assert.equal((await c.query('select count(*) from event where idempotency_key=$1', [mixedKey])).rows[0].count, '0');
  const out = await command('log-decision', {
    ...args, idempotency_key: randomUUID(), about: party.ref,
  }, actor, 'carr_writer');
  assert.equal(out.ok, true);
  assert.deepEqual(out.about, [{ type: 'party', id: party.id, ref: party.ref }]);
});
