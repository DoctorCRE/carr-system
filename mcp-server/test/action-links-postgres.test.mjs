import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { actionLinkTools, buildActionLink } from '../src/action-links.js';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import pg from 'pg';
import { randomUUID } from 'node:crypto';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';
import { Validator } from '@cfworker/json-schema';

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}
const actor = { id: '20000000-0000-4000-8000-000000000001' };
const other = { id: '20000000-0000-4000-8000-000000000002' };
const draft = '20000000-0000-4000-8000-0000000000d1';
const lead = '20000000-0000-4000-8000-0000000000e1';
const party = '20000000-0000-4000-8000-0000000000a1';
const current = '20000000-0000-4000-8000-0000000000a2';

test('reply links reuse registered human approval on PostgreSQL', async t => {
  const fixture = await acquireDisposablePostgres({ prefix: 'reply-links-', snapshot: true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  t.after(async () => { try { await c.end(); } finally { await fixture.close(); } });
  const actors = {};
  for (const slug of ['joe', 'dell']) actors[slug] = { ...(await c.query(`insert into actor(slug,kind,display_name,email)
    values($1,'human','Synthetic partner',$2) on conflict(slug) do update set email=excluded.email,active=true returning id,slug`,
  [slug, `${slug}@example.test`])).rows[0], human: true };
  const call = async (verb, args, who = actors.joe) => {
    await c.query('begin');
    try {
      if (!TOOLS[verb].write) await c.query('set transaction read only');
      await c.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true)", [who?.slug ?? '']);
      await c.query(verb === 'resolve-action-link' ? 'set local role carr_reader' : 'set local role carr_writer');
      const result = await executeRegisteredTool(c, who, verb, args);
      await c.query('commit');
      return result;
    } catch (error) { await c.query('rollback'); throw error; }
  };
  const proposal = draft, source = randomUUID();
  await c.query(`insert into activity(id,occurred_at,actor_id,kind,summary,detail,email_source)
    values($1,now(),$2,'email_in','Synthetic reply source','Synthetic source evidence',
      '{"mailbox_owner":"joe","sender_email":"sender@example.test"}')`, [source, actors.joe.id]);
  await c.query(`insert into email_proposal(id,kind,activity_id,evidence_quote,owner,confidence,created_by,draft_subject,draft_body)
    values($1,'reply_draft',$2,'Synthetic source evidence','joe',0.95,$3,'Synthetic subject','Synthetic body')`,
  [proposal, source, actors.joe.id]);
  const link = version => buildActionLink({ kind: 'reply_draft', id: proposal, owner: 'joe', version });
  const resolve = (ref = link(1).ref, who) => call('resolve-action-link', { ref }, who);
  const refuses = (promise, error) => assert.rejects(promise, e => e.payload?.error === error);
  let loaded;
  await t.test('issue and resolve exact card repeatedly without approving or sending', async () => {
    const listed = await call('list-email-proposals', { proposal_id: proposal });
    assert.deepEqual(listed.proposals[0].action_link, link(1));
    const before = (await c.query('select * from email_proposal where id=$1', [proposal])).rows;
    loaded = await resolve();
    const contract = JSON.parse(await readFile(new URL('../contracts/action-link-resolve.v2.json', import.meta.url), 'utf8'));
    assert.equal(new Validator(contract).validate(loaded).valid, true);
    assert.deepEqual(await resolve(), loaded);
    assert.deepEqual(loaded, JSON.parse(await readFile(new URL('../contracts/fixtures/action-link-reply.v2.json', import.meta.url), 'utf8')));
    assert.deepEqual((await c.query('select * from email_proposal where id=$1', [proposal])).rows, before);
    assert.equal(loaded.card.actions[0].verb, 'submit-email-draft');
    assert.equal(new Validator(structuredClone(TOOLS['submit-email-draft'].inputSchema)).validate({
      ...loaded.card.actions[0].args, idempotency_key: randomUUID(),
    }).valid, true);
  });
  await t.test('wrong card and foreign actor are indistinguishable from unknown', async () => {
    await refuses(resolve(link(1).ref, actors.dell), 'action_link_not_found');
    await refuses(resolve(buildActionLink({ kind: 'reply_draft', id: randomUUID(), owner: 'joe', version: 1 }).ref), 'action_link_not_found');
    await refuses(resolve(buildActionLink({ kind: 'lead_draft', id: proposal }).ref), 'action_link_not_found');
  });
  await t.test('signed-out and machine reads and writes refuse', async () => {
    for (const who of [{}, { id: randomUUID(), slug: 'codex', human: false }]) {
      await assert.rejects(resolve(link(1).ref, who));
      await assert.rejects(call('submit-email-draft', { ...loaded.card.actions[0].args, idempotency_key: randomUUID() }, who));
    }
  });
  await t.test('missing copy address disables issuance and resolve; changed recipients refuse approval', async () => {
    await c.query("update actor set email=null where slug='dell'");
    const listed = await call('list-email-proposals', { proposal_id: proposal });
    assert.equal(listed.proposals[0].action_link, undefined);
    await refuses(resolve(), 'action_link_no_longer_actionable');
    await c.query("update actor set email='changed@example.test' where slug='dell'");
    await refuses(call('submit-email-draft', { ...loaded.card.actions[0].args, idempotency_key: randomUUID() }), 'email_draft_recipients_mismatch');
    await c.query("update actor set email='dell@example.test' where slug='dell'");
  });
  await t.test('edit expires reference and stale act; fresh resolve restores review', async () => {
    await call('edit-email-draft', { proposal_id: proposal, base_version: 1, subject: 'Edited synthetic subject',
      body: 'Edited synthetic body', idempotency_key: randomUUID() });
    await refuses(resolve(), 'action_link_stale');
    await refuses(call('submit-email-draft', { ...loaded.card.actions[0].args, idempotency_key: randomUUID() }), 'version_conflict');
    loaded = await resolve(link(2).ref);
    assert.equal(loaded.card.source_version, 2);
  });
  await t.test('source retirement expires both issued and reviewed references', async () => {
    await c.query(`update activity set email_context='{"lifecycle":{"state":"deleted"}}' where id=$1`, [source]);
    await refuses(resolve(link(2).ref), 'action_link_no_longer_actionable');
    await refuses(call('submit-email-draft', { ...loaded.card.actions[0].args, idempotency_key: randomUUID() }), 'email_not_live');
    await c.query('update activity set email_context=null where id=$1', [source]);
    assert.equal((await resolve(link(2).ref)).card.id, proposal);
  });
  await t.test('explicit approval consumes, replay refuses and same-key retry returns receipt', async () => {
    const op = loaded.card.actions[0];
    const args = { ...op.args, idempotency_key: randomUUID() };
    const receipt = await call(op.verb, args);
    assert.equal(receipt.status, 'approved_to_send');
    assert.deepEqual(await call(op.verb, args), { replayed: true, ...receipt });
    await refuses(resolve(link(2).ref), 'action_link_no_longer_actionable');
    await refuses(call(op.verb, { ...args, idempotency_key: randomUUID() }), 'email_draft_already_submitted');
    const row = (await c.query('select status,version,send_result from email_proposal where id=$1', [proposal])).rows[0];
    assert.deepEqual(row, { status: 'approved_to_send', version: 3, send_result: null });
  });
});

test('action links use current draft eligibility on PostgreSQL', async t => {
  const db = await postgresDatabase('action-links-');
  const c = db.client;
  try {
    const schema = await readFile(new URL('../../db/schema.sql', import.meta.url), 'utf8');
    for (const name of ['actor', 'party', 'lead', 'lead_contact_draft']) {
      const ddl = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`));
      assert.ok(ddl, `schema contains ${name}`);
      await c.query(ddl[0]);
    }
    await c.query("insert into party(id,kind,name,created_by,updated_by) values ($1,'person','Original',$3,$3),($2,'person','Current',$3,$3)", [party, current, actor.id]);
    await c.query("insert into lead(id,party_id,stage,created_by,updated_by) values ($1,$2,'qualified',$3,$3)", [lead, party, actor.id]);
    await c.query("insert into lead_contact_draft(id,lead_id,party_id,owner_id,subject,body,scheduled_for,time_zone,created_by) values ($1,$2,$3,$4,'Subject','Body','2026-11-03T14:00:00Z','America/Chicago',$4)", [draft, lead, party, actor.id]);
    const ref = buildActionLink({ kind: 'lead_draft', id: draft }).ref;
    const resolve = who => actionLinkTools({ ToolError })['resolve-action-link'].handler(c, who, { ref });
    const refused = (who, code) => assert.rejects(resolve(who), e => e.payload?.error === code);
    await t.test('owner receives a pending action with the scheduled instant', async () => {
      const result = await resolve(actor);
      assert.equal(result.card.actions[0].action, 'approve');
      assert.equal(Date.parse(result.card.scheduled_for), Date.parse('2026-11-03T14:00:00Z'));
    });
    await t.test('a different current party makes the draft ineligible', async () => {
      await c.query('update lead set party_id=$1 where id=$2', [current, lead]);
      await refused(actor, 'action_link_no_longer_actionable');
      await refused(other, 'action_link_not_found');
    });
    await t.test('a suppressed current party makes the draft ineligible', async () => {
      await c.query('update lead set party_id=$1 where id=$2', [party, lead]);
      await c.query("update party set contact_state='paused' where id=$1", [party]);
      await refused(actor, 'action_link_no_longer_actionable');
      await refused(other, 'action_link_not_found');
    });
  } finally { await db.close(); }
});
