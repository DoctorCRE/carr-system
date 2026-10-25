import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { connectionRouteForTool } from '../src/mcp.js';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';

const subject = 'aa000000-0000-4000-8000-000000000901';
const joe = { id: 'aa000000-0000-4000-8000-000000000001', slug: 'joe', human: true };
const agent = { id: 'aa000000-0000-4000-8000-000000000002', slug: 'codex', human: false };
let fixture, c;
before(async () => {
  fixture = await postgresDatabase('next-action-');
  c = fixture.client;
  const schema = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
  for (const name of ['actor', 'next_action', 'event', 'tool_call']) {
    const definition = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))?.[0];
    assert.ok(definition, name);
    await c.query(definition);
  }
  await restoreEventIdentity(c, schema);
  await c.query(schema.match(/CREATE FUNCTION public\.trg_touch_row\(\) RETURNS trigger[\s\S]*?end \$\$;/)[0]);
  await c.query('create trigger next_action_touch before update on next_action for each row execute function trg_touch_row()');
  await c.query("create unique index one_open_next_action_per_owner on next_action(subject_type,subject_id,owner_id) where status='open' and item_title is null");
  await c.query('alter table tool_call add primary key(idempotency_key)');
  await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic owner'),($2,'codex','automation','Synthetic runtime')", [joe.id, agent.id]);
  await c.query(`create view v_ref_index as select 'client'::text subject_type, '${subject}'::uuid subject_id, 'C-901'::text ref`);
});
after(async () => fixture?.close());

async function command(name, args, identity = agent, client = c) {
  await client.query('begin');
  try {
    const result = await executeRegisteredTool(client, identity, name, { ...(TOOLS[name]?.write ? { idempotency_key: randomUUID() } : {}), ...args });
    await client.query('commit');
    return result;
  } catch (error) {
    await client.query('rollback');
    throw error;
  }
}
const set = (description, extra = {}, identity = joe) => command('set-next-action', { ref: 'C-901', description, ...extra }, identity);
const read = () => command('get-next-action', { ref: 'C-901', owner: 'joe' });
const conflict = error => error.payload?.error === 'next_action_conflict';
const reset = () => c.query("update next_action set status='done' where status='open'");

test('canonical action remains visible behind 35 newer timeline entries and a hold', async () => {
  await reset();
  const action = await set('Preserve the current obligation');
  await c.query("update next_action set hold_until=current_date+7 where id=$1", [action.next_action_id]);
  for (let n = 0; n < 35; n++) {
    await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,cause) values(clock_timestamp(),$1,'log-activity','client',$2,'system')", [joe.id, subject]);
  }
  const current = await read();
  assert.equal(current.owner, 'joe');
  assert.equal(current.action.id, action.next_action_id);
  assert.equal(current.action.version, 2);
  assert.ok(current.action.updated_at);
});

test('stale empty state cannot replace an intervening human action', async () => {
  await reset();
  const snapshot = await read();
  assert.equal(snapshot.action, null);
  const human = await set('Keep the human decision');
  await assert.rejects(set('Automation guessed there was no action', { owner: 'joe', expected_action: snapshot.action }, agent), conflict);
  assert.equal((await read()).action.id, human.next_action_id);
});

test('a changed version cannot be replaced even when the action id is unchanged', async () => {
  await reset();
  const human = await set('Keep the revised human decision');
  await c.query("update next_action set due_on=current_date+1 where id=$1", [human.next_action_id]);
  await assert.rejects(set('Stale automated replacement', {
    owner: 'joe', expected_action: { id: human.next_action_id, version: 1 },
  }, agent), conflict);
  assert.equal((await read()).action.id, human.next_action_id);
});


test('canonical reads use the writer read-only route and work with its table grants', async () => {
  assert.equal(connectionRouteForTool(TOOLS['get-next-action']), 'writer_read_only');
  await c.query('create role carr_writer');
  await c.query('grant select on actor, next_action, v_ref_index to carr_writer');
  await c.query('set role carr_writer');
  try { assert.equal((await read()).owner, 'joe'); }
  finally { await c.query('reset role'); }
});

test('guarded creation and replacement replay without replacing the replacement', async () => {
  await reset();
  const args = { ref: 'C-901', description: 'Synthetic automated action', owner: 'joe', expected_action: null, idempotency_key: randomUUID() };
  const first = await command('set-next-action', args);
  assert.equal((await read()).action.id, first.next_action_id);
  const snapshot = (await read()).action;
  const replacementArgs = { ...args, description: 'Replacement action', expected_action: { id: snapshot.id, version: snapshot.version }, idempotency_key: randomUUID() };
  const replacement = await command('set-next-action', replacementArgs);
  const replay = await command('set-next-action', replacementArgs);
  assert.equal(replay.replayed, true);
  assert.equal(replay.next_action_id, replacement.next_action_id);
  assert.equal((await read()).action.id, replacement.next_action_id);
});

test('concurrent empty-snapshot writers cannot overwrite each other', async () => {
  await reset();
  const other = new pg.Client(c.connectionParameters);
  await other.connect();
  try {
    const args = { ref: 'C-901', description: 'Concurrent automation', owner: 'joe', expected_action: null };
    const results = await Promise.allSettled([
      command('set-next-action', args), command('set-next-action', args, agent, other),
    ]);
    assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
    const rejected = results.find(r => r.status === 'rejected');
    assert.ok(conflict(rejected.reason));
    const accepted = results.find(r => r.status === 'fulfilled').value;
    assert.equal((await read()).action.id, accepted.next_action_id);
  } finally { await other.end(); }
});

test('concurrent same-key guarded creation returns one replay', async () => {
  await reset();
  const other = new pg.Client(c.connectionParameters);
  await other.connect();
  try {
    const args = { ref: 'C-901', description: 'One operation', owner: 'joe', expected_action: null, idempotency_key: randomUUID() };
    const results = await Promise.all([
      command('set-next-action', args), command('set-next-action', args, agent, other),
    ]);
    assert.equal(results[0].next_action_id, results[1].next_action_id);
    assert.equal(results.filter(r => r.replayed === true).length, 1);
  } finally { await other.end(); }
});
