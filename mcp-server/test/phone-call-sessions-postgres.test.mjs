// Verify phone session SQL, concurrency, and grants on disposable PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { createServer } from 'node:http';
import { Readable } from 'node:stream';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { handoffState } from './helpers/phone-handoff-state.mjs';
import { PhoneAudioHandoff } from '../src/phone-audio-handoff.js';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { createPhoneCallHandler } from '../src/phone-call-sessions.js';

let fixture, pool, actor, handler;
const clients = new Map();
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'phone-call-', snapshot: true });
  pool = new pg.Pool(fixture.connection);
  pool.on('connect', client => {
    let closed;
    const done = new Promise(resolve => { closed = resolve; });
    clients.set(client, { done, closed });
  });
  pool.on('remove', client => { clients.get(client).closed(); clients.delete(client); });

  actor = { ...(await pool.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  assert.ok(actor.id);
  handler = createPhoneCallHandler({ withWriter: async fn => {
    const c = await pool.connect();
    try {
      await c.query('begin');
      await c.query('set local role carr_writer');
      const result = await fn(c);
      await c.query('commit');
      return result;
    } catch (error) { await c.query('rollback'); console.error(error.code, error.message); throw error; }
    finally { c.release(); }
  } });
});
after(async () => {
  try {
    const closing = [...clients.values()].map(client => client.done);
    await pool?.end();
    await Promise.all(closing);
    assert.equal(clients.size, 0, 'all clients closed before PostgreSQL stops');
  } finally { await fixture?.close(); }
});

const handoff = new PhoneAudioHandoff(handoffState());
const env = { CALL_STUDIO_TOKEN: 'synthetic-service-secret',
  PHONE_AUDIO_HANDOFF: { idFromName: n => n, get: () => handoff } };
const url = 'https://app.example.test/api/v1/call-sessions';
const audio = Buffer.alloc(32044);
audio.write('RIFF', 0); audio.writeUInt32LE(32036, 4); audio.write('WAVEfmt ', 8);
audio.writeUInt32LE(16, 16); audio.writeUInt16LE(1, 20); audio.writeUInt16LE(1, 22);
audio.writeUInt32LE(16000, 24); audio.writeUInt32LE(32000, 28);
audio.writeUInt16LE(2, 32); audio.writeUInt16LE(16, 34);
audio.write('data', 36); audio.writeUInt32LE(32000, 40);
const metadata = { sha256: createHash('sha256').update(audio).digest('hex'), byte_count: audio.length, duration_seconds: 1, mime_type: 'audio/wav',
  started_at: '2026-01-01T12:00:00Z', consent_announced_at: '2026-01-01T12:00:00Z' };

