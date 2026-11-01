// Prove automatic evidence checks through capture and registered verbs on synthetic PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { promisify } from 'node:util';
import { randomUUID, createHash } from 'node:crypto';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { applyPendingDealRoomReads } from './helpers/snapshot-schema.mjs';
import { createCaptureHandler } from '../src/capture.js';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';

let fixture, c, actor, clientId;
before(async () => {
  fixture = await postgresDatabase('call-phase-evidence-');
  c = fixture.client;
  await c.query('create role neondb_owner');
  await promisify(execFile)('psql', ['-X', '-h', '127.0.0.1', '-p', String(c.connectionParameters.port),
    '-U', 'fixture', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-q',
    '-f', new URL('../../db/schema.sql', import.meta.url).pathname], { maxBuffer: 4 * 1024 * 1024 });
  await applyPendingDealRoomReads(c);
  await c.query('alter table activity add column email_context jsonb, add column email_source jsonb, add column email_identity jsonb');
  await c.query(readFileSync(new URL('../../migrations/1125_calendar_confirmation.sql', import.meta.url), 'utf8'));
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
const command = async (name, args = {}) => {
  const result = await transaction(client => executeRegisteredTool(client, actor, name, args));
  return TOOLS[name].afterCommit
    ? TOOLS[name].afterCommit(c, actor, args, result, fn => transaction(fn)) : result;
};

async function filePhase(evidence, { speaker = "joe", storedQuote = evidence, kind = "phase_move", overdue = false, existingDeal = null, stepText = 'Review revised terms' } = {}) {
  const deal = existingDeal || (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic phase purchase',$1,'purchase','site_selection','joe',$2,$2) returning id", [clientId, actor.id])).rows[0].id;
  let prior;
  if (overdue) prior = await command('set-next-step', {
    idempotency_key: randomUUID(), deal, text: 'Request revised terms', next_date: '2020-01-01',
    base_version: (await command('get-deal-room', { deal })).base_version,
  });
  const token = randomUUID();
  const session = (await c.query(`insert into capture_session(nonce,device_id,actor_id,mode,started_at,consent_announced_at,
    session_token_hash,expires_at,state,state_at,post_call) values($1,'fixture',$2,'meeting',now(),now(),$1,
    now()+interval '1 day','distilling',now(),$3) returning id`, [createHash('sha256').update(token).digest('hex'), actor.id, kind !== 'meeting_record'])).rows[0].id;
  const handler = createCaptureHandler({ withWriter: fn => transaction(fn, 'carr_writer') });
  const response = await handler.fetch(new Request('http://127.0.0.1/capture/candidates', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({
      session_token: token, idempotency_key: randomUUID(), ...(kind === 'meeting_record' ? {} : { evidence_sources: [speaker === "client" ? null : {
        speaker, source_sha256: createHash('sha256').update(JSON.stringify({ speaker, text: storedQuote })).digest('hex'),
        quote_sha256: createHash('sha256').update(storedQuote).digest('hex'),
      }] }), items: [{ kind,
        payload: kind === 'next_step' ? { deal, text: stepText, next_date: '2030-01-01' }
          : ['activity', 'meeting_record'].includes(kind) ? { ref: deal, kind: 'note', summary: evidence }
          : { deal, field: 'phase', value: 'legal' }, evidence_quote: evidence, confidence: 1 }]
    })
  }), {});
  const body = await response.json();
  assert.equal(response.status, 200, JSON.stringify(body));
  return { deal, session, id: body.candidate_ids[0], prior };
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

test('expired pending-only calls remain visible until reviewed', async () => {
  const { session, id } = await filePhase('...');
  await c.query("update capture_session set expires_at=now()-interval '1 day' where id=$1", [session]);
  const outcome = (await command('read-call-outcomes')).calls.find(call => call.session_id === session);
  assert.ok(outcome, 'expired call with pending work must remain listed');
  assert.deepEqual(outcome.applied, []);
  assert.ok(outcome.open_cards.some(card => card.candidate_id === id));
});

test('only the owning device can renew an unfinished expired capture', async () => {
  const { session, id, deal } = await filePhase('...');
  const token = randomUUID();
  await c.query(`update capture_session set device_id='fixture-device',session_token_hash=$2,
    expires_at=now()-interval '1 day' where id=$1`,
  [session, createHash('sha256').update(token).digest('hex')]);
  const handler = createCaptureHandler({ withWriter: fn => transaction(fn, 'carr_writer') });
  const environment = { CAPTURE_TOKENS: JSON.stringify({ 'fixture-device': 'fixture-device-proof', other: 'other-proof' }) };
  const read = () => handler.fetch(new Request('https://worker.test/capture/session', {
    headers: { authorization: `Bearer ${token}` },
  }), environment);
  const renew = (proof = 'fixture-device-proof') => handler.fetch(new Request('https://worker.test/capture/renew', {
    method: 'POST', headers: { authorization: `Bearer ${proof}`, 'content-type': 'application/json' },
    body: JSON.stringify({ session_token: token }),
  }), environment);
  const expired = await read();
  assert.equal(expired.status, 401);
  assert.equal((await expired.json()).error, 'session_token_expired');
  assert.equal((await renew('wrong')).status, 401);
  assert.equal((await renew('other-proof')).status, 401);
  assert.equal((await read()).status, 401);
  assert.equal((await renew()).status, 200);
  assert.equal((await read()).status, 200);
  const payload = { session_token: token, idempotency_key: randomUUID(), items: [{
    kind: 'activity', payload: { ref: deal, kind: 'note', summary: 'Discussed the call.' },
    evidence_quote: 'Discussed the call.', confidence: 0.5,
  }] };
  const publish = () => handler.fetch(new Request('https://worker.test/capture/candidates', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload),
  }), environment);
  const first = await publish();
  assert.equal(first.status, 200);
  const receipt = await first.json();
  assert.deepEqual(await (await publish()).json(), receipt);
  assert.ok((await (await read()).json()).candidate_statuses.some(row => row.id === id));
  for (const state of ['done', 'failed']) {
    await c.query("update capture_session set state=$2,expires_at=now()-interval '1 day' where id=$1", [session, state]);
    assert.equal((await renew()).status, 409);
    assert.equal((await read()).status, 401);
  }
  await c.query("update capture_session set state='distilling' where id=$1", [session]);
  await c.query(`insert into capture_post_call_report(session_id,idempotency_key,report_sha256,candidate_count)
    values($1,$2,$3,2)`, [session, randomUUID(), 'a'.repeat(64)]);
  assert.equal((await renew()).status, 409);
  assert.equal((await read()).status, 401);
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

for (const [kind, automatic, verdict, callUndo = false] of [
  ['next_step', true, 'yes'], ['next_step', false, 'yes'],
  ['next_step', true, 'no'], ['activity', false, 'yes'], ['activity', false, 'no'],
  ['next_step', true, 'yes', true], ['next_step', false, 'yes', true],
  ['next_step', true, 'yes', 'whole-call'], ['next_step', true, 'yes', 'assessment-then-whole-call'],
]) test(`${kind} call confirmation automatic=${automatic} assesses overdue completion ${verdict} once with ${callUndo === true ? 'call Undo' : callUndo || 'assessment Undo'}`, async () => {
  const evidence = verdict === 'yes' ? 'The landlord sent the requested revised terms.' : 'We discussed parking at the other property.';
  const { deal, session, id, prior } = await filePhase(evidence, { kind, overdue: true });
  let calls = 0;
  c.jevClient = { systemOne: async request => {
    calls++;
    assert.equal((await c.query("select count(*)::int as count from pg_locks where pid=pg_backend_pid() and locktype='advisory'")).rows[0].count, 0, 'Jev runs after commit without transaction locks');
    assert.equal(request.state.update, evidence);
    assert.equal(request.state.actions[0].description, 'Request revised terms');
    return { answers: Object.fromEntries(Object.keys(request.questions).map(key => [key,
      key.startsWith('quote_') ? { type: 'choice', choice: 'quote_0' } : {
        type: 'choice', choice: verdict, confidence: 0.99,
        probabilities: Object.fromEntries(['yes', 'no', 'unclear'].map(v => [v, v === verdict ? 0.98 : 0.01])),
      }])) };
  } };
  try {
    const args = { idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic };
    await command('resolve-candidate', args);
    const room = await command('get-deal-room', { deal });
    assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, verdict === 'yes' ? 'done' : 'open');
    const assessment = room.events.find(e => e.field === 'overdue_assessment');
    assert.equal(assessment.new_value.update, evidence);
    assert.equal(assessment.new_value.candidate_id, id);
    assert.equal(assessment.new_value.resolved, verdict === 'yes');
    assert.equal((await command('resolve-candidate', args)).replayed, true);
    assert.equal((await command('resolve-candidate', { ...args, idempotency_key: randomUUID() })).already, 'confirmed');
    assert.equal(calls, 1);
    assert.deepEqual((await command('get-deal-room', { deal })).events, room.events);
    if (verdict === 'yes') {
      const resolutions = (await command('read-deal-update-reviews')).automatic_resolutions;
      assert.equal(resolutions.find(row => row.assessment_id === assessment.id)?.undo_possible, true);
      if (callUndo === 'whole-call') await command('undo-call', { idempotency_key: randomUUID(), session_id: session });
      else if (callUndo === 'assessment-then-whole-call') {
        await command('review-deal-update', { idempotency_key: randomUUID(), assessment_id: assessment.id,
          decision: 'undo', reason: 'Synthetic completion correction' });
        await command('undo-call', { idempotency_key: randomUUID(), session_id: session });
      }
      else if (callUndo && automatic) await command('undo-call-change', {
        idempotency_key: randomUUID(), candidate_id: id, base_version: room.base_version,
      });
      else if (callUndo) {
        await assert.rejects(command('undo-call-change', { idempotency_key: randomUUID(),
          candidate_id: id, base_version: room.base_version }), error => error.payload?.error === 'not_applied_automatically');
        await command('review-deal-update', { idempotency_key: randomUUID(), assessment_id: assessment.id,
          decision: 'undo', reason: 'Synthetic completion correction' });
      }
      else await command('review-deal-update', { idempotency_key: randomUUID(), assessment_id: assessment.id,
        decision: 'undo', reason: 'Synthetic completion correction' });
      const restored = await command('get-deal-room', { deal });
      assert.equal(restored.next_actions.find(a => a.id === prior.next_action_id).status, 'open');
      assert.equal(restored.next_date, kind === 'next_step' && (!callUndo || !automatic) ? '2030-01-01' : '2020-01-01');
      assert.equal(restored.items.some(item => item.source_ref === `capture_candidate:${id}`), kind === 'next_step' && (!callUndo || !automatic));
      if (kind === 'next_step' && (!callUndo || !automatic)) assert.equal(restored.next_step, 'Review revised terms');
      if (kind === 'next_step' && !callUndo && automatic) {
        await command('undo-call-change', { idempotency_key: randomUUID(), candidate_id: id,
          base_version: restored.base_version });
        assert.equal((await command('get-deal-room', { deal })).next_date, '2020-01-01');
      }
      if (kind === 'next_step' && !callUndo && !automatic)
        await assert.rejects(command('undo-call-change', { idempotency_key: randomUUID(), candidate_id: id,
          base_version: restored.base_version }), error => error.payload?.error === 'not_applied_automatically');
      if (callUndo) {
        const undone = await command('review-deal-update', { idempotency_key: randomUUID(),
          assessment_id: assessment.id, decision: 'undo', reason: 'Undo after call Undo' });
        assert.equal(undone.ok, true);
      }
    }
  } finally { delete c.jevClient; }
});

for (const automatic of [true, false]) test(`Jev failure preserves the overdue action and accepted call plan automatic=${automatic}`, async () => {
  const { deal, id, prior } = await filePhase('The landlord sent the requested revised terms.', { kind: 'next_step', overdue: true });
  c.jevClient = { systemOne: async () => { throw new Error('Synthetic Jev outage'); } };
  try {
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic });
    const room = await command('get-deal-room', { deal });
    assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'open');
    assert.equal(room.next_date, '2030-01-01');
    assert.equal(room.next_step, 'Review revised terms');
    assert.ok(room.items.some(item => item.source_ref === `capture_candidate:${id}`));
    const proposal = (await command('read-deal-update-reviews')).proposals.find(p => p.deal_id === deal);
    assert.equal(proposal.assessment.candidate_id, id);
    assert.equal(proposal.assessment.reason, 'jev_unavailable');
    assert.equal(proposal.assessment.resolved, false);
    await command('review-deal-update', { idempotency_key: randomUUID(), assessment_id: proposal.assessment_id,
      decision: 'resolve', reason: 'Partner confirms the requested revised terms arrived' });
    const reviewed = await command('get-deal-room', { deal });
    assert.equal(reviewed.next_actions.find(a => a.id === prior.next_action_id).status, 'done');
    assert.equal(reviewed.next_date, '2030-01-01');
    assert.equal(reviewed.next_step, 'Review revised terms');
  } finally { delete c.jevClient; }
});

