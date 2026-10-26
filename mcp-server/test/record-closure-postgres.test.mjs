import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';

const joe = { id: randomUUID(), slug: 'joe', human: true };
const dell = { id: randomUUID(), slug: 'dell', human: true };
const retired = { id: randomUUID(), slug: 'hermes-pilot', human: false };
let fixture, c;
before(async () => {
  fixture = await postgresDatabase('record-closure-');
  c = fixture.client;
  const schema = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
  for (const name of ['actor', 'next_action', 'critical_date', 'event', 'tool_call']) {
    const definition = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))?.[0];
    assert.ok(definition, name);
    await c.query(definition);
  }
  await c.query('begin');
  await c.query(readFileSync(new URL('../../migrations/0989_critical_date_resolution.sql', import.meta.url), 'utf8'));
  await c.query('commit');
  await restoreEventIdentity(c, schema);
  await c.query(schema.match(/CREATE FUNCTION public\.trg_touch_row\(\) RETURNS trigger[\s\S]*?end \$\$;/)[0]);
  await c.query('create trigger next_action_touch before update on next_action for each row execute function trg_touch_row()');
  await c.query('create trigger critical_date_touch before update on critical_date for each row execute function trg_touch_row()');
  await c.query('alter table tool_call add primary key(idempotency_key)');
  for (const actor of [joe, dell, retired]) await c.query('insert into actor(id,slug,kind,display_name,active) values($1,$2,$3,$2,$4)',
    [actor.id, actor.slug, actor.human ? 'human' : 'automation', actor !== retired]);
});
after(async () => fixture?.close());

async function command(name, args, actor = joe, client = c) {
  await client.query('begin');
  try {
    const result = await executeRegisteredTool(client, actor, name, { idempotency_key: randomUUID(), ...args });
    await client.query('commit');
    return result;
  } catch (error) {
    await client.query('rollback');
    throw error;
  }
}
async function action(type = 'deal') {
  return (await c.query(`insert into next_action(subject_type,subject_id,owner_id,description,created_by)
    values($1,$2,$3,'Synthetic obsolete obligation',$3) returning *`, [type, randomUUID(), retired.id])).rows[0];
}

test('a partner drops exactly the retired seat action without creating a replacement', async () => {
  const row = await action();
  const other = await action();
  const before = (await c.query('select count(*)::int as count from next_action')).rows[0].count;
  const result = await command('drop-action', { action_id: row.id, base_version: row.version, reason: 'No longer needed' });
  assert.equal(result.ok, true);
  assert.equal(result.action_id, row.id);
  const saved = (await c.query('select status,version,updated_by from next_action where id=$1', [row.id])).rows[0];
  assert.deepEqual(saved, { status: 'dropped', version: 2, updated_by: joe.id });
  assert.equal((await c.query('select status from next_action where id=$1', [other.id])).rows[0].status, 'open');
  assert.equal((await c.query('select count(*)::int as count from next_action')).rows[0].count, before);
  const events = (await c.query('select actor_id,old_value as old,new_value as new,human_quote from event where verb=$1 and subject_id=$2', ['drop-action', row.subject_id])).rows;
  assert.equal(events.length, 1);
  assert.equal(events[0].actor_id, joe.id);
  assert.equal(events[0].old.status, 'open');
  assert.equal(events[0].new.status, 'dropped');
  assert.equal(events[0].human_quote, 'No longer needed');
});

async function criticalDate() {
  return (await c.query(`insert into critical_date(deal_id,kind,due_on,source,created_by)
    values($1,'option_window','2026-01-01','Synthetic fixture',$2) returning *`, [randomUUID(), retired.id])).rows[0];
}