test('owner answers once and Studio pickup cannot resurrect resolved questions', async () => {
  const session = await (await handler.fetch(new Request(url, { method: 'POST',
    body: JSON.stringify({ ...metadata, sha256: 'c'.repeat(64) }) }), env, actor)).json();
  const id = session.session_id;
  const read = async (who = actor) => handler.fetch(new Request(`${url}/${id}`), env, who);
  const other = { ...actor, id: '10000000-0000-0000-0000-000000000009', slug: 'dell' };
  const status = body => handler.studioFetch(new Request(
    `https://api.example.test/capture/phone/v1/call-sessions/${id}/status`, {
      method: 'POST', headers: { authorization: `Bearer ${env.CALL_STUDIO_TOKEN}` }, body: JSON.stringify(body),
    }), env);
  const question = { id: 'review-model-1', question: 'Confirm the closure?' };
  const held = { state: 'held_review', reason: 'open_review_questions', open_questions: 1, review_questions: [question] };
  assert.equal((await status(held)).status, 200);
  assert.deepEqual((await (await read()).json()).review_questions, [question]);
  assert.equal((await read(other)).status, 404);
  const body = { idempotency_key: 'question-answer-1', session_id: id, question_id: question.id, answer: 'confirm' };
  const answer = (value = body, who = actor) => handler.fetch(new Request(`${url}/${id}/answers`, {
    method: 'POST', body: JSON.stringify(value),
  }), env, who);
  for (const invalid of [{ ...body, answer: '' }, { ...body, answer: ' '.repeat(2) },
    { ...body, answer: 'x'.repeat(1001) }, { ...body, session_id: 'different' },
    { ...body, transcript: 'private words' }, { ...body, idempotency_key: '' }])
    assert.equal((await answer(invalid)).status, 400);
  assert.equal((await answer(body, other)).status, 404);
  const [first, duplicate] = await Promise.all([answer(), answer()]);
  assert.equal(first.status, 200);
  assert.equal(duplicate.status, 200);
  assert.deepEqual(await first.json(), await duplicate.json());
  assert.equal((await answer({ ...body, answer: 'dismiss' })).status, 409);
  assert.equal((await answer({ ...body, idempotency_key: 'new-answer' })).status, 409);
  assert.equal((await answer({ ...body, question_id: 'unknown', idempotency_key: 'unknown-answer' })).status, 404);
  const visible = await (await read()).json();
  assert.deepEqual(visible.review_questions, []);
  assert.equal(visible.open_questions, 0);
  const pickup = await (await status(held)).json();
  assert.deepEqual(pickup.question_answers, [{ question_id: question.id, answer: 'confirm' }]);
  assert.equal((await (await read()).json()).open_questions, 0);
  assert.equal((await status({ state: null, reason: null, open_questions: 0, review_questions: [] })).status, 200);
  assert.equal((await (await read()).json()).state, 'pending');
  assert.equal((await answer()).status, 200);
  assert.equal((await status(held)).status, 200);
  assert.deepEqual((await (await read()).json()).review_questions, []);
  const more = [{ id: 'q2', question: 'Invoice now?' }, { id: 'q3', question: 'Which recipient?' }];
  assert.equal((await status({ ...held, open_questions: 2, review_questions: more })).status, 200);
  for (const [question_id, value] of [['q2', 'dismiss'], ['q3', 'Use the attached client contact.']]) {
    const response = await answer({ ...body, question_id, answer: value, idempotency_key: question_id });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).answer, value);
  }
  assert.equal((await (await read()).json()).open_questions, 0);
  const local = { id: 'q4', question: 'Confirm the local closure?' };
  await status({ ...held, review_questions: [local] });
  await answer({ ...body, question_id: local.id, idempotency_key: local.id });
  const clear = { state: null, reason: null, open_questions: 0 };
  const beforeAck = await (await status(clear)).json();
  assert.ok(beforeAck.question_answers.some(q => q.question_id === local.id));
  assert.deepEqual((await (await read()).json()).review_questions, []);
  const outcomes = [{ question_id: local.id, outcome: 'already_resolved' }];
  assert.equal((await status({ ...clear, answer_outcomes: outcomes })).status, 200);
  assert.deepEqual((await (await read()).json()).answer_outcomes, outcomes);
  assert.equal((await answer({ ...body, question_id: local.id, idempotency_key: local.id })).status, 200);
  assert.equal((await (await answer({ ...body, question_id: local.id, idempotency_key: local.id })).json()).outcome,
    'already_resolved');
  assert.ok(!(await (await status(clear)).json()).question_answers.some(q => q.question_id === local.id));
  const stale = { id: 'q5', question: 'Resolved on the Mac?' };
  await status({ ...held, review_questions: [stale] });
  await status(clear);
  assert.deepEqual((await (await read()).json()).review_questions, []);
});

