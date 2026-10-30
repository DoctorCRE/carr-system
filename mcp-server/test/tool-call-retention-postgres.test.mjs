// Proves the tool_call replay window, its bounded retention, and the bounded
// publish-board-snapshot reply on a disposable PostgreSQL built as deploy
// builds it: db/schema.sql, then every pending migration in filename order.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { migrationNames, readMigration } from './migration-source.testhelper.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { durableReceipt } from '../src/replay-receipt.ts';

const MIGRATION = migrationNames().find(name => /^\d{4}_tool_call_retention\.sql$/.test(name));

let fixture, c, actor;
before(async () => {
  assert.ok(MIGRATION, 'the retention migration exists');
  const bin = [process.env.CARR_LOCAL_PG_BIN_DIR, '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin',
    '/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin'].find(p => existsSync(`${p}/initdb`));
  assert.ok(bin, 'PostgreSQL is required for the isolated retention proof');
  fixture = await acquireDisposablePostgres({ prefix: 'tool-call-retention-', pgCtl: `${bin}/pg_ctl` });
  await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
  await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
  await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
  c = new pg.Client({ host: fixture.root, user: 'carr_ci', database: 'carr_ci' });
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', '-X', '-v', 'ON_ERROR_STOP=1', '-q',
    '-f', new URL('../../db/schema.sql', import.meta.url).pathname]);
  const applied = new Set((await c.query('select filename from schema_migrations')).rows.map(r => r.filename));
  const pending = migrationNames().filter(name => !applied.has(name)).sort();
  const at = pending.indexOf(MIGRATION);
  assert.ok(at >= 0, 'the retention migration is pending over the snapshot');
  for (const name of pending.slice(0, at)) await c.query(readMigration(name));
  actor = { ...(await c.query(`insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner')
    on conflict(slug) do update set active=true returning id,slug`)).rows[0], human: true, client_id: 'synthetic-client' };
  // Rows the migration must clean up or keep, seeded before it runs.
  await c.query(`insert into tool_call (idempotency_key, verb, actor_id, request_hash, response, created_at)
    select 'old-' || g, 'add-room-turn', $1::uuid, 'h', '{}'::jsonb, now() - interval '40 days' from generate_series(1, 25) g
    union all select 'attempt-' || g, 'ask-jev-attempt', $1::uuid, 'h', '{}'::jsonb, now() from generate_series(1, 25) g
    union all select 'fresh-' || g, 'add-room-turn', $1::uuid, 'h', '{}'::jsonb, now() - interval '29 days' from generate_series(1, 5) g
    union all select 'rw02-missing-joe:' || md5(g::text), 'add-loop', $1::uuid, 'h', '{}'::jsonb, now() - interval '90 days' from generate_series(1, 2) g
    union all select 'calcap-' || g, 'log-activity', $1::uuid, 'h', '{}'::jsonb, now() - interval '90 days' from generate_series(1, 2) g
    union all select gen_random_uuid()::text, 'review-and-triage', $1::uuid, 'h', '{}'::jsonb, now() - interval '90 days' from generate_series(1, 2) g`,
  [actor.id]);
  await c.query(readMigration(MIGRATION));
  for (const name of pending.slice(at + 1)) await c.query(readMigration(name));
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });

async function call(name, args) {
  await c.query('begin');
  try {
    await c.query('set local role carr_writer');
    const result = await executeRegisteredTool(c, actor, name, args);
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}

const count = async (where, params = []) =>
  Number((await c.query(`select count(*) from tool_call where ${where}`, params)).rows[0].count);

test('the migration removes expired and attempt rows and keeps rows the database reads back', async () => {
  assert.equal(await count(`idempotency_key like 'old-%'`), 0);
  assert.equal(await count(`verb = 'ask-jev-attempt'`), 0);
  assert.equal(await count(`idempotency_key like 'fresh-%'`), 5);
  assert.equal(await count(`idempotency_key like 'rw02-%' or idempotency_key like 'calcap-%' or verb = 'review-and-triage'`), 6);
});

test('publish-board-snapshot replies with its board and version, never the snapshot', async () => {
  const key = randomUUID();
  const args = { idempotency_key: key, board_id: 'retention-proof', base_version: 0, snapshot: { title: 'x'.repeat(20000) } };
  const first = await call('publish-board-snapshot', args);
  assert.deepEqual(first, { ok: true, board_id: 'retention-proof', version: 1 });
  const stored = (await c.query('select response from tool_call where idempotency_key=$1', [key])).rows[0];
  assert.ok(JSON.stringify(stored.response).length < 512, 'the receipt stays bounded');
  assert.deepEqual(await call('publish-board-snapshot', args), { replayed: true, ...first });
});

test('a key replays inside 30 days and is unknown after them', async () => {
  const key = randomUUID();
  const args = { idempotency_key: key, board_id: 'window-proof', base_version: 0, snapshot: { n: 1 } };
  assert.equal((await call('publish-board-snapshot', args)).version, 1);
  await c.query(`update tool_call set created_at = now() - interval '29 days' where idempotency_key=$1`, [key]);
  assert.equal((await call('publish-board-snapshot', args)).replayed, true, 'day 29 still replays');
  await c.query(`update tool_call set created_at = now() - interval '31 days' where idempotency_key=$1`, [key]);
  // Unknown key: the write runs as new, and a stale base version refuses as it would for any new key.
  await assert.rejects(() => call('publish-board-snapshot', args),
    error => error.payload?.error === 'board_version_conflict');
  // A different request under the expired key is not key_reuse; it runs and takes the key.
  const next = await call('publish-board-snapshot', { ...args, base_version: 1, snapshot: { n: 2 } });
  assert.deepEqual(next, { ok: true, board_id: 'window-proof', version: 2 });
  const row = (await c.query(`select created_at > now() - interval '1 minute' as renewed from tool_call where idempotency_key=$1`, [key])).rows[0];
  assert.equal(row.renewed, true);
});

test('each write deletes a bounded batch of expired rows and never a durable one', async () => {
  // Seed without the trigger, which would otherwise sweep the seed rows as they land.
  await c.query('alter table tool_call disable trigger tool_call_expire');
  await c.query(`insert into tool_call (idempotency_key, verb, actor_id, request_hash, response, created_at)
    select 'sweep-' || g, 'add-room-turn', $1::uuid, 'h', '{}'::jsonb, now() - interval '31 days' - g * interval '1 second'
      from generate_series(1, 150) g
    union all select 'rw02-keep-' || g, 'add-loop', $1::uuid, 'h', '{}'::jsonb, now() - interval '400 days' from generate_series(1, 3) g`,
  [actor.id]);
  await c.query('alter table tool_call enable trigger tool_call_expire');
  assert.equal(await count(`idempotency_key like 'sweep-%'`), 150);
  await call('publish-board-snapshot', { idempotency_key: randomUUID(), board_id: 'sweep-proof', base_version: 0, snapshot: {} });
  assert.equal(await count(`idempotency_key like 'sweep-%'`), 130, 'one write removes at most 20 expired rows, oldest first');
  assert.equal(await count(`idempotency_key = any($1)`, [Array.from({ length: 20 }, (_, i) => `sweep-${131 + i}`)]), 0,
    'the 20 oldest went first');
  for (let i = 2; i <= 8; i++)
    await call('publish-board-snapshot', { idempotency_key: randomUUID(), board_id: `sweep-proof-${i}`, base_version: 0, snapshot: {} });
  assert.equal(await count(`idempotency_key like 'sweep-%'`), 0);
  assert.equal(await count(`idempotency_key like 'rw02-keep-%'`), 3);
});

test('the database and the server agree on which receipts are durable', async () => {
  const uuid = '00000000-0000-4000-8000-000000000001';
  const cases = [['add-loop', 'rw02-missing-joe:' + 'a'.repeat(32)], ['add-loop', 'rw02-unknown-to-carr:' + 'c'.repeat(32) + ':e2'],
    ['record-salesforce-page-stop', `rw02-read:${'b'.repeat(24)}:page-stop:3`], ['record-finding', 'rw02-absent:' + 'd'.repeat(32)],
    ['record-finding', uuid], ['review-and-triage', uuid],
    ['accept-ready-plan', uuid], ['accept-outcome-feedback', uuid], ['add-loop', uuid], ['publish-board-snapshot', uuid],
    ['ask-jev-attempt', 'jev-attempt-1']];
  const { predicate } = (await c.query(`select pg_get_expr(indpred, indrelid) as predicate from pg_index
    where indexrelid = 'public.tool_call_expiry_idx'::regclass`)).rows[0];
  for (const [verb, key] of cases) {
    const { expiring } = (await c.query(`select ${predicate} as expiring
      from (select $1::text as verb, $2::text as idempotency_key) t`, [verb, key])).rows[0];
    assert.equal(!expiring, durableReceipt(verb, key), `${verb} ${key}`);
  }
});
