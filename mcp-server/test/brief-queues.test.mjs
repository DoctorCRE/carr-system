import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import pg from 'pg';
import { executeRegisteredTool, ToolError } from '../src/tools.js';
import { acquireDisposablePostgres, acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';
import { readNeedsJoe } from '../src/needs-joe.js';

const JOE = { slug: 'joe', human: true, via: 'oauth-google', client_id: 'fixture' };
let bin;
try { bin = execFileSync('pg_config', ['--bindir'], { encoding: 'utf8' }).trim(); } catch {}
if (!bin || !existsSync(path.join(bin, 'postgres'))) {
  bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin', '/usr/lib/postgresql/16/bin']
    .find(candidate => existsSync(path.join(candidate, 'postgres')));
}
let fixture, db, release;
before(async () => {
  assert.ok(bin, 'install PostgreSQL to run the triage contract proof');
  release = await acquirePostgresFixtureGroup();
  fixture = await acquireDisposablePostgres({ prefix: 'brief-queues-', pgCtl: path.join(bin, 'pg_ctl') });
  const data = path.join(fixture.root, 'data');
  await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
  await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'), '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
  db = new pg.Client({ host: fixture.root, user: 'fixture', database: 'postgres', types: {
    // Match the Worker's UTC DATE serialization regardless of the test host's timezone.
    getTypeParser: (oid, format) => oid === 1082
      ? value => new Date(`${value}T00:00:00.000Z`) : pg.types.getTypeParser(oid, format),
  } });
  await db.connect();
  await db.query(`create table triage_fixture (
    item_kind text, id uuid, subject_type text, subject_id uuid, owner text,
    what text, due_on date, subject_name text, subject_ref text, business_days_overdue integer);
    create view v_today_triage as select * from triage_fixture;
    create table loop_item (number text, kind text, status text, owner text, domain text, marker text,
      blocker_class text, blocker_detail text, unblocks text, created_at timestamptz, title text, body text,
      due_on date, version integer, since_text text);
    create schema ops;
    create function ops.read_governance_queue() returns jsonb language sql as $$select '{}'::jsonb$$;
    create table ops.work_request (ref text, title text, blocker_detail text, updated_at timestamptz,
      state text, organization_tenant_id text);
    create table board_question (board_id text, question_id text, prompt text, asked_at timestamptz,
      organization_tenant_id text, sponsoring_human_slug text, current boolean, revision integer);
    create table board_answer (id uuid, organization_tenant_id text, sponsoring_human_slug text,
      board_id text, question_id text, question_revision integer);
    create table board_snapshot (snapshot_json jsonb, updated_at timestamptz,
      organization_tenant_id text, sponsoring_human_slug text, board_id text)`);
});
after(async () => { await db?.end(); await fixture?.close(); await release?.(); });

const call = args => executeRegisteredTool(db, JOE, 'morning-brief-queue', args);
async function seedLoops(values) {
  await db.query('truncate loop_item');
  for (const [number, owner, due, blocker = 'human_only', domain = 'business'] of values)
    await db.query(`insert into loop_item values ($1,'open_loop','open',$2,$5,'none',$4,null,
      null,'2026-01-01', $1, '',$3,1,null)`, [number, owner, due, blocker, domain]);
}
test('Needs Joe reaches an eligible row after 500 internal, waiting, and prospecting rows', async () => {
  await seedLoops([
    ...Array.from({ length: 501 }, (_, i) => [String(i), 'joe', null,
      i % 2 ? 'counterparty' : null]),
    ['urgent', 'joe', '2026-01-01'],
  ]);
  const list = await readNeedsJoe(db, JOE, {}, { now: Date.parse('2026-10-01') });
  assert.deepEqual(list.items.map(row => row.key), ['loop:urgent']);
  assert.equal(list.excluded.count, 501);
});
test('loop queue puts due work before 61 future or undated low numbered loops across partners', async () => {
  await seedLoops([
    ...Array.from({ length: 61 }, (_, i) => [String(i), 'joe', i % 2 ? null : '2027-01-01']),
    ['999', 'joe', '2026-01-01'], ['agent', 'claude', '2025-01-01'],
  ]);
  const brief = await executeRegisteredTool(db, JOE, 'morning-brief', {});
  const first = brief.sections.loops;
  assert.equal(first.items[0].number, '999');
  assert.equal(first.items[0].owner_partner, 'joe');
  assert.equal(first.total, 62);
  assert.equal(first.remaining, 12);
  const all = [...first.items];
  let page = first;
  while (page.has_more) {
    page = await call({ queue: 'loops', limit: 7, cursor: page.next_cursor });
    all.push(...page.items);
  }
  assert.equal(all.length, 62);
  assert.equal(new Set(all.map(row => row.number)).size, 62);
  assert.equal(page.remaining, 0);
});
test('loop cursor is stable for ties, survives deleted anchor, and rejects another partner or queue', async () => {
  await seedLoops([['a', 'joe', '2026-01-01'], ['b', 'dell', '2026-01-01'], ['c', 'joe', null]]);
  const first = await call({ queue: 'loops', limit: 1 });
  assert.deepEqual(await call({ queue: 'loops', limit: 1 }), first);
  await db.query("delete from loop_item where number = 'a'");
  const next = await call({ queue: 'loops', limit: 1, cursor: first.next_cursor });
  assert.equal(next.items[0].number, 'b');
  await assert.rejects(executeRegisteredTool(db, { ...JOE, slug: 'dell' }, 'morning-brief-queue',
    { queue: 'loops', cursor: first.next_cursor }), ToolError);
  await assert.rejects(call({ queue: 'needs_joe', cursor: first.next_cursor }), ToolError);
});
test('queue rejects audience selection, unknown context and malformed cursors before reading', async () => {
  for (const args of [{ queue: 'loops', sponsor: 'dell' }, { queue: 'loops', limit: 0 },
    { queue: 'loops', cursor: 'not-a-cursor' }]) await assert.rejects(call(args), ToolError);
  await assert.rejects(executeRegisteredTool(db, { slug: 'claude', human: false },
    'morning-brief-queue', { queue: 'loops' }), ToolError);
});

test('Needs Joe pages after exclusions and duplicate collapse, preserving partial source evidence', async () => {
  await seedLoops(Array.from({ length: 51 }, (_, i) => [`decision-${i}`, 'joe', null]));
  const first = await call({ queue: 'needs_joe' });
  assert.equal(first.total, 51);
  assert.equal(first.returned, 50);
  assert.equal(first.remaining, 1);
  const last = await call({ queue: 'needs_joe', cursor: first.next_cursor });
  assert.equal(last.items.length, 1);
  assert.equal(new Set([...first.items, ...last.items].map(row => row.key)).size, 51);
  assert.equal(last.remaining, 0);
  assert.equal(first.state, 'partial');
  assert.equal(first.total_scope, 'available_sources');
});