test('Studio holds override normal reads and clear idempotently', async () => {
  const admitted = await (await handler.fetch(new Request(url, { method: 'POST',
    body: JSON.stringify({ ...metadata, sha256: 'b'.repeat(64) }) }), env, actor)).json();
  const id = admitted.session_id;
  const read = async () => (await handler.fetch(new Request(`${url}/${id}`), env, actor)).json();
  const status = (body, token = env.CALL_STUDIO_TOKEN) => handler.studioFetch(new Request(
    `https://api.example.test/capture/phone/v1/call-sessions/${id}/status`, {
      method: 'POST', headers: { authorization: `Bearer ${token}` }, body: JSON.stringify(body),
    }), env);
  assert.equal((await status({ state: 'held_review', reason: 'summary_repeats_transcript', open_questions: 2 }, 'wrong')).status, 401);
  for (const update of [
    { state: 'needs_attention', reason: 'extraction_failed', open_questions: 0 },
    { state: 'needs_attention', reason: 'transient_ceiling', open_questions: 0 },
    { state: 'held_review', reason: 'summary_repeats_transcript', open_questions: 2 },
    { state: 'held_review', reason: 'transcript_unavailable', open_questions: 1 },
    { state: 'held_review', reason: 'budget', open_questions: 0 },
    { state: 'held_review', reason: 'open_review_questions', open_questions: 3 },
  ]) {
    assert.equal((await status(update)).status, 200);
    assert.deepEqual(await (await status(update)).json(), { ok: true, session_id: id, changed: false });
    const visible = await read();
    assert.deepEqual({ state: visible.state, reason: visible.reason, open_questions: visible.open_questions }, update);
  }
  const clear = { state: null, reason: null, open_questions: 0 };
  assert.equal((await status(clear)).status, 200);
  assert.deepEqual(await read(), { schema_version: 1, session_id: id, state: 'pending',
    filed: false, transferred: false, reason: null, open_questions: 0, review_questions: [] });
  const report = await handler.studioFetch(new Request(
    `https://api.example.test/capture/phone/v1/call-sessions/${id}/report`, {
      method: 'POST', headers: { authorization: `Bearer ${env.CALL_STUDIO_TOKEN}` },
      body: JSON.stringify({ summary: 'Synthetic summary', decisions: [], open_questions: [] }),
    }), env);
  assert.equal(report.status, 200);
  const held = { state: 'held_review', reason: 'summary_repeats_transcript', open_questions: 0 };
  await status(held);
  assert.equal((await read()).state, 'held_review');
  const reportRead = await (await handler.fetch(new Request(`${url}/${id}/report`), env, actor)).json();
  assert.equal(reportRead.state, 'held_review');
  await status(clear);
  assert.equal((await read()).state, 'ready_review');
  for (const invalid of [
    { ...clear, transcript: 'private transcript' }, { ...clear, reason: 'private summary' },
    { ...clear, open_questions: -1 }, { ...clear, open_questions: 2 },
    { state: 'held_review', reason: 'private question', open_questions: 0 },
  ]) assert.equal((await status(invalid)).status, 400);
});

