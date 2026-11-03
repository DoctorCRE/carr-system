import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';
import { HOME_REVIEW_CONTRACT } from '../src/home-review.js';

test('Home review lifecycle uses exact current domain owners on PostgreSQL', async t => {
  const fixture = await acquireDisposablePostgres({prefix:'home-review-',snapshot:true});
  const c = new pg.Client(fixture.connection);
  await c.connect();
  t.after(async () => { try { await c.end(); } finally { await fixture.close(); } });
  assert.equal((await c.query('select ops.scac_mutation_catalog_v118_current() current')).rows[0].current,true);
  const actors = {};
  for (const slug of ['joe', 'dell']) actors[slug] = { ...(await c.query(`insert into actor(slug,kind,display_name,email)
    values($1,'human','Synthetic partner',$2) on conflict(slug) do update set email=excluded.email,active=true returning id,slug`,
  [slug, `${slug}@example.test`])).rows[0], human: true };
  const call = async (verb, args = {}, who = actors.joe) => {
    await c.query('begin');
    try {
      if (!TOOLS[verb].write) await c.query('set transaction read only');
      await c.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true)", [who.slug]);
      await c.query('set local role carr_writer');
      const result = await executeRegisteredTool(c, who, verb, args);
      await c.query('commit');
      return result;
    } catch (error) { await c.query('rollback'); throw error; }
  };
  const read = () => call('read-home-inbox-v3');
  const load = item => call('read-home-review', { item_id: item.item_id, source_version: item.source_version });
  const refuses = (promise, error) => assert.rejects(promise, e => e.payload?.error === error);
  const subject = randomUUID();
  const task = (await c.query(`insert into next_action(subject_type,subject_id,owner_id,description,created_by)
    values('client',$1,$2,'Synthetic review task',$2) returning id`, [subject, actors.joe.id])).rows[0].id;
  const duplicate = (await c.query(`insert into next_action(subject_type,subject_id,owner_id,description,created_by)
    values('client',$1,$2,'Synthetic review task',$2) returning id`, [randomUUID(), actors.joe.id])).rows[0].id;
  let item = (await read()).items.find(i => i.source_id === task);
  let operation, receipt;
  await t.test('projected to detail loaded targets one task and changes no domain state', async () => {
    assert.equal((await read()).items.filter(i => [task, duplicate].includes(i.source_id)).length, 2);
    assert.deepEqual(item.review_reference.operations, ['complete-one-action']);
    const before = (await c.query('select * from next_action order by id')).rows;
    operation = (await load(item)).operations[0];
    assert.deepEqual((await c.query('select * from next_action order by id')).rows, before);
  });
  await t.test('detail loaded to owner submitted to refreshed completes one representative', async () => {
    receipt = await call(operation.verb, { ...operation.args, idempotency_key: randomUUID() });
    assert.equal(receipt.ok, true);
    assert.equal((await c.query('select status from next_action where id=$1', [duplicate])).rows[0].status, 'open');
    item = (await read()).items.find(i => i.source_id === task);
    assert.equal(item.source_state, 'done');
    assert.equal(item.review_reference, null);
    await refuses(load(item), 'home_review_resolved');
  });
  await t.test('owner submitted to refused rechecks stale version and machine authority', async () => {
    await refuses(call(operation.verb, { ...operation.args, idempotency_key: randomUUID() }), 'version_conflict');
    await assert.rejects(executeRegisteredTool(c, { id: randomUUID(), slug: 'codex', human: false },
      operation.verb, { ...operation.args, idempotency_key: randomUUID() }));
    assert.equal(TOOLS['read-home-review'].write, undefined);
    assert.deepEqual(Object.keys(TOOLS['read-home-review'].inputSchema.properties), ['item_id', 'source_version']);
  });
  await t.test('refreshed to projected uses owner receipt for Undo', async () => {
    await call('reopen-one-action', { action_id: task, completion_event_id: receipt.completion_event_id,
      base_version: receipt.version, idempotency_key: randomUUID() });
    assert.equal((await read()).items.find(i => i.source_id === task).review_reference.kind, 'action');
  });
  const activity = randomUUID(), proposal = randomUUID();
  await c.query(`insert into activity(id,occurred_at,actor_id,kind,summary,detail,email_source)
    values($1,now(),$2,'email_in','Synthetic reply source','Synthetic source evidence',
      '{"mailbox_owner":"joe","sender_email":"sender@example.test"}');`, [activity, actors.joe.id]);
  await c.query(`insert into email_proposal(id,kind,activity_id,evidence_quote,owner,confidence,created_by,draft_subject,draft_body)
    values($1,'reply_draft',$2,'Synthetic source evidence','joe',0.95,$3,'Synthetic subject','Synthetic body')`,
  [proposal, activity, actors.joe.id]);
  await t.test('reply detail reuses exact mailbox-owned hydration and changes no state', async () => {
    const current = (await read()).items.find(i => i.source_id === proposal);
    const before = (await c.query('select * from email_proposal where id=$1', [proposal])).rows;
    const loaded = await load(current);
    assert.equal(loaded.detail.reply_draft.body, 'Synthetic body');
    assert.deepEqual(loaded.operations.map(o => o.verb), HOME_REVIEW_CONTRACT.kinds.reply_draft);
    assert.deepEqual(loaded.operations.at(-1).args.to, ['sender@example.test']);
    assert.deepEqual((await c.query('select * from email_proposal where id=$1', [proposal])).rows, before);
    assert.deepEqual((await call('list-email-proposals', { proposal_id: proposal }, actors.dell)).proposals, []);
    await refuses(call('read-home-review', { item_id: current.item_id, source_version: current.source_version }, actors.dell), 'home_review_not_found');
    await call('edit-email-draft', { ...loaded.operations[0].args, subject: 'Edited synthetic subject',
      body: 'Edited synthetic body', idempotency_key: randomUUID() });
    await refuses(load(current), 'home_review_stale');
    const fresh = (await read()).items.find(i => i.source_id === proposal);
    assert.equal((await load(fresh)).detail.reply_draft.body, 'Edited synthetic body');
  });
  await t.test('unsupported notification card refuses safely', async () => {
    const notification = randomUUID();
    await c.query(`insert into ops.notification(id,recipient_actor,subject_type,subject_ref,severity,reason,event_ref,event_source,deep_link,dedupe_key)
      values($1::uuid,$2,'call','synthetic','action_required','Synthetic unsupported update',$3,'event','/home',$1::uuid::text)`,
    [notification,actors.joe.id,receipt.completion_event_id]);
    const current = (await read()).items.find(i => i.source_type === 'notification');
    assert.equal(current.review_reference, null);
    await refuses(load(current), 'home_review_unsupported');
  });
  await t.test('reply with unavailable recipients offers no submission operation', async () => {
    await c.query("update actor set email=null where slug='dell'");
    const current = (await read()).items.find(i => i.source_id === proposal);
    const loaded = await load(current);
    assert.deepEqual(loaded.operations.map(o => o.verb), ['edit-email-draft', 'dismiss-email-proposal']);
    assert.equal(loaded.detail.preview_error, 'email_draft_copy_address_missing');
    await refuses(call('list-email-proposals', {proposal_id:proposal,limit:1}), 'email_proposals_arguments_invalid');
  });
  assert.deepEqual(JSON.parse(readFileSync(new URL('../contracts/home-review.v1.json', import.meta.url))), HOME_REVIEW_CONTRACT);
});