test('Dell resolves a critical date as passed and records the reason in deal history', async () => {
  const row = await criticalDate();
  const result = await command('resolve-critical-date', { critical_date_id: row.id, resolution: 'passed', reason: 'Window elapsed' }, dell);
  assert.equal(result.ok, true);
  assert.equal(result.critical_date_id, row.id);
  const saved = (await c.query('select status,version,updated_by from critical_date where id=$1', [row.id])).rows[0];
  assert.deepEqual(saved, { status: 'passed', version: 2, updated_by: dell.id });
  const events = (await c.query('select actor_id,old_value as old,new_value as new,human_quote from event where verb=$1 and subject_id=$2', ['resolve-critical-date', row.deal_id])).rows;
  assert.equal(events.length, 1);
  assert.equal(events[0].actor_id, dell.id);
  assert.equal(events[0].old.status, 'open');
  assert.equal(events[0].new.status, 'passed');
  assert.equal(events[0].human_quote, 'Window elapsed');
});

const refuses = code => error => error.payload?.error === code;

test('both partners can drop retired-owner actions for every supported subject', async () => {
  for (const type of ['deal', 'client', 'lead', 'vendor']) {
    const row = await action(type);
    await command('drop-action', { action_id: row.id, base_version: 1, reason: 'Retired obligation' }, dell);
    assert.equal((await c.query('select status from next_action where id=$1', [row.id])).rows[0].status, 'dropped');
    assert.equal((await c.query('select subject_type from event where verb=$1 and subject_id=$2', ['drop-action', row.subject_id])).rows[0].subject_type, type);
  }
});

test('drop refuses missing, stale, and already terminal actions', async () => {
  await assert.rejects(command('drop-action', { action_id: randomUUID(), base_version: 1, reason: 'Missing' }), refuses('action_not_found'));
  const row = await action();
  await assert.rejects(command('drop-action', { action_id: row.id, base_version: 2, reason: 'Stale' }), refuses('version_conflict'));
  assert.equal((await c.query('select status from next_action where id=$1', [row.id])).rows[0].status, 'open');
  for (const status of ['done', 'dropped']) {
    await c.query('update next_action set status=$2 where id=$1', [row.id, status]);
    const version = (await c.query('select version from next_action where id=$1', [row.id])).rows[0].version;
    await assert.rejects(command('drop-action', { action_id: row.id, base_version: version, reason: 'Already terminal' }), refuses('action_not_open'));
  }
});

test('drop validates IDs, action versions, and reasons with named refusals', async () => {
  const row = await action();
  const valid = { action_id: row.id, base_version: 1, reason: 'Obsolete' };
  for (const [field, value, code] of [
    ['action_id', 'wrong', 'invalid_action_id'],
    ['base_version', 0, 'invalid_base_version'],
    ['base_version', 1.5, 'invalid_base_version'],
    ['reason', '   ', 'invalid_reason'],
  ]) await assert.rejects(command('drop-action', { ...valid, [field]: value }), refuses(code));
  assert.equal((await c.query('select status from next_action where id=$1', [row.id])).rows[0].status, 'open');
});

test('drop retry replays its receipt and a changed reason cannot reuse its key', async () => {
  const row = await action();
  const args = { idempotency_key: randomUUID(), action_id: row.id, base_version: 1, reason: 'No longer applies' };
  const first = await command('drop-action', args);
  assert.deepEqual(await command('drop-action', args), { replayed: true, ...first });
  await assert.rejects(command('drop-action', { ...args, reason: 'A different request' }), refuses('key_reuse'));
  await assert.rejects(command('drop-action', args, dell), refuses('key_reuse'));
  assert.equal((await c.query('select count(*)::int as count from event where idempotency_key=$1', [args.idempotency_key])).rows[0].count, 1);
});

test('unverified and unattended callers cannot reach either closure handler', async () => {
  const database = { query: () => { throw new Error('unauthorized database access'); } };
  for (const name of ['drop-action', 'resolve-critical-date']) {
    assert.ok(TOOLS[name]?.humanOnly);
    for (const actor of [retired, { ...retired, slug: 'claude', sponsoring_human_slug: 'joe' }, { ...joe, slug: 'outsider' }]) {
      await assert.rejects(executeRegisteredTool(database, actor, name, {}), refuses('human_only_verb_requires_verified_partner'));
      await assert.rejects(TOOLS[name].handler(database, actor, {}), refuses('partner_required'));
    }
    await assert.rejects(executeRegisteredTool(database, { ...joe, unattended: true }, name, {}), refuses('unattended_runner_authority_not_admitted'));
  }
});


