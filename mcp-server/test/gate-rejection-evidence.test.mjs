// Test transactional gate rejection evidence and rollback behavior.
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import pg from 'pg';
import { Pool, neonConfig } from '@neondatabase/serverless';
import { callTool } from '../src/mcp.js';
import { ToolError } from '../src/tools.js';
import { authenticatedIdentity } from '../src/identity.js';
import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';

const ACTOR = '10000000-0000-4000-8000-000000000001';
const args = () => ({ idempotency_key: crypto.randomUUID(), document: 'synthetic-doc',
  section_key: 'synthetic-section', body_text: 'Synthetic rejected prose with delve.', base_version: 0 });

async function database() {
  const bin = ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']
    .map(config => { try { return execFileSync(config, ['--bindir'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim(); } catch { return ''; } })
    .find(candidate => existsSync(path.join(candidate, 'postgres')));
  assert.ok(bin, 'rejection evidence proof requires local PostgreSQL');
  const release = await acquirePostgresFixtureGroup();
  let fixture, owner, writer;
  const close = async () => {
    try { await writer?.end(); await owner?.end(); }
    finally { try { await fixture?.close(); } finally { await release(); } }
  };
  try {
    fixture = await acquireDisposablePostgres({ prefix: 'gate-rejection-', pgCtl: path.join(bin, 'pg_ctl') });
    const data = path.join(fixture.root, 'data');
    await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'),
      '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    owner = new pg.Client({ host: fixture.root, user: 'fixture', database: 'postgres' });
    await owner.connect();
    await owner.query(`create role carr_writer login; create role carr_reader; create role carr_jobs; create role carr_authority;
      create table actor(id uuid primary key, slug text);
      insert into actor values ('${ACTOR}', 'joe');
      create table tool_call(idempotency_key text primary key, verb text, actor_id uuid, request_hash text,
        response jsonb, via text, client_id text, organization_tenant_id text, sponsoring_human_slug text,
        personal_scope text, authorization_class text, correlation_id uuid);
      create table event(occurred_at timestamptz, recorded_at timestamptz, actor_id uuid,
        verb text, subject_type text, subject_id uuid, field text, old_value jsonb, new_value jsonb,
        cause text, human_quote text, agent_rationale text, idempotency_key text, via text,
        client_id text, organization_tenant_id text, sponsoring_human_slug text, personal_scope text,
        authorization_class text, correlation_id uuid);
      grant select on actor to carr_writer;
      grant select, insert on tool_call, event to carr_writer;`);
    for (const file of ['0075_doctrine_store.sql', '0076_doctrine_grants.sql'])
      await owner.query(await readFile(new URL(`../../migrations/${file}`, import.meta.url), 'utf8'));
    await owner.query(`insert into doctrine_document(slug,title,content_class,created_by)
      values ('synthetic-doc','Synthetic document','sop','${ACTOR}');`);
    writer = new pg.Client({ host: fixture.root, user: 'carr_writer', database: 'postgres' });
    await writer.connect();
    return { owner, writer, close };
  } catch (error) { await close(); throw error; }
}

async function rejection(promise) {
  let payload;
  await assert.rejects(promise, error => {
    assert.ok(error instanceof ToolError);
    assert.equal(error.payload.error, 'gate_blocked');
    payload = error.payload;
    return true;
  });
  return payload;
}

async function withDatabase(run) {
  const db = await database();
  const connect = Pool.prototype.connect, end = Pool.prototype.end, fetch = neonConfig.fetchFunction;
  try {
    Pool.prototype.connect = async () => ({ query: db.writer.query.bind(db.writer), release() {} });
    Pool.prototype.end = async () => {};
    neonConfig.fetchFunction = async (_url, options) => {
      const { query, params } = JSON.parse(options.body);
      const result = await db.writer.query({ text: query, values: params, rowMode: 'array' });
      return Response.json({ fields: result.fields.map(f => ({ name: f.name, dataTypeID: f.dataTypeID })),
        rows: result.rows, rowCount: result.rowCount, command: result.command, rowAsArray: true });
    };
    const env = { DATABASE_URL_WRITER: 'postgresql://carr_writer@fixture.invalid/postgres' };
    const call = (input = args()) => callTool(env, authenticatedIdentity.connectionForGrant({ slug: 'joe' }),
      'write-doctrine-section', input);
    await run({ ...db, env, call });
  } finally {
    Pool.prototype.connect = connect; Pool.prototype.end = end; neonConfig.fetchFunction = fetch;
    await db.close();
  }
}

test('a rejected doctrine write rolls back content and returns readable gate evidence', async () => {
  await withDatabase(async ({ owner, call }) => {
    const before = (await owner.query(`select jsonb_build_object(
      'documents',(select jsonb_agg(d) from doctrine_document d),
      'sections',(select jsonb_agg(s) from doctrine_section s),
      'revisions',(select jsonb_agg(r) from doctrine_revision r),
      'generation',(select generation from doctrine_meta where id=1),
      'events',(select jsonb_agg(e) from event e),
      'calls',(select jsonb_agg(t) from tool_call t)) as state`)).rows[0].state;
    const input = args();
    const payload = await rejection(call(input));
    assert.equal(payload.persisted, true);
    const run = (await owner.query('select * from doctrine_gate_run where id=$1', [payload.gate_run_id])).rows[0];
    assert.ok(run, 'the returned ID must name a committed row');
    assert.equal(run.actor_id, ACTOR);
    assert.equal(run.result, 'fail');
    assert.equal(run.change_set_id, null);
    assert.equal(run.report.verb, 'write-doctrine-section');
    assert.match(run.report.input_digest, /^sha256:[0-9a-f]{64}$/);
    assert.equal(JSON.stringify(run.report).includes(input.body_text), false);
    const findings = (await owner.query('select check_key,severity,passed,message,path from doctrine_gate_finding where run_id=$1',
      [payload.gate_run_id])).rows;
    assert.deepEqual(findings, payload.findings);
    const after = (await owner.query(`select jsonb_build_object(
      'documents',(select jsonb_agg(d) from doctrine_document d),
      'sections',(select jsonb_agg(s) from doctrine_section s),
      'revisions',(select jsonb_agg(r) from doctrine_revision r),
      'generation',(select generation from doctrine_meta where id=1),
      'events',(select jsonb_agg(e) from event e),
      'calls',(select jsonb_agg(t) from tool_call t)) as state`)).rows[0].state;
    assert.deepEqual(after, before);
  });
});

test('an unavailable audit writer returns persisted:false without a dead gate run ID', async () => {
  await withDatabase(async ({ owner, call }) => {
    neonConfig.fetchFunction = async () => { throw new Error('synthetic writer unavailable'); };
    const payload = await rejection(call());
    assert.equal(payload.persisted, false);
    assert.equal(Object.hasOwn(payload, 'gate_run_id'), false);
    assert.ok(payload.findings.some(f => f.check_key === 'banned_phrases'));
    assert.equal((await owner.query('select * from doctrine_gate_run')).rowCount, 0);
    assert.equal((await owner.query('select * from doctrine_gate_finding')).rowCount, 0);
    assert.equal((await owner.query('select * from doctrine_section')).rowCount, 0);
  });
});

test('a failed finding insert leaves neither a partial run nor rejected content', async () => {
  await withDatabase(async ({ owner, writer, call }) => {
    await owner.query(`create function reject_audit_finding() returns trigger language plpgsql as
      $$ begin raise exception 'synthetic finding persistence failure'; end $$;
      create trigger reject_audit_finding before insert on doctrine_gate_finding
        for each row when (current_setting('test.reject_finding',true) = 'true') execute function reject_audit_finding();`);
    // Reject only the detached statement, after runGates has evaluated the input.
    const fetch = neonConfig.fetchFunction;
    neonConfig.fetchFunction = async (...parameters) => {
      await writer.query("select set_config('test.reject_finding','true',false)");
      return fetch(...parameters);
    };
    const payload = await rejection(call());
    assert.equal(payload.persisted, false);
    assert.equal(Object.hasOwn(payload, 'gate_run_id'), false);
    assert.equal((await owner.query('select * from doctrine_gate_run')).rowCount, 0);
    assert.equal((await owner.query('select * from doctrine_section')).rowCount, 0);
  });
});

test('successful content and gate evidence commit together without a detached audit write', async () => {
  await withDatabase(async ({ owner, call }) => {
    neonConfig.fetchFunction = async () => { throw new Error('successful writes must not use detached audit'); };
    const input = { ...args(), body_text: 'Synthetic accepted prose.' };
    const response = await call(input);
    assert.equal(response.ok, true);
    assert.equal((await owner.query('select * from doctrine_revision')).rowCount, 1);
    const runs = (await owner.query('select * from doctrine_gate_run')).rows;
    assert.equal(runs.length, 1);
    assert.equal(runs[0].result, 'pass');
    assert.ok(Array.isArray(runs[0].report), 'successful evidence retains its existing report format');
    assert.equal((await owner.query('select * from tool_call')).rowCount, 1);
  });
});

test('a later business failure rolls back successful gate evidence along with content', async () => {
  await withDatabase(async ({ owner, call }) => {
    await owner.query(`create function reject_revision() returns trigger language plpgsql as
      $$ begin raise exception 'synthetic revision failure'; end $$;
      create trigger reject_revision before insert on doctrine_revision
        for each row execute function reject_revision();`);
    neonConfig.fetchFunction = async () => { throw new Error('non-gate failures must not persist a gate rejection'); };
    await assert.rejects(call({ ...args(), body_text: 'Synthetic accepted prose.' }), /synthetic revision failure/);
    assert.equal((await owner.query('select * from doctrine_gate_run')).rowCount, 0);
    assert.equal((await owner.query('select * from doctrine_section')).rowCount, 0);
    assert.equal((await owner.query('select * from tool_call')).rowCount, 0);
  });
});

test('a batch rejection rolls back earlier sections and the change set while preserving its failed gate', async () => {
  await withDatabase(async ({ owner, env }) => {
    const payload = await rejection(callTool(env, authenticatedIdentity.connectionForGrant({ slug: 'joe' }),
      'change-doctrine-sections', { idempotency_key: crypto.randomUUID(), document: 'synthetic-doc', items: [
        { section_key: 'a-accepted', body_text: 'Synthetic accepted prose.', base_version: 0 },
        { section_key: 'b-rejected', body_text: 'Synthetic prose with delve.', base_version: 0 },
      ] }));
    assert.equal(payload.persisted, true);
    assert.equal((await owner.query('select * from doctrine_change_set')).rowCount, 0);
    assert.equal((await owner.query('select * from doctrine_section')).rowCount, 0);
    assert.equal((await owner.query('select * from doctrine_revision')).rowCount, 0);
    const runs = (await owner.query('select * from doctrine_gate_run')).rows;
    assert.equal(runs.length, 1);
    assert.equal(runs[0].id, payload.gate_run_id);
    assert.equal(runs[0].change_set_id, null);
    assert.equal(runs[0].report.verb, 'change-doctrine-sections');
  });
});

test('the rejection response waits for the audit writer to finish', async () => {
  await withDatabase(async ({ call }) => {
    const fetch = neonConfig.fetchFunction;
    let release, entered;
    const pending = new Promise(resolve => { release = resolve; });
    const started = new Promise(resolve => { entered = resolve; });
    neonConfig.fetchFunction = async (...parameters) => { entered(); await pending; return fetch(...parameters); };
    let settled = false;
    const response = rejection(call()).then(payload => { settled = true; return payload; });
    try {
      await started;
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(settled, false);
    } finally { release(); }
    assert.equal((await response).persisted, true);
  });
});