test('concurrent imports admit one session under carr_writer and expose its report without filing', async () => {
  const admit = () => handler.fetch(new Request(url, { method: 'POST', body: JSON.stringify(metadata) }), env, actor);
  const replies = await Promise.all(Array.from({ length: 6 }, async () => {
    const response = await admit();
    assert.ok([200, 201].includes(response.status), await response.clone().text());
    return response.json();
  }));
  assert.equal(new Set(replies.map(r => r.session_id)).size, 1);
  assert.equal(replies.filter(r => !r.duplicate).length, 1);
  const id = replies[0].session_id;
  const transfer = await handler.fetch(new Request(`${url}/${id}/transfer`, { method: 'PUT',
    headers: { 'content-type': 'audio/wav', 'content-length': String(audio.length) }, body: audio }), env, actor);
  assert.equal(transfer.status, 202, await transfer.clone().text());
  const dir = await mkdtemp(join(tmpdir(), 'phone-pull-'));
  const server = createServer(async (req, res) => {
    try {
      const response = await handler.studioFetch(new Request(`http://127.0.0.1${req.url}`, {
        method: req.method, headers: req.headers,
        ...(['GET', 'HEAD'].includes(req.method) ? {} : { body: Readable.toWeb(req), duplex: 'half' }),
      }), env);
      res.writeHead(response.status, Object.fromEntries(response.headers));
      res.end(Buffer.from(await response.arrayBuffer()));
    } catch { res.writeHead(500).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const script = `
import sys,json,os,hashlib
from pathlib import Path
sys.path.insert(0, str(Path('tools/dictation-rig/bin').resolve()))
import phone_intake,post_call,transcribe_session
root = Path(sys.argv[1])
cfg = {'base_url': sys.argv[2]}
result = phone_intake.pull_pending(root,cfg,'synthetic-service-secret')
assert result['received'] == 1, result
session = next(root.glob('phone-*'))
assert hashlib.sha256((session/'imported.audio').read_bytes()).hexdigest() == sys.argv[3]
assert (session/'imported.audio').stat().st_mode & 0o777 == 0o600
model = root/'synthetic-model'
model.write_bytes(b'fixture model')
decoder = root/'decoder'
decoder.write_text('#!'+sys.executable+'\\nimport shutil,sys\\nshutil.copyfile(sys.argv[sys.argv.index("-i")+1],sys.argv[-1])\\n')
whisper = root/'whisper'
whisper.write_text('#!'+sys.executable+'\\nimport json,sys\\njson.dump({"transcription":[{"offsets":{"from":0,"to":1000},"text":"Synthetic recording."}]},open(sys.argv[sys.argv.index("-of")+1]+".json","w"))\\n')
decoder.chmod(0o700)
whisper.chmod(0o700)
os.environ['CARR_WHISPER_MODEL'] = str(model)
transcribe_session.WHISPER_CLI_BIN = str(whisper)
post_call.LOG_PATH = root/'processor.log'
post_call.store_context(session, {'session':session.name,'workspace_kind':'all',
    'generated_at':'2026-01-01T12:00:00Z','deals':[]})
def distiller(request):
    assert request['transcript']['segments'][0]['text'] == 'Synthetic recording.'
    return {'schema_version':1,'report':{'summary':'Synthetic imported conversation.','decisions':[], 'open_questions':[]},
        'joe_tasks':[],'dell_tasks':[],'deal_updates':[],'draft_proposals':[],'review_questions':[]}
status = phone_intake.extract(session, decoder=str(decoder), distiller=distiller)
assert status['state'] == 'ready_review', status
assert not (session/'ingested.json').exists()
print(json.dumps(result))
`;
    const child = spawn('python3', ['-c', script, dir, `http://127.0.0.1:${server.address().port}`, metadata.sha256], {
      cwd: new URL('../..', import.meta.url).pathname, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '', errors = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { errors += chunk; });
    const exit = await new Promise((resolve, reject) => { child.on('error', reject); child.on('exit', resolve); });
    assert.equal(exit, 0, errors);
    assert.equal(JSON.parse(output.trim().split('\n').at(-1)).received, 1);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await rm(dir, { recursive: true, force: true });
    await handoff.fetch(new Request(`https://fixture.test/${id}`, { method: 'DELETE' }));
  }
  const transferred = await (await handler.fetch(new Request(`${url}/${id}`), env, actor)).json();
  assert.equal(transferred.transferred, true);
  const projection = { summary: 'Synthetic imported conversation.', decisions: [], open_questions: [] };
  const report = await handler.studioFetch(new Request(`https://api.example.test/capture/phone/v1/call-sessions/${id}/report`, {
    method: 'POST', headers: { authorization: `Bearer ${env.CALL_STUDIO_TOKEN}` }, body: JSON.stringify(projection),
  }), env);
  assert.equal(report.status, 200);
  const read = await handler.fetch(new Request(`${url}/${id}/report`), env, actor);
  assert.equal(read.status, 200);
  const visible = await read.json();
  assert.deepEqual(visible.report, projection);
  assert.equal(visible.filed, false);
  assert.deepEqual(visible.actions, []);
  assert.deepEqual(visible.deal_updates, []);
  assert.equal((await handler.fetch(new Request(`${url}/${id}`), env, { ...actor, id: '10000000-0000-0000-0000-000000000009', slug: 'dell' })).status, 404);
});

test('phone writer grants preserve the current SCAC catalog and reject extra authority', async () => {
  const c = await pool.connect();
  try {
    await c.query('begin');
    const current = async () => (await c.query('select ops.scac_mutation_catalog_v118_current() as ok')).rows[0].ok;
    assert.equal(await current(), true, 'phone admission must preserve the sealed catalog');
    const contract = (await c.query('select ops.scac_current_grant_contract() as contract')).rows[0].contract;
    const grants = contract.relation_dml.filter(row => row.relation === 'public.phone_call_session');
    assert.deepEqual(grants.map(row => [row.grantee, row.privilege, row.grantable]),
      [['carr_writer', 'insert', false], ['carr_writer', 'update', false]]);
    await c.query('grant delete on public.phone_call_session to carr_writer');
    assert.equal(await current(), false, 'unregistered DELETE must fail closed');
    await c.query('revoke delete on public.phone_call_session from carr_writer');
    assert.equal(await current(), true);
  } finally {
    await c.query('rollback');
    c.release();
  }
});
