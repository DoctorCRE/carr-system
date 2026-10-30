// Prove automatic evidence checks through capture and registered verbs on synthetic PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID, createHash } from 'node:crypto';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { applyPendingDealRoomReads } from './helpers/snapshot-schema.mjs';
import { createCaptureHandler } from '../src/capture.js';
import { executeRegisteredTool } from '../src/tools.js';

let fixture, c, actor, clientId;
before(async () => {
  fixture = await postgresDatabase('call-phase-evidence-');
  c = fixture.client;
  await c.query('create role neondb_owner');
  await promisify(execFile)('psql', ['-X', '-h', '127.0.0.1', '-p', String(c.connectionParameters.port),
    '-U', 'fixture', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-q',
    '-f', new URL('../../db/schema.sql', import.meta.url).pathname], { maxBuffer: 4 * 1024 * 1024 });
  await applyPendingDealRoomReads(c);
  actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic phase physician',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  clientId = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'fixture_active',$2,$2) returning id", [party, actor.id])).rows[0].id;
});
after(async () => { await fixture?.close(); });

async function transaction(fn, role = null) {
  await c.query('begin');
  try {
    if (role) await c.query(`set local role ${role}`);
    const result = await fn(c);
    await c.query('commit');
    return result;
  } catch (error) { await c.query('rollback'); throw error; }
}
const command = (name, args = {}) => transaction(client => executeRegisteredTool(client, actor, name, args));

async function filePhase(evidence, { speaker = "joe", storedQuote = evidence } = {}) {
  const deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic phase purchase',$1,'purchase','site_selection','joe',$2,$2) returning id", [clientId, actor.id])).rows[0].id;
  const token = randomUUID();
  const session = (await c.query(`insert into capture_session(nonce,device_id,actor_id,mode,started_at,consent_announced_at,
    session_token_hash,expires_at,state,state_at,post_call) values($1,'fixture',$2,'meeting',now(),now(),$1,
    now()+interval '1 day','distilling',now(),true) returning id`, [createHash('sha256').update(token).digest('hex'), actor.id])).rows[0].id;
  const handler = createCaptureHandler({ withWriter: fn => transaction(fn, 'carr_writer') });
  const response = await handler.fetch(new Request('http://127.0.0.1/capture/candidates', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({
      session_token: token, idempotency_key: randomUUID(), evidence_sources: [speaker === "client" ? null : {
        speaker, source_sha256: createHash('sha256').update(JSON.stringify({ speaker, text: storedQuote })).digest('hex'),
        quote_sha256: createHash('sha256').update(storedQuote).digest('hex'),
      }], items: [{ kind: 'phase_move',
        payload: { deal, field: 'phase', value: 'legal' }, evidence_quote: evidence, confidence: 1 }]
    })
  }), {});
  const body = await response.json();
  assert.equal(response.status, 200, JSON.stringify(body));
  return { deal, session, id: body.candidate_ids[0] };
}

test('punctuation-only evidence stays pending, preserves the deal and can be resolved by a partner', async () => {
  const { deal, session, id } = await filePhase('... !!! ??? ---');
  const before = await command('get-deal-room', { deal });
  const args = { idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic: true };
  for (let attempt = 0; attempt < 2; attempt++) {
    await assert.rejects(command('resolve-candidate', args), error => error.payload?.error === 'below_confidence_bar'
      && /evidence/.test(error.payload.reason));
  }
  const after = await command('get-deal-room', { deal });
  assert.equal(after.phase, before.phase);
  assert.equal(after.base_version, before.base_version);
  assert.deepEqual(after.events, before.events);
  assert.deepEqual(after.items, before.items);
  const outcome = (await command('read-call-outcomes')).calls.find(call => call.session_id === session);
  assert.ok(outcome.open_cards.some(card => card.candidate_id === id));
  await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: id, accept: true });
  assert.equal((await command('get-deal-room', { deal })).phase, 'legal');
});

test('a stated phase applies automatically once and remains undoable', async () => {
  const { deal, session, id } = await filePhase('Synthetic phase purchase moved to LEGAL.');
  const args = { idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic: true };
  assert.equal((await command('resolve-candidate', args)).automatic, true);
  const applied = await command('get-deal-room', { deal });
  assert.equal(applied.phase, 'legal');
  assert.equal((await command('resolve-candidate', args)).replayed, true);
  assert.deepEqual((await command('get-deal-room', { deal })).events, applied.events);
  const outcome = (await command('read-call-outcomes')).calls.find(call => call.session_id === session);
  assert.ok(outcome.applied.some(change => change.candidate_id === id));
  await command('undo-call-change', { idempotency_key: randomUUID(), candidate_id: id, base_version: applied.base_version });
  assert.equal((await command('get-deal-room', { deal })).phase, 'site_selection');
});

for (const [name, options] of [
  ['a client-only phase statement', { speaker: 'client' }],
  ['a quote missing from the stored transcript receipt', { storedQuote: 'We discussed a synthetic purchase without moving phase' }],
]) test(`${name} remains a proposal and cannot change the deal automatically`, async () => {
  const { deal, session, id } = await filePhase('Synthetic phase purchase moved to legal', options);
  const before = await command('get-deal-room', { deal });
  await assert.rejects(command('resolve-candidate', {
    idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic: true,
  }), error => error.payload?.error === 'below_confidence_bar');
  const after = await command('get-deal-room', { deal });
  assert.equal(after.phase, before.phase);
  assert.equal(after.base_version, before.base_version);
  assert.deepEqual(after.events, before.events);
  const outcome = (await command('read-call-outcomes')).calls.find(call => call.session_id === session);
  assert.ok(outcome.open_cards.some(card => card.candidate_id === id));
  await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: id, accept: true });
  assert.equal((await command('get-deal-room', { deal })).phase, 'legal');
});

test('a caller without partner authority cannot automatically apply a valid partner quote', async () => {
  const { deal, id } = await filePhase('Synthetic phase purchase moved to legal');
  const before = await command('get-deal-room', { deal });
  const unverified = { ...actor, human: false, native_agent_verified: false };
  await assert.rejects(transaction(client => executeRegisteredTool(client, unverified, 'resolve-candidate', {
    idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic: true,
  })), error => error.payload?.error === 'automatic_apply_not_allowed');
  const after = await command('get-deal-room', { deal });
  assert.equal(after.phase, before.phase);
  assert.deepEqual(after.events, before.events);
});
