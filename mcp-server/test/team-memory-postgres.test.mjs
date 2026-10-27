// P1 S2 proof (docs/memory/team-memory-design.md §9): attach, real-time
// lesson, attribution, stale write, and the memory_attach_required gate,
// against a disposable PostgreSQL built from db/schema.sql + the S2 migration.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { publicError } from '../src/public-errors.ts';
import { SCAC_MUTATION_CURRENT_DB_GRANTS } from '../src/scac-mutation-registry.current.generated.js';

const MIGRATION = readdirSync(new URL('../../migrations/', import.meta.url))
  .find(name => /^\d{4}_team_memory_store\.sql$/.test(name));
const LESSON = 'Ruled out: X — the dependency checks were clean. Next agent: skip X, go straight to Y.';

let fixture, c, actor;
before(async () => {
  assert.ok(MIGRATION, 'the S2 migration exists');
  const bin = ['/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin',
    '/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin'].find(p => existsSync(`${p}/initdb`));
  assert.ok(bin, 'PostgreSQL is required for the isolated team-memory proof');
  fixture = await acquireDisposablePostgres({ prefix: 'team-memory-', pgCtl: `${bin}/pg_ctl` });
  await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
  await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
  await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
  c = new pg.Client({ host: fixture.root, user: 'carr_ci', database: 'carr_ci' });
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', '-X', '-v', 'ON_ERROR_STOP=1', '-q',
    '-f', new URL('../../db/schema.sql', import.meta.url).pathname]);
  await c.query(readFileSync(new URL(`../../migrations/${MIGRATION}`, import.meta.url), 'utf8'));
  actor = { ...(await c.query(`insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner')
    on conflict(slug) do update set active=true returning id,slug`)).rows[0], human: true, client_id: 'synthetic-client' };
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });

async function call(name, args) {
  await c.query('begin');
  try {
    await c.query('set local role carr_writer');
    const keyed = name === 'dry-run-doctrine-gates' ? args : { idempotency_key: randomUUID(), ...args };
    const result = await executeRegisteredTool(c, actor, name, keyed);
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}

async function refused(promise, code) {
  await assert.rejects(promise, error => {
    assert.equal(error.payload?.error, code);
    assert.equal(publicError(error).error, code);
    return true;
  });
}

const attach = (access = 'read_write', surface = 'claude_code') =>
  call('attach-memory', { surface, access, native_session_id: `synthetic-${randomUUID()}`, host: 'synthetic-host' });
const lesson = (attach_id, base_version = 0, section_key = 'incidents/probe-x', body_text = LESSON) =>
  call('write-doctrine-section', { document: 'team-memory', section_key, body_text, base_version,
    ...(attach_id === undefined ? {} : { attach_id }) });

let a, b;
test('agent A attaches read-write and receives both stores', async () => {
  a = await attach();
  assert.match(a.attach_id, /^[0-9a-f-]{36}$/);
  const [org, team] = a.stores;
  assert.equal(org.name, 'org-conventions');
  assert.equal(org.access, 'read_only');
  assert.match(org.boot.digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(typeof org.boot.counts.rules, 'number');
  assert.match(org.propose_with, /conventions\/proposed\//);
  assert.equal(team.name, 'team-memory');
  assert.equal(team.access, 'read_write');
  assert.equal(team.document, 'team-memory');
  assert.deepEqual(team.index, []);
  assert.match(team.instructions, /attach_id/);
  const row = (await c.query('select * from memory_session where attach_id=$1', [a.attach_id])).rows[0];
  assert.equal(row.actor_id, actor.id);
  assert.equal(row.surface, 'claude_code');
  assert.equal(row.access, 'read_write');
  assert.equal(row.host, 'synthetic-host');
  assert.equal(Number(row.attached_generation), team.generation);
  assert.match(row.attached_index_digest, /^[0-9a-f]{64}$/);
});

test('A writes a lesson containing an em-dash, attributed to its attach', async () => {
  const out = await lesson(a.attach_id);
  assert.equal(out.version, 1);
  const rev = (await c.query(`select r.session_key from doctrine_revision r
    join doctrine_section s on s.id=r.section_id where s.section_key='incidents/probe-x'`)).rows;
  assert.deepEqual(rev.map(r => r.session_key), [a.attach_id]);
});

test('agent B attaches and its index shows A\'s lesson at version 1, written by A', async () => {
  b = await attach('read_write', 'codex');
  const team = b.stores[1];
  const entry = team.index.find(s => s.section_key === 'incidents/probe-x');
  assert.equal(entry.version, 1);
  assert.match(entry.content_hash, /^[0-9a-f]{64}$/);
  assert.equal(entry.last_writer_surface, 'claude_code');
  assert.ok(entry.review_after, 'the team-memory review policy sets the staleness clock');
  const read = await executeRegisteredTool(c, actor, 'read-doctrine', { document: 'team-memory' });
  assert.equal(read.sections[0].body.text, LESSON);
  assert.notEqual(b.stores[1].generation, a.stores[1].generation);
  const digests = (await c.query('select attached_index_digest from memory_session where attach_id = any($1::uuid[])',
    [[a.attach_id, b.attach_id]])).rows.map(r => r.attached_index_digest);
  assert.equal(new Set(digests).size, 2, 'the index digest records what each session read');
});

test('a stale base_version returns version_conflict', async () => {
  await refused(lesson(b.attach_id, 0), 'version_conflict');
});

test('a write without attach_id is refused with memory_attach_required', async () => {
  await refused(lesson(undefined, 1), 'memory_attach_required');
});

test('an unknown or malformed attach_id is refused with memory_attach_required', async () => {
  await refused(lesson(randomUUID(), 1), 'memory_attach_required');
  await refused(lesson('not-an-attach', 1), 'memory_attach_required');
});

test('a read-only attach cannot write or retire team memory', async () => {
  const ro = await attach('read_only', 'dot');
  assert.equal(ro.stores[1].access, 'read_only');
  await refused(lesson(ro.attach_id, 1), 'memory_read_only');
  await refused(call('change-doctrine-sections', { document: 'team-memory', attach_id: ro.attach_id,
    items: [{ op: 'retire', section_key: 'incidents/probe-x', base_version: 1 }] }), 'memory_read_only');
});

test('change-doctrine-sections writes and retires atomically under one attach', async () => {
  const out = await call('change-doctrine-sections', { document: 'team-memory', attach_id: b.attach_id, items: [
    { section_key: 'incidents/probe-y', body_text: 'Synthetic second lesson.', base_version: 0 },
    { op: 'retire', section_key: 'incidents/probe-x', base_version: 1 }] });
  assert.deepEqual(out.sections.map(s => [s.section_key, s.status]),
    [['incidents/probe-x', 'retired'], ['incidents/probe-y', 'active']]);
  const items = (await c.query(`select op from doctrine_change_item where change_set_id=$1 order by op`, [out.change_set_id])).rows;
  assert.deepEqual(items.map(i => i.op), ['retire', 'write']);
  const cs = (await c.query('select session_key from doctrine_change_set where id=$1', [out.change_set_id])).rows[0];
  assert.equal(cs.session_key, b.attach_id);
  const index = (await attach()).stores[1].index.map(s => s.section_key);
  assert.deepEqual(index, ['incidents/probe-y']);
});

test('retire-doctrine-section on team memory requires a read-write attach', async () => {
  const sec = (await c.query(`select id,current_version from doctrine_section where section_key='incidents/probe-y'`)).rows[0];
  const args = { section_id: sec.id, base_version: Number(sec.current_version), reason: 'Synthetic retire' };
  await refused(call('retire-doctrine-section', args), 'memory_attach_required');
  const out = await call('retire-doctrine-section', { ...args, attach_id: a.attach_id });
  assert.equal(out.status, 'retired');
});

test('the gate passes other distillation documents untouched', async () => {
  const out = await call('write-doctrine-section', { document: 'dreams-log', section_key: 'synthetic-night',
    body_text: 'Synthetic dream log entry.', base_version: 0 });
  assert.equal(out.version, 1);
  const dry = await call('dry-run-doctrine-gates', { op: 'write', document: 'team-memory', body_text: 'Synthetic.' });
  assert.equal(dry.result, 'fail');
  assert.ok(dry.findings.some(f => f.check_key === 'memory_attach_required' && !f.passed));
  const ok = await call('dry-run-doctrine-gates', { op: 'write', document: 'team-memory', body_text: 'Synthetic.', attach_id: a.attach_id });
  assert.equal(ok.result, 'pass');
});

test('attach-memory refuses an unknown surface or access and blank text, recording nothing', async () => {
  const before = (await c.query('select count(*) from memory_session')).rows[0].count;
  await refused(call('attach-memory', { surface: 'pager' }), 'value_not_in_declared_vocabulary');
  await refused(call('attach-memory', { surface: 'codex', access: 'admin' }), 'value_not_in_declared_vocabulary');
  await assert.rejects(call('attach-memory', { surface: 'codex', host: '  ' }),
    error => publicError(error).error === 'database_refused_the_statement');
  assert.equal((await c.query('select count(*) from memory_session')).rows[0].count, before);
});

test('the installed grant contract registers the memory_session writer grant', async () => {
  const installed = (await c.query('select ops.scac_current_grant_contract() as contract')).rows[0].contract;
  assert.deepEqual(installed, SCAC_MUTATION_CURRENT_DB_GRANTS);
  assert.deepEqual(installed.relation_dml.filter(g => g.relation === 'public.memory_session').map(g => g.privilege), ['insert']);
  const acl = (await c.query(`select privilege_type from information_schema.role_table_grants
    where table_name='memory_session' and grantee='carr_writer' order by 1`)).rows.map(r => r.privilege_type);
  assert.deepEqual(acl, ['INSERT', 'SELECT'], 'attach receipts are insert-only for the writer');
});