test('call apply commits a pending assessment before Jev runs, and Undo invalidates it', async () => {
  const { deal, id, prior } = await filePhase('The landlord sent the requested revised terms.', { kind: 'next_step', overdue: true });
  const args = { idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic: true };
  const result = await transaction(client => executeRegisteredTool(client, actor, 'resolve-candidate', args));
  const room = await command('get-deal-room', { deal });
  assert.ok(room.events.some(e => e.field === 'overdue_assessment_pending' && e.verb === 'resolve-candidate'));
  assert.ok(!room.events.some(e => e.field === 'overdue_assessment'));
  assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'open');
  await command('undo-call-change', { idempotency_key: randomUUID(), candidate_id: id, base_version: room.base_version });
  c.jevClient = { systemOne: async () => assert.fail('Undone call must not run Jev') };
  try {
    const processed = await TOOLS['resolve-candidate'].afterCommit(c, actor, args, result, fn => transaction(fn));
    assert.equal(processed.overdue_assessment.reason, 'overdue_action_changed');
    assert.equal((await command('get-deal-room', { deal })).next_actions.find(a => a.id === prior.next_action_id).status, 'open');
  } finally { delete c.jevClient; }
});

test('a later assessment receipt cannot hide a plan edit made while Jev runs', async () => {
  const { deal, id } = await filePhase('The landlord sent the requested revised terms.', { kind: 'next_step', overdue: true });
  c.jevClient = { systemOne: async request => {
    const room = await command('get-deal-room', { deal });
    await command('patch-deal-field', { idempotency_key: randomUUID(), deal, field: 'next_date',
      value: '2031-01-01', base_event_id: room.events.find(e => e.field === 'next_date').id });
    return { answers: Object.fromEntries(Object.keys(request.questions).map(key => [key,
      key.startsWith('quote_') ? { type: 'choice', choice: 'quote_0' } : {
        type: 'choice', choice: 'yes', confidence: 0.99, probabilities: { yes: 0.98, no: 0.01, unclear: 0.01 },
      }])) };
  } };
  try {
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic: true });
    const room = await command('get-deal-room', { deal });
    assert.ok(room.events.some(e => e.field === 'overdue_assessment' && e.new_value.resolved));
    await assert.rejects(command('undo-call-change', { idempotency_key: randomUUID(), candidate_id: id,
      base_version: room.base_version }), error => error.payload?.error === 'newer_change_exists');
    assert.equal((await command('get-deal-room', { deal })).next_date, '2031-01-01');
  } finally { delete c.jevClient; }
});

