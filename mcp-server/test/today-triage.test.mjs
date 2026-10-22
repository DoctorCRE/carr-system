import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import pg from 'pg';
import { executeRegisteredTool, ToolError } from '../src/tools.js';
import { acquireDisposablePostgres, acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';
import { morningBriefView } from './fixtures/doctorcre-brief-a753e121/morning-brief-model.js';

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
  fixture = await acquireDisposablePostgres({ prefix: 'today-triage-', pgCtl: path.join(bin, 'pg_ctl') });
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
    create view v_today_triage as select * from triage_fixture`);
});
after(async () => { await db?.end(); await fixture?.close(); await release?.(); });
const id = n => `aa000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
async function rows(values) {
  await db.query('truncate triage_fixture');
  for (const [number, kind, owner, due] of values)
    await db.query(`insert into triage_fixture values ($1,$2,'deal',$2,$3,$4,$5,'Example Practice','D-1',0)`,
      [kind, id(number), owner, `Synthetic ${number}`, due]);
}
const call = args => executeRegisteredTool(db, JOE, 'today-triage', args);

test('due actions reach the exact app brief consumer before a full page of future critical dates', async () => {
  const source = new URL('./fixtures/doctorcre-brief-a753e121/', import.meta.url);
  const manifest = JSON.parse(readFileSync(new URL('source.json', source), 'utf8'));
  assert.equal(manifest.commit, 'a753e12120ba396339c0e874ba309333348a12eb');
  for (const [name, digest] of Object.entries(manifest.files))
    assert.equal(createHash('sha256').update(readFileSync(new URL(name, source))).digest('hex'), digest, name);
  const { today, tomorrow, yesterday } = (await db.query(`select current_date::text as today,
    (current_date + 1)::text as tomorrow, (current_date - 1)::text as yesterday`)).rows[0];
  await rows([
    ...Array.from({ length: 50 }, (_, i) => [i + 1, 'critical_date', null, tomorrow]),
    [61, 'next_action', 'joe-local', today],
    [62, 'next_action', 'dell-local', yesterday],
  ]);
  const brief = await executeRegisteredTool(db, JOE, 'morning-brief', {});
  // Other sections require tables outside this disposable triage fixture.
  brief.sections.deals = { state: 'empty', items: [] };
  brief.sections.loops = { state: 'empty', items: [] };
  const view = morningBriefView(JSON.parse(JSON.stringify(brief)), {
    now: new Date(`${today}T12:00:00`), caughtUp: true,
  });
  assert.deepEqual(view.groups.map(group => group.row.id), [id(62), id(61)]);
  const first = brief.sections.today;
  assert.equal(first.items.length, 50);
  assert.equal(first.total, 52);
  assert.equal(first.has_more, true);
  const next = await call({ owners: 'partners', cursor: first.next_cursor });
  assert.equal(next.items.length, 2);
  assert.equal(next.has_more, false);
  assert.equal(new Set([...first.items, ...next.items].map(row => row.id)).size, 52);
});

test('morning brief filters autonomous backlog before its page and places due actions before imminent dates', async () => {
  const near = (await db.query("select (current_date + 3)::text as day")).rows[0].day;
  await rows([
    ...Array.from({ length: 55 }, (_, i) => [i + 1, 'next_action', 'claude', '2020-01-01']),
    [61, 'next_action', 'joe-local', '2026-01-01'],
    [62, 'post_call_action', 'dell-local', '2026-01-01'],
    [63, 'critical_date', null, near],
  ]);
  const result = await executeRegisteredTool(db, JOE, 'morning-brief', {});
  assert.deepEqual(result.sections.today.items.map(row => row.id), [id(61), id(62), id(63)]);
  assert.deepEqual(result.sections.today.items.map(row => row.owner_partner), ['joe', 'dell', null]);
  assert.equal(result.sections.today.total, 3);
  assert.equal(result.sections.today.has_more, false);
  assert.equal(result.sections.today.next_cursor, null);
});

test('triage pages equal and null dates across kinds without dropping or repeating rows', async () => {
  await rows([
    [1, 'next_action', 'joe', '2020-01-01'],
    [1, 'post_call_action', 'dell', '2020-01-01'],
    [2, 'next_action', null, '2020-01-01'],
    [3, 'ingest', null, null],
    [4, 'next_action', 'dell-local', null],
    [5, 'next_action', 'claude', '2019-01-01'],
  ]);
  const first = await call({ owners: 'partners', limit: 2 });
  assert.equal(first.total, 5);
  assert.equal(first.has_more, true);
  assert.equal(typeof first.next_cursor, 'string');
  const second = await call({ owners: 'partners', limit: 2, cursor: first.next_cursor });
  const third = await call({ owners: 'partners', limit: 2, cursor: second.next_cursor });
  assert.deepEqual([first, second, third].flatMap(page => page.items.map(row => [row.item_kind, row.id])), [
    ['next_action', id(1)], ['next_action', id(2)], ['post_call_action', id(1)], ['ingest', id(3)], ['next_action', id(4)],
  ]);
  for (const page of [first, second, third]) assert.equal(page.total, 5);
  assert.equal(third.has_more, false);
  assert.equal(third.next_cursor, null);
  await assert.rejects(call({ owners: 'all', cursor: first.next_cursor }), error =>
    error instanceof ToolError && error.payload.error === 'triage_cursor_invalid');
  const broad = await call({});
  assert.equal(broad.total, 6);
  assert.equal(broad.items[0].owner, 'claude');
  await db.query("delete from triage_fixture where id <> $1 or item_kind <> 'next_action'", [id(1)]);
  assert.deepEqual(await call({ owners: 'partners', cursor: first.next_cursor }),
    { items: [], total: 1, has_more: false, next_cursor: null });
});

test('imminent critical dates survive more than one page of overdue critical dates', async () => {
  const near = (await db.query("select (current_date + 1)::text as day")).rows[0].day;
  await rows([
    ...Array.from({ length: 55 }, (_, i) => [i + 1, 'critical_date', null, '2020-01-01']),
    [61, 'critical_date', null, near],
    [62, 'next_action', 'joe', '2019-01-01'],
  ]);
  const first = (await executeRegisteredTool(db, JOE, 'morning-brief', {})).sections.today;
  assert.equal(first.items.length, 50);
  assert.equal(first.items[0].id, id(62));
  assert.equal(first.items[1].id, id(61));
  assert.equal(first.total, 57);
  assert.equal(first.has_more, true);
  const next = await call({ owners: 'partners', cursor: first.next_cursor });
  assert.equal(next.items.length, 7);
  assert.equal(new Set([...first.items, ...next.items].map(row => row.id)).size, 57);
  assert.equal(next.total, 57);
  assert.equal(next.has_more, false);
});

test('invalid paging inputs are refused before SQL and empty queues remain complete', async () => {
  const noQuery = { query: () => assert.fail('invalid paging inputs must not reach SQL') };
  for (const args of [
    { limit: 0 }, { limit: 51 }, { limit: 1.5 }, { limit: null }, { owners: null }, { owners: 'someone' },
    { cursor: '' }, { cursor: 'garbage' }, { cursor: btoa('{}') }, { cursor: 7 },
  ]) await assert.rejects(executeRegisteredTool(noQuery, JOE, 'today-triage', args), ToolError);
  await rows([]);
  assert.deepEqual(await call({ owners: 'partners' }), { items: [], total: 0, has_more: false, next_cursor: null });
});


test('malformed cursor dates and keys refuse before SQL', async () => {
  const noQuery = { query: () => assert.fail('malformed cursors must not reach SQL') };
  const valid = { v: 1, owners: 'partners', on: '2026-01-01', rank: 1, due_on: null,
    item_kind: 'next_action', id: id(1) };
  for (const patch of [
    { v: 2 }, { owners: 'all' }, { on: '0000-01-01' }, { due_on: '0000-01-01' },
    { on: '2026-02-30' }, { due_on: '2026-01-01T00:00:00Z' }, { rank: 3 },
    { item_kind: 'unknown' }, { id: 'not-an-id' },
  ]) await assert.rejects(executeRegisteredTool(noQuery, JOE, 'today-triage',
    { owners: 'partners', cursor: btoa(JSON.stringify({ ...valid, ...patch })) }),
    error => error instanceof ToolError && error.payload.error === 'triage_cursor_invalid');
});
