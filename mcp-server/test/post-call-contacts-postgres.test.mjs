// Verify captured contacts through the Home contract on synthetic PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID, createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { applyPendingDealRoomReads } from './helpers/snapshot-schema.mjs';
import { createCaptureHandler } from '../src/capture.js';
import { allowedIn } from '../src/mcp.js';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';

let fixture, c, actor, clientId;
before(async () => {
  fixture = await postgresDatabase('call-contact-');
  c = fixture.client;
  await c.query('create role neondb_owner');
  await promisify(execFile)('psql', ['-X', '-h', '127.0.0.1', '-p', String(c.connectionParameters.port),
    '-U', 'fixture', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-q',
    '-f', new URL('../../db/schema.sql', import.meta.url).pathname], { maxBuffer: 4 * 1024 * 1024 });
  await applyPendingDealRoomReads(c);
  const migration = new URL('../../migrations/1065_call_followup_contact.sql', import.meta.url);
  await c.query(readFileSync(migration, 'utf8'));
  actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic physician',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  clientId = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'fixture_active',$2,$2) returning id", [party, actor.id])).rows[0].id;
});
after(async () => { await fixture?.close(); });

async function followup(counterpart, owner = 'joe', parked = false) {
  const deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic contact call',$1,'purchase','site_selection',$2,$3,$3) returning id", [clientId, owner, actor.id])).rows[0].id;
  const token = randomUUID();
  await c.query(`insert into capture_session(nonce,device_id,actor_id,mode,started_at,consent_announced_at,
    session_token_hash,expires_at,state,state_at,post_call) values($1,'fixture',$2,'meeting',now(),now(),$1,
    now()+interval '1 day','distilling',now(),true)`, [createHash('sha256').update(token).digest('hex'), actor.id]);
  const transaction = async fn => {
    await c.query('begin');
    try {
      await c.query('set local role carr_writer');
      const result = await fn(c);
      await c.query('commit');
      return result;
    } catch (error) { await c.query('rollback'); throw error; }
  };
  const handler = createCaptureHandler({ withWriter: transaction });
  const response = await handler.fetch(new Request('http://127.0.0.1/capture/post-call/candidates', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({
      session_token: token, idempotency_key: randomUUID(), items: [{ kind: 'assigned_action', deal_id: deal,
        assignee: owner, action: 'Call about the lease', evidence_quote: 'Please call about the lease', confidence: 1,
        ...(counterpart === undefined ? {} : { counterpart }) }],
    }),
  }), {});
  const body = await response.json();
  assert.equal(response.status, 200, JSON.stringify(body));
  if (parked) await c.query("update deal set operating_state='parked',parking_reason='client_paused',parked_at=now(),parked_by=created_by where id=$1", [deal]);
  const result = await transaction(db => executeRegisteredTool(db, actor, 'read-post-call-followups', {}));
  return result.candidates.find(row => row.id === body.candidate_ids[0]);
}

test('a captured counterpart address resolves to the contact display name on Home', async () => {
  await c.query("insert into party(kind,name,email,phone,created_by,updated_by) values('person','Synthetic Pat','Pat <pat@example.test>','000-555-0101',$1,$1)", [actor.id]);
  assert.equal((await followup({ email: 'PAT@example.test' })).contact_name, 'Synthetic Pat');
});

test('a captured phone resolves through the shared contact resolver', async () => {
  assert.equal((await followup({ phone: '+1 (000) 555-0101' })).contact_name, 'Synthetic Pat');
});

test('a unique participant name resolves without deriving a person from the deal', async () => {
  assert.equal((await followup({ name: 'Synthetic Pat' })).contact_name, 'Synthetic Pat');
});

test('missing and unmatched counterparts return null, never the supplied display name', async () => {
  for (const counterpart of [undefined, { name: 'Unknown Person' }, { email: 'unknown@example.test', name: 'Synthetic Pat' }, { phone: '0005550199' }]) {
    assert.equal((await followup(counterpart)).contact_name, null);
  }
});

test('ambiguous, merged and deleted contact addresses never expose a name', async () => {
  await c.query(`insert into party(kind,name,email,deleted_at,merged_into,created_by,updated_by) values
    ('person','Synthetic Duplicate','pat@example.test',null,null,$1,$1),
    ('person','Synthetic Deleted','deleted@example.test',now(),null,$1,$1),
    ('person','Synthetic Merged','merged@example.test',null,null,$1,$1)`, [actor.id]);
  await c.query("update party set merged_into=(select id from party where name='Synthetic Deleted') where name='Synthetic Merged'");
  for (const email of ['pat@example.test', 'deleted@example.test', 'merged@example.test']) {
    assert.equal((await followup({ email })).contact_name, null);
  }
});

test('another partner contact stays behind the existing follow-up read rules', async () => {
  const dell = (await c.query("select id from actor where slug='dell'")).rows[0].id;
  await c.query("insert into party(kind,name,email,created_by,updated_by) values('person','Synthetic Dell Contact','dell-contact@example.test',$1,$1)", [dell]);
  assert.equal((await followup({ email: 'dell-contact@example.test' }, 'dell')).contact_name, 'Synthetic Dell Contact');
  assert.equal(await followup({ email: 'dell-contact@example.test' }, 'dell', true), undefined);
  assert.equal(allowedIn('doc', 'read-post-call-followups', TOOLS['read-post-call-followups']), false);
});
