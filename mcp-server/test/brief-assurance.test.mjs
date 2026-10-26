import test, { before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import pg from 'pg';
import { readMigration } from './migration-source.testhelper.mjs';
import { readBriefAssurance } from '../src/brief-assurance.ts';
import { executeRegisteredTool, ToolError, TOOLS } from '../src/tools.js';
import { connectionRouteForTool } from '../src/mcp.js';
import { acquireDisposablePostgres, acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';

let bin;
try { bin = execFileSync('pg_config', ['--bindir'], { encoding: 'utf8' }).trim(); } catch {}
if (!bin || !existsSync(path.join(bin, 'postgres'))) {
  bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin', '/usr/lib/postgresql/16/bin']
    .find(candidate => existsSync(path.join(candidate, 'postgres')));
}
let fixture, db, release;
const id = n => `aa000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
before(async () => {
  assert.ok(bin, 'PostgreSQL is required for the assurance queue proof');
  release = await acquirePostgresFixtureGroup();
  fixture = await acquireDisposablePostgres({ prefix: 'brief-assurance-', pgCtl: path.join(bin, 'pg_ctl') });
  const data = path.join(fixture.root, 'data');
  await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
  await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'), '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
  db = new pg.Client({ host: fixture.root, user: 'fixture', database: 'postgres' });
  await db.connect();
  await db.query(`create table loop_item (number text, kind text, status text, owner text, domain text, marker text,
      blocker_class text, blocker_detail text, unblocks text, created_at timestamptz, title text, body text);
    create role carr_writer nologin;
    create schema ops;
    create function ops.read_governance_queue() returns jsonb language sql as $$select '{}'::jsonb$$;
    create table actor(id uuid primary key, slug text, kind text, active boolean);
    create table signal_event(id uuid primary key, producer text, signal_kind text);
    create table ops.notification(id uuid primary key, recipient_actor uuid, event_ref uuid,
      event_source text, reason text, severity text, subject_type text, subject_ref text,
      deep_link text, created_at timestamptz);
    create table ops.notification_read(notification_id uuid, recipient_actor uuid);
    create table ops.notification_delivery(notification_id uuid, state text, held_until timestamptz);
    grant usage on schema ops to carr_writer;
    grant select on actor, signal_event, ops.notification, ops.notification_read, ops.notification_delivery to carr_writer;
    insert into actor values ('${id(1)}','joe','human',true), ('${id(2)}','dell','human',true),
      ('${id(3)}','codex','automation',true);
    insert into signal_event values ('${id(1)}','v5-a05-delivery-cadence','synthetic-alert'),
      ('${id(2)}','unrelated-producer','synthetic-other');`);
  const resolver = readMigration(new URL('../../migrations/0223_doctrine_search_reads_without_writing_or_reading_actor.sql', import.meta.url).pathname)
    .match(/create or replace function retrieval_visibility_actor_id[\s\S]*?\$\$;/i)[0];
  await db.query(resolver);
  await db.query('grant execute on function retrieval_visibility_actor_id(text) to carr_writer');
});
after(async () => { await db?.end(); await fixture?.close(); await release?.(); });
beforeEach(async () => {
  await db.query('truncate ops.notification, ops.notification_read, ops.notification_delivery');
  await db.query("update actor set active=true");
});
async function add(n, { recipient = 1, event = 1, source = 'signal_event', severity = 'action_required', at = '2026-10-01T00:00:00Z' } = {}) {
  await db.query(`insert into ops.notification values($1,$2,$3,$4,'Synthetic attention',$5,'deal','D-SYNTHETIC','/leads',$6)`,
    [id(n), id(recipient), id(event), source, severity, at]);
}
async function read(sponsor, load = () => readBriefAssurance(db, sponsor)) {
  await db.query('begin read only');
  try {
    await db.query('set local role carr_writer');
    assert.equal((await db.query('show transaction_read_only')).rows[0].transaction_read_only, 'on');
    return await load();
  } finally { await db.query('rollback'); }
}

const JOE = { slug: 'joe', human: true, via: 'oauth-google', client_id: 'fixture' };
const DELL = { ...JOE, slug: 'dell' };
const queue = (args = {}, actor = JOE) => read(actor.slug,
  () => executeRegisteredTool(db, actor, 'morning-assurance-queue', args));

test('public assurance queue returns every eligible row across 50+3 pages in stable urgency order', async () => {
  for (let n = 51; n >= 1; n--) await add(n + 100);
  await add(200, { severity: 'failure', at: '2026-10-03T00:00:00Z' });
  await add(201, { severity: 'completion', at: '2026-09-01T00:00:00Z' });
  const first = await queue();
  assert.equal(first.total, 53);
  assert.equal(first.returned, 50);
  assert.equal(first.remaining, 3);
  assert.equal(first.has_more, true);
  const second = await queue({ cursor: first.next_cursor });
  assert.equal(second.total, 53);
  assert.equal(second.returned, 3);
  assert.equal(second.remaining, 0);
  assert.equal(second.has_more, false);
  assert.equal(second.next_cursor, null);
  assert.deepEqual([...first.items, ...second.items].map(row => row.notification_id),
    [id(200), ...Array.from({ length: 51 }, (_, n) => id(n + 101)), id(201)]);
});

test('public assurance cursor cannot cross partner scope and caller cannot select a recipient', async () => {
  await add(101);
  await add(102);
  await add(103, { recipient: 2 });
  const first = await queue({ limit: 1 });
  assert.deepEqual((await queue({}, DELL)).items.map(row => row.notification_id), [id(103)]);
  await assert.rejects(queue({ cursor: first.next_cursor }, DELL),
    error => error instanceof ToolError && error.payload.error === 'brief_queue_cursor_invalid');
  for (const args of [{ recipient: 'dell' }, { sponsor: 'dell' }, { audience: 'dell' }])
    await assert.rejects(queue(args), error => error instanceof ToolError);
});

test('public assurance uses the read-only writer route while other brief reads stay on the reader route', () => {
  assert.equal(TOOLS['morning-assurance-queue'].write, false);
  assert.equal(connectionRouteForTool(TOOLS['morning-assurance-queue']), 'writer_read_only');
  assert.equal(connectionRouteForTool(TOOLS['morning-brief']), 'reader');
  assert.equal(connectionRouteForTool(TOOLS['morning-brief-queue']), 'reader');
});

test('public assurance refuses missing identity and sanitizes failed source details', async () => {
  const broken = { query: async () => { throw Error('private driver detail should never escape'); } };
  await assert.rejects(executeRegisteredTool(broken, JOE, 'morning-assurance-queue', {}), error => {
    assert.equal(error.payload.error, 'source_unavailable');
    assert.doesNotMatch(JSON.stringify(error.payload), /private driver detail/);
    return true;
  });
  await assert.rejects(executeRegisteredTool(broken, { slug: 'unverified' }, 'morning-assurance-queue', {}),
    error => error instanceof ToolError && error.payload.error === 'morning_brief_requires_partner_scope');
  await db.query("update actor set active=false where slug='joe'");
  await assert.rejects(queue(), error => error instanceof ToolError && error.payload.error === 'source_unavailable');
});

test('Needs Joe applies eligibility before paging past 500 excluded rows', async () => {
  await db.query(`insert into loop_item (number, kind, status, owner, created_at, title, blocker_class)
    select n::text, 'open_loop', 'open', 'joe', '2026-01-01'::timestamptz, n::text,
      case when n=501 then 'human_only' else 'counterparty' end from generate_series(0,501) n`);
  const page = await executeRegisteredTool(db, JOE, 'morning-brief-queue', { queue: 'needs_joe' });
  assert.equal(page.total, 1);
  assert.equal(page.count, 1);
  assert.equal(page.items[0].key, 'loop:501');
  assert.equal(page.items[0].records[0], page.items[0].key);
  assert.equal(page.excluded.count, 501);
});