function completionAnswer(request, verdict = 'yes') {
  return { answers: Object.fromEntries(Object.keys(request.questions).map(key => [key,
    key.startsWith('quote_') ? { type: 'choice', choice: 'quote_0' } : {
      type: 'choice', choice: verdict, confidence: 0.99,
      probabilities: Object.fromEntries(['yes', 'no', 'unclear'].map(v => [v, v === verdict ? 0.98 : 0.01])),
    }])) };
}

for (const kind of ['activity', 'meeting_record']) test(`${kind} closing the current action prompts for a new step without rewriting the plan`, async () => {
  const { deal, id, prior } = await filePhase('The landlord sent the requested revised terms.', { kind, overdue: true });
  c.jevClient = { systemOne: async request => completionAnswer(request) };
  try {
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: id, accept: true });
    const room = await command('get-deal-room', { deal });
    assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'done');
    assert.equal(room.next_step, null, 'finished action must not appear as the current step');
    assert.equal(room.next_date, null, 'finished action must not appear overdue');
    assert.equal(room.next_step_required, true);
    const board = (await command('deal-room-board')).deals.find(d => d.id === deal);
    assert.deepEqual([board.next_step, board.next_date, board.next_step_required], [null, null, true]);
    assert.equal(room.events.filter(e => e.field === 'next_step').length, 1, 'projection must not rewrite the stored plan');
  } finally { delete c.jevClient; }
});