test('concurrent identical action drops produce one mutation and one replay', async () => {
  const row = await action();
  const args = { idempotency_key: randomUUID(), action_id: row.id, base_version: 1, reason: 'One intended drop' };
  const other = new pg.Client(c.connectionParameters);
  await other.connect();
  try {
    const results = await Promise.all([command('drop-action', args), command('drop-action', args, joe, other)]);
    assert.equal(results.filter(result => result.replayed).length, 1);
    assert.equal((await c.query('select version from next_action where id=$1', [row.id])).rows[0].version, 2);
    assert.equal((await c.query('select count(*)::int as count from event where idempotency_key=$1', [args.idempotency_key])).rows[0].count, 1);
  } finally { await other.end(); }
});

test('an event failure rolls back an action drop and the same key can retry', async () => {
  const row = await action();
  const args = { idempotency_key: randomUUID(), action_id: row.id, base_version: 1, reason: 'Retry after failure' };
  await c.query("alter table event add constraint synthetic_event_failure check (verb <> 'drop-action') not valid");
  try { await assert.rejects(command('drop-action', args)); }
  finally { await c.query('alter table event drop constraint synthetic_event_failure'); }
  assert.deepEqual((await c.query('select status,version from next_action where id=$1', [row.id])).rows[0], { status: 'open', version: 1 });
  assert.equal((await c.query('select count(*)::int as count from tool_call where idempotency_key=$1', [args.idempotency_key])).rows[0].count, 0);
  assert.equal((await command('drop-action', args)).ok, true);
});

test('verified sponsored local callers retain their runtime identity in closure history', async () => {
  const local = { id: randomUUID(), slug: 'joe-local', human: false, sponsoring_human_slug: 'joe', native_agent_verified: true, via: 'local-token', client_id: 'local-verb' };
  await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe-local','automation','Synthetic local caller')", [local.id]);
  const row = await action();
  await command('drop-action', { action_id: row.id, base_version: 1, reason: 'Partner-directed closure' }, local);
  const history = (await c.query("select actor_id,sponsoring_human_slug from event where verb='drop-action' and subject_id=$1", [row.subject_id])).rows[0];
  assert.deepEqual(history, { actor_id: local.id, sponsoring_human_slug: 'joe' });
});

test('critical-date done and dropped resolutions are supported by the forward migration', async () => {
  for (const resolution of ['done', 'dropped']) {
    const row = await criticalDate();
    const result = await command('resolve-critical-date', { critical_date_id: row.id, resolution, reason: 'Synthetic explicit resolution' });
    assert.equal(result.status, resolution);
    assert.equal((await c.query('select status from critical_date where id=$1', [row.id])).rows[0].status, resolution);
  }
});

test('critical-date resolution refuses missing and every terminal date without changing siblings', async () => {
  await assert.rejects(command('resolve-critical-date', { critical_date_id: randomUUID(), resolution: 'done', reason: 'Missing' }), refuses('critical_date_not_found'));
  const sibling = await criticalDate();
  for (const status of ['passed', 'cleared', 'dropped', 'done']) {
    const row = await criticalDate();
    await c.query('update critical_date set status=$2 where id=$1', [row.id, status]);
    await assert.rejects(command('resolve-critical-date', { critical_date_id: row.id, resolution: 'done', reason: 'Already closed' }), refuses('critical_date_not_open'));
    assert.equal((await c.query('select status from critical_date where id=$1', [row.id])).rows[0].status, status);
  }
  assert.equal((await c.query('select status from critical_date where id=$1', [sibling.id])).rows[0].status, 'open');
});

