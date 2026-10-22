import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import pg from 'pg';
import { executeRegisteredTool, ToolError } from '../src/tools.js';
import { acquireDisposablePostgres, acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';

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
  db = new pg.Client({ host: fixture.root, user: 'fixture', database: 'postgres' });
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

test('morning brief filters autonomous backlog before its page and places imminent critical dates first', async () => {
  await rows([
    ...Array.from({ length: 55 }, (_, i) => [i + 1, 'next_action', 'claude', '2020-01-01']),
    [61, 'next_action', 'joe-local', '2026-01-01'],
    [62, 'post_call_action', 'dell-local', '2026-01-01'],
    [63, 'critical_date', null, '2026-12-01'],
  ]);
  const result = await executeRegisteredTool(db, JOE, 'morning-brief', {});
  assert.deepEqual(result.sections.today.items.map(row => row.id), [id(63), id(61), id(62)]);
  assert.deepEqual(result.sections.today.items.map(row => row.owner_partner), [null, 'joe', 'dell']);
  assert.equal(result.sections.today.total, 3);
  assert.equal(result.sections.today.has_more, false);
  assert.equal(result.sections.today.next_cursor, null);
});