for (const verdict of ['no', 'down']) test(`retained action after Jev ${verdict} keeps its own due date for later notes`, async () => {
  const { deal, id, prior } = await filePhase('We discussed parking at the other property.', { kind: 'next_step', overdue: true });
  c.jevClient = { systemOne: async request => {
    if (verdict === 'down') throw new Error('Synthetic Jev unavailable');
    return completionAnswer(request, 'no');
  } };
  try {
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic: true });
    const note = await command('add-deal-note', { idempotency_key: randomUUID(), deal,
      text: 'The landlord sent the requested revised terms.' });
    const room = await command('get-deal-room', { deal });
    const request = room.events.find(e => e.field === 'overdue_assessment_pending' && e.new_value.note_id === note.note_id);
    assert.ok(request, 'the old action remains overdue despite the future call date');
    assert.equal(request.new_value.actions.find(a => a.id === prior.next_action_id).due_on, '2020-01-01');
  } finally { delete c.jevClient; }
});

for (const verdict of ['no', 'down']) for (const later of ['note', 'activity', 'meeting_record'])
  test(`later ${later} completion after Jev ${verdict} preserves the call plan`, async () => {
    const { deal, id, prior } = await filePhase('We discussed parking at the other property.', { kind: 'next_step', overdue: true });
    c.jevClient = { systemOne: async request => {
      if (verdict === 'down') throw new Error('Synthetic Jev unavailable');
      return completionAnswer(request, 'no');
    } };
    try {
      await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: id, accept: true, automatic: true });
      const before = await command('get-deal-room', { deal });
      c.jevClient = { systemOne: async request => completionAnswer(request) };
      const text = 'The landlord sent the requested revised terms.';
      if (later === 'note') await command('add-deal-note', { idempotency_key: randomUUID(), deal, text });
      else {
        const candidate = await filePhase(text, { kind: later, existingDeal: deal });
        await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: candidate.id, accept: true });
      }
      const room = await command('get-deal-room', { deal });
      assert.equal(room.next_actions.find(a => a.id === prior.next_action_id).status, 'done');
      assert.equal(room.next_step, 'Review revised terms', 'closing the hidden action must preserve the call step');
      assert.equal(room.next_date, '2030-01-01', 'closing the hidden action must preserve the call date');
      assert.equal(room.next_step_required, false);
      const board = (await command('deal-room-board')).deals.find(d => d.id === deal);
      assert.deepEqual([board.next_step, board.next_date, board.next_step_required], ['Review revised terms', '2030-01-01', false]);
      assert.deepEqual(room.items, before.items);
      assert.deepEqual(room.events.filter(e => ['next_step', 'next_date'].includes(e.field)),
        before.events.filter(e => ['next_step', 'next_date'].includes(e.field)));
    } finally { delete c.jevClient; }
  });