test('critical-date resolution validates the ID, resolution, and reason', async () => {
  const row = await criticalDate();
  const valid = { critical_date_id: row.id, resolution: 'passed', reason: 'Elapsed' };
  for (const [field, value, code] of [
    ['critical_date_id', 'wrong', 'invalid_critical_date_id'],
    ['resolution', 'cleared', 'value_not_in_declared_vocabulary'],
    ['resolution', 'open', 'value_not_in_declared_vocabulary'],
    ['reason', '  ', 'invalid_reason'],
  ]) await assert.rejects(command('resolve-critical-date', { ...valid, [field]: value }), refuses(code));
  assert.equal((await c.query('select status from critical_date where id=$1', [row.id])).rows[0].status, 'open');
});

test('concurrent identical date resolutions replay once and reject changed payloads', async () => {
  const row = await criticalDate();
  const args = { idempotency_key: randomUUID(), critical_date_id: row.id, resolution: 'done', reason: 'Deadline satisfied' };
  const other = new pg.Client(c.connectionParameters);
  await other.connect();
  try {
    const results = await Promise.all([command('resolve-critical-date', args), command('resolve-critical-date', args, joe, other)]);
    assert.equal(results.filter(result => result.replayed).length, 1);
    const first = results.find(result => !result.replayed);
    assert.deepEqual(await command('resolve-critical-date', args), { replayed: true, ...first });
    await assert.rejects(command('resolve-critical-date', { ...args, resolution: 'dropped' }), refuses('key_reuse'));
    await assert.rejects(command('resolve-critical-date', { ...args, reason: 'Another request' }), refuses('key_reuse'));
    await assert.rejects(command('resolve-critical-date', args, dell), refuses('key_reuse'));
    await assert.rejects(command('drop-action', { idempotency_key: args.idempotency_key, action_id: randomUUID(), base_version: 1, reason: 'Different verb' }), refuses('key_reuse'));
    assert.equal((await c.query('select count(*)::int as count from event where idempotency_key=$1', [args.idempotency_key])).rows[0].count, 1);
  } finally { await other.end(); }
});

test('competing resolutions cannot overwrite the winning terminal status', async () => {
  const row = await criticalDate();
  const other = new pg.Client(c.connectionParameters);
  await other.connect();
  try {
    const args = { critical_date_id: row.id, reason: 'Partner decision' };
    const results = await Promise.allSettled([
      command('resolve-critical-date', { ...args, resolution: 'passed' }),
      command('resolve-critical-date', { ...args, resolution: 'dropped' }, dell, other),
    ]);
    assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
    assert.ok(refuses('critical_date_not_open')(results.find(result => result.status === 'rejected').reason));
    const winner = results.find(result => result.status === 'fulfilled').value;
    assert.deepEqual((await c.query('select status,version from critical_date where id=$1', [row.id])).rows[0], { status: winner.status, version: 2 });
    assert.equal((await c.query("select count(*)::int as count from event where verb='resolve-critical-date' and subject_id=$1", [row.deal_id])).rows[0].count, 1);
  } finally { await other.end(); }
});

test('an event failure rolls back date resolution and permits retry with the same key', async () => {
  const row = await criticalDate();
  const args = { idempotency_key: randomUUID(), critical_date_id: row.id, resolution: 'dropped', reason: 'Retry after failure' };
  await c.query("alter table event add constraint synthetic_event_failure check (verb <> 'resolve-critical-date') not valid");
  try { await assert.rejects(command('resolve-critical-date', args)); }
  finally { await c.query('alter table event drop constraint synthetic_event_failure'); }
  assert.deepEqual((await c.query('select status,version from critical_date where id=$1', [row.id])).rows[0], { status: 'open', version: 1 });
  assert.equal((await c.query('select count(*)::int as count from tool_call where idempotency_key=$1', [args.idempotency_key])).rows[0].count, 0);
  assert.equal((await command('resolve-critical-date', args)).ok, true);
});