test('an earlier completed step cannot hide a later call plan with a retained action', async () => {
  const first = await filePhase('The landlord sent the requested revised terms.', { kind: 'activity', overdue: true });
  c.jevClient = { systemOne: async request => completionAnswer(request) };
  try {
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: first.id, accept: true });
    const call = await filePhase('We discussed parking at the other property.', {
      kind: 'next_step', overdue: true, existingDeal: first.deal,
    });
    c.jevClient = { systemOne: async request => completionAnswer(request, 'no') };
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: call.id, accept: true, automatic: true });
    const room = await command('get-deal-room', { deal: first.deal });
    assert.equal(room.next_step, 'Review revised terms');
    assert.equal(room.next_date, '2030-01-01');
  } finally { delete c.jevClient; }
});

for (const mode of ['whole-call', 'change']) for (const order of ['call-first', 'activity-first'])
  test(`${mode} undo ${order} keeps a completed retained action off the board`, async () => {
  const call = await filePhase('We discussed parking at the other property.', { kind: 'next_step', overdue: true });
  c.jevClient = { systemOne: async request => completionAnswer(request, 'no') };
  try {
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: call.id, accept: true, automatic: true });
    c.jevClient = { systemOne: async request => completionAnswer(request) };
    const activity = await filePhase('The landlord sent the requested revised terms.', { kind: 'activity', existingDeal: call.deal });
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: activity.id, accept: true });
    const sequence = order === 'call-first' ? [call, activity] : [activity, call];
    for (const item of sequence) {
      if (item === call && mode === 'change') await command('undo-call-change', { idempotency_key: randomUUID(),
        candidate_id: call.id, base_version: (await command('get-deal-room', { deal: call.deal })).base_version });
      else await command('undo-call', { idempotency_key: randomUUID(), session_id: item.session });
    }
    const room = await command('get-deal-room', { deal: call.deal });
    assert.equal(room.next_actions.find(a => a.id === call.prior.next_action_id).status, 'done');
    assert.deepEqual([room.next_step, room.next_date, room.next_step_required], [null, null, true]);
    const board = (await command('deal-room-board')).deals.find(d => d.id === call.deal);
    assert.deepEqual([board.next_step, board.next_date, board.next_step_required], [null, null, true]);
  } finally { delete c.jevClient; }
});

test('superseding a closed current step preserves an older hidden future action', async () => {
  const { deal, prior } = await filePhase('Synthetic closed current step', { kind: 'activity', overdue: true });
  await c.query("update next_action set status='done',updated_at=clock_timestamp() where id=$1", [prior.next_action_id]);
  const older = (await c.query(`insert into next_action(subject_type,subject_id,owner_id,due_on,description,created_by,updated_by,updated_at)
    values('deal',$1,$2,'2035-01-01','Older hidden future action',$2,$2,now()-interval '1 day') returning id`, [deal, actor.id])).rows[0].id;
  await c.query(`insert into event(cause,occurred_at,actor_id,verb,subject_type,subject_id,field,new_value)
    values('system',now(),$1,'resolve-candidate','deal',$2,'overdue_assessment_pending',$3)`, [actor.id, deal,
    { candidate_id: randomUUID(), candidate_kind: 'next_step', actions: [{ id: older, source: 'next_action' }] }]);
  const before = await command('get-deal-room', { deal });
  assert.equal(before.next_step, null);
  await command('patch-deal-field', { idempotency_key: randomUUID(), deal, field: 'phase', value: 'legal', base_event_id: null });
  const room = await command('get-deal-room', { deal });
  assert.equal(room.next_actions.find(a => a.id === older).status, 'open', 'hidden action keeps its own future date');
  assert.deepEqual(room.events.filter(e => ['next_step','next_date'].includes(e.field)),
    before.events.filter(e => ['next_step','next_date'].includes(e.field)));
});

for (const mode of ['whole-call', 'change']) test(`${mode} undo restores the prior action when a different completed retained action has the same text`, async () => {
  const first = await filePhase('We discussed parking at the other property.', {
    kind: 'next_step', overdue: true, stepText: 'Request revised terms',
  });
  c.jevClient = { systemOne: async request => completionAnswer(request, 'no') };
  try {
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: first.id, accept: true, automatic: true });
    const second = await filePhase('We discussed parking again.', { kind: 'next_step', existingDeal: first.deal });
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: second.id, accept: true, automatic: true });
    c.jevClient = { systemOne: async request => completionAnswer(request) };
    await command('add-deal-note', { idempotency_key: randomUUID(), deal: first.deal,
      text: 'The landlord sent the requested revised terms.' });
    const before = await command('get-deal-room', { deal: first.deal });
    assert.equal(before.next_actions.find(a => a.id === first.prior.next_action_id).status, 'done');
    if (mode === 'change') await command('undo-call-change', { idempotency_key: randomUUID(),
      candidate_id: second.id, base_version: before.base_version });
    else await command('undo-call', { idempotency_key: randomUUID(), session_id: second.session });
    const room = await command('get-deal-room', { deal: first.deal });
    assert.equal(room.next_step, 'Request revised terms', 'restore the prior plan, not the unrelated finished action');
    assert.equal(room.next_date, '2030-01-01');
    const board = (await command('deal-room-board')).deals.find(d => d.id === first.deal);
    assert.equal(board.next_step, 'Request revised terms');
  } finally { delete c.jevClient; }
});

for (const mode of ['whole-call', 'change']) test(`${mode} undo after an earlier call undo keeps the completed shown action off the board`, async () => {
  const first = await filePhase('We discussed parking at the other property.', { kind: 'next_step', overdue: true });
  c.jevClient = { systemOne: async request => completionAnswer(request, 'no') };
  try {
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: first.id, accept: true, automatic: true });
    await command('undo-call', { idempotency_key: randomUUID(), session_id: first.session });
    const restored = await command('get-deal-room', { deal: first.deal });
    assert.equal(restored.next_actions.find(a => a.id === first.prior.next_action_id).status, 'open');
    assert.deepEqual([restored.next_step, restored.next_date], ['Request revised terms', '2020-01-01']);
    const second = await filePhase('We discussed touring the second site.', {
      kind: 'next_step', existingDeal: first.deal, stepText: 'Tour the second site',
    });
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: second.id, accept: true, automatic: true });
    c.jevClient = { systemOne: async request => completionAnswer(request) };
    const activity = await filePhase('The landlord sent the requested revised terms.', { kind: 'activity', existingDeal: first.deal });
    await command('resolve-candidate', { idempotency_key: randomUUID(), candidate_id: activity.id, accept: true });
    const before = await command('get-deal-room', { deal: first.deal });
    assert.equal(before.next_actions.find(a => a.id === first.prior.next_action_id).status, 'done');
    if (mode === 'change') await command('undo-call-change', { idempotency_key: randomUUID(),
      candidate_id: second.id, base_version: before.base_version });
    else await command('undo-call', { idempotency_key: randomUUID(), session_id: second.session });
    const room = await command('get-deal-room', { deal: first.deal });
    assert.equal(room.next_actions.find(a => a.id === first.prior.next_action_id).status, 'done');
    assert.deepEqual([room.next_step, room.next_date, room.next_step_required], [null, null, true]);
    const board = (await command('deal-room-board')).deals.find(d => d.id === first.deal);
    assert.deepEqual([board.next_step, board.next_date, board.next_step_required], [null, null, true]);
  } finally { delete c.jevClient; }
});
