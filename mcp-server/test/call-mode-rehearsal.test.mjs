// End-to-end rehearsal of the Monday pipeline call (WR-000433 AC4).
//
// Drives the real Call Mode pipeline on a SYNTHETIC call about SYNTHETIC deals
// against a disposable PostgreSQL: the companion (call-mode.py serve), the
// capture bridge claim and publish, the post-call worker, the capture Worker
// routes, and the record-layer verbs. Only three seams are injected, each the
// one production already exposes: the capture config file (pointing at this
// test's Worker), the distiller command (a deterministic stand-in for headless
// Sonnet unless CARR_REHEARSAL_SONNET=1), and the verb command (this test's
// record layer instead of tools/call-verb.py's production Worker).
//
// CARR_REHEARSAL_AUDIO=1 (macOS with whisper-cli) speaks the call with `say -o`
// into the two Quill tracks and transcribes them with transcribe_session.py.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';
import { createCaptureHandler } from '../src/capture.js';
import { agentActorForToken } from '../src/identity.js';

const REPO = fileURLToPath(new URL('../../', import.meta.url));
const BIN = path.join(REPO, 'tools/dictation-rig/bin');
const PYTHON = existsSync(path.join(REPO, '.venv/bin/python3')) ? path.join(REPO, '.venv/bin/python3') : 'python3';
const AUDIO = process.env.CARR_REHEARSAL_AUDIO === '1';
const SONNET = AUDIO || process.env.CARR_REHEARSAL_SONNET === '1';
const run = promisify(execFile);

// Who says what. Joe is on the microphone track, Dell on system audio.
const CALL = [
  ['Joe', "Let's start with Synthetic Bayside Dental. The LOI was signed on October 20, 2026, so Bayside moves to negotiation."],
  ['Dell', 'Agreed, Bayside is in negotiation as of October 20, 2026.'],
  ['Joe', 'Next step for Bayside: send the lease draft to the landlord by November 6, 2026.'],
  ['Dell', 'Synthetic Harbor Clinic toured both sites and liked the second one. Next step for Harbor: schedule the second walkthrough on November 3, 2026.'],
  ['Joe', 'Yes, agreed on the Harbor walkthrough for November 3.'],
  ['Joe', 'On the Pinecrest lab, I think it goes to legal.'],
  ['Dell', 'No, keep Pinecrest in site selection for now. We have not agreed on that one.'],
  ['Joe', 'Please email the Bayside landlord contact a short update.'],
  ['Dell', 'I will call the Harbor lender this week.'],
];

let fixture, admin, database, pool, c, server, baseUrl, companion, root, session, deals = {};
const joe = { slug: 'joe', human: true, via: 'oauth' };
const joeLocal = agentActorForToken('Bearer rehearsal-local', JSON.stringify({ 'joe-local': 'rehearsal-local' }), 'local-token');

async function inTransaction(fn) {
  const client = await pool.connect();
  try {
    await client.query('begin');
    const result = await fn(client);
    await client.query('commit');
    return result;
  } catch (error) { await client.query('rollback'); throw error; }
  finally { client.release(); }
}
const verb = (actor, name, args = {}) => inTransaction(client =>
  executeRegisteredTool(client, actor, name, TOOLS[name].write ? { idempotency_key: randomUUID(), ...args } : args));
const deal = async name => (await c.query(
  `select d.id, d.phase, d.version, b.next_step, d.next_date::text as next_date
     from deal d join v_deal_room_board b on b.id=d.id where d.name=$1`, [name])).rows[0];

before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin, connection;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'rehearsal requires disposable loopback PostgreSQL');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    database = `call_rehearsal_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${database}" template template0`);
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated rehearsal');
    fixture = await acquireDisposablePostgres({ prefix: 'call-rehearsal-', pgCtl: `${bin}/pg_ctl` });
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
    connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  }
  c = new pg.Client(connection);
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  const files = ['db/schema.sql', 'migrations/0984_call_mode_auto_apply.sql'].map(f => path.join(REPO, f));
  for (const file of files) {
    const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', file];
    if (ciDsn) await run('psql', [connection.connectionString, ...restore], { maxBuffer: 4 * 1024 * 1024 });
    else await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
  }
  pool = new pg.Pool({ ...connection, max: 4 });
  for (const actor of [joe, joeLocal]) actor.id = (await c.query('select id from actor where slug=$1', [actor.slug])).rows[0].id;

  // Synthetic book: three active deals and one addressable landlord contact.
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic Practice Owner',$1,$1) returning id", [joe.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'fixture_active',$2,$2) returning id", [party, joe.id])).rows[0].id;
  for (const [name, phase] of [['Synthetic Bayside Dental Relocation', 'research'],
    ['Synthetic Harbor Clinic Expansion', 'site_selection'], ['Synthetic Pinecrest Lab Lease', 'site_selection']]) {
    deals[name] = (await c.query(`insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by)
      values($1,$2,'lease',$3,'joe',$4,$4) returning id`, [name, client, phase, joe.id])).rows[0].id;
    await c.query("insert into deal_participant(deal_id,actor_id,role,set_by) values($1,$2,'lead',$2)", [deals[name], joe.id]);
  }
  const landlord = (await c.query(`insert into party(kind,name,email,ref,created_by,updated_by)
    values('person','Synthetic Bayside Landlord','landlord@example.test','P-' || nextval('ref_party_seq'),$1,$1) returning id`, [joe.id])).rows[0].id;
  await c.query("insert into deal_participant(deal_id,party_id,role,set_by) values($1,$2,'client_contact',$3)",
    [deals['Synthetic Bayside Dental Relocation'], landlord, joe.id]);
  // Each deal already carries a step Joe set by hand before the call.
  for (const [name, text, date] of [['Synthetic Bayside Dental Relocation', 'Old Bayside step', '2026-10-01'],
    ['Synthetic Harbor Clinic Expansion', 'Old Harbor step', '2026-10-30'],
    ['Synthetic Pinecrest Lab Lease', 'Old Pinecrest step', '2026-10-30']]) {
    const current = await deal(name);
    await verb(joe, 'set-next-step', { deal: current.id, text, next_date: date, base_version: current.version });
  }

  // The capture Worker and a record-layer door for the companion's verb calls.
  const capture = createCaptureHandler({ withWriter: inTransaction });
  const env = { CAPTURE_TOKENS: JSON.stringify({ 'quill-joe-mac': 'synthetic-device-token' }) };
  server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    let status = 200, payload;
    try {
      if (request.url === '/rehearsal/verb') {
        const { verb: name, args } = JSON.parse(body);
        payload = await inTransaction(client => executeRegisteredTool(client, joeLocal, name, args));
        if (payload?.ok === false) status = 409;
      } else {
        const answer = await capture.fetch(new Request(`${baseUrl}${request.url}`, {
          method: request.method, headers: request.headers, body: body.length ? body : undefined }), env);
        status = answer.status;
        payload = await answer.json();
      }
    } catch (error) { status = 409; payload = error.payload || { error: String(error.message) }; }
    response.writeHead(status, { 'content-type': 'application/json' });
    response.end(JSON.stringify(payload));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  companion?.kill();
  await new Promise(resolve => server ? server.close(resolve) : resolve());
  try { await pool?.end(); await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}" with (force)`); }
    finally {
      await Promise.all([admin?.end(), fixture?.close()]);
      if (root && process.env.CARR_REHEARSAL_KEEP !== '1') rmSync(root, { recursive: true, force: true });
      else if (root) console.log(`rehearsal files kept in ${root}`);
    }
  }
});

async function companionCall(port, route, body) {
  const response = await fetch(`http://127.0.0.1:${port}${route}`, body === undefined
    ? { headers: { origin: 'https://app.doctorcre.com', 'x-carr-call-mode': 'deal-room-v1' } }
    : { method: 'POST', body: JSON.stringify(body), headers: { origin: 'https://app.doctorcre.com',
      'x-carr-call-mode': 'deal-room-v1', 'content-type': 'application/json' } });
  const payload = await response.json();
  assert.equal(response.status, 200, JSON.stringify(payload));
  return payload;
}

test('a recorded weekly call updates every confidently discussed card and leaves the rest as cards', { timeout: SONNET ? 900_000 : 180_000 }, async () => {
  root = mkdtempSync(path.join(tmpdir(), 'carr-call-rehearsal-'));
  const recordings = path.join(root, 'Recordings');
  session = path.join(recordings, '2026.10.26-0900');
  mkdirSync(session, { recursive: true });
  const started = new Date().toISOString().replace(/\.\d+Z$/u, 'Z');
  const write = (file, value) => writeFileSync(path.join(session, file), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  write('announcement.json', { announcement_fired_at: started });
  write('meta.json', { started, files: { mic: 'mic.caf', system: 'system.caf' }, start_offset_ms: { mic: 0, system: 0 } });
  write('call-context.json', { schema: 1, mode: 'weekly_deal_call', started_at: started, recorder: 'Joe',
    speaker_labels: { mic: 'Joe', system: 'Dell' }, speaker_method: 'separate audio channels; no third-party voiceprint' });
  writeFileSync(path.join(root, 'capture.json'), JSON.stringify({ base_url: baseUrl, device_id: 'quill-joe-mac', token: 'synthetic-device-token' }));
  const env = { ...process.env, CARR_RECORDINGS_DIR: recordings, CARR_CAPTURE_CONFIG: path.join(root, 'capture.json'),
    CARR_CAPTURE_LOG: path.join(root, 'capture-bridge.log'), CARR_CALL_MODE_LOG: path.join(root, 'call-mode.log'),
    CARR_POST_CALL_VERB_COMMAND: `node ${fileURLToPath(new URL('./helpers/rehearsal-verb.mjs', import.meta.url))} ${baseUrl}/rehearsal/verb`,
    HOME: process.env.HOME };
  if (!SONNET) env.CARR_POST_CALL_DISTILLER_COMMAND = `${PYTHON} ${path.join(REPO, 'tools/dictation-rig/tests/fixtures/rehearsal_distiller.py')}`;

  // 1. Recording start: the capture rig claims the session with consent proof.
  await run(PYTHON, [path.join(BIN, 'capture-bridge.py'), 'claim', session], { env });
  assert.ok(existsSync(path.join(session, '.capture.json')), readFileSync(path.join(root, 'capture-bridge.log'), 'utf8'));

  // 2. The companion is running and the Deal Room hands it the exact deal index.
  const port = await new Promise(resolve => { const probe = createServer().listen(0, '127.0.0.1', () => {
    const { port } = probe.address(); probe.close(() => resolve(port)); }); });
  companion = spawn(PYTHON, [path.join(BIN, 'call-mode.py'), 'serve', '--port', String(port)], { env, stdio: 'ignore' });
  for (let i = 0; i < 100; i++) {
    try { await fetch(`http://127.0.0.1:${port}/api/state`); break; } catch { await new Promise(r => setTimeout(r, 100)); }
  }
  const { deals: index } = await verb(joe, 'get-call-context', { deal_ids: Object.values(deals) });
  const shaped = index.map(d => ({ id: d.id, name: d.name, owner: d.owner || '', operating_state: d.operating_state,
    participants: d.participants.filter(p => p.party_id && p.ref).map(p => ({ party_id: p.party_id, ref: p.ref,
      name: p.name || '', email: p.email || '', role: p.role || '' })) }));
  assert.deepEqual(shaped.find(d => d.id === deals['Synthetic Bayside Dental Relocation']).participants.map(p => p.name),
    ['Synthetic Bayside Landlord'], JSON.stringify(index));
  const context = { session: path.basename(session), workspace_kind: 'team', generated_at: started, deals: shaped };

  // 3. The call itself, then transcription, distillation, filing and auto-apply.
  if (AUDIO) {
    await companionCall(port, '/api/call-context', context);
    for (const [speaker, track] of [['Joe', 'mic'], ['Dell', 'system']]) {
      const lines = CALL.map(([who, text]) => (who === speaker ? text : '[[slnc 4000]]')).join(' ');
      await run('/usr/bin/say', ['-o', path.join(session, `${track}.caf`), lines]);
    }
    await run(PYTHON, [path.join(BIN, 'transcribe_session.py'), session], { env, timeout: 600_000 });
  } else {
    write('transcript.json', { segments: CALL.map(([speaker, text], i) => ({ start_ms: i * 5000, end_ms: i * 5000 + 4000, speaker, text })) });
    await companionCall(port, '/api/call-context', context);
  }
  let review;
  for (let i = 0; i < (SONNET ? 600 : 240); i++) {
    review = await companionCall(port, `/api/post-call?session=${encodeURIComponent(path.basename(session))}`);
    if (review.report?.automatic?.finished_at || review.status?.state === 'blocked') break;
    await new Promise(r => setTimeout(r, 500));
  }
  const logs = () => ['capture-bridge.log', 'call-mode.log'].map(f => existsSync(path.join(root, f)) ? readFileSync(path.join(root, f), 'utf8') : '').join('\n');
  assert.ok(review.report?.automatic?.finished_at, `auto-apply never finished: ${JSON.stringify(review.status)}\n${logs()}`);

  // 4. One durable call outcome lists every applied change with its evidence
  //    and every card still waiting (the task, the draft, anything unsettled).
  const bayside = await deal('Synthetic Bayside Dental Relocation');
  const harbor = await deal('Synthetic Harbor Clinic Expansion');
  const pinecrest = await deal('Synthetic Pinecrest Lab Lease');
  const { calls } = await verb(joe, 'read-call-outcomes');
  assert.equal(calls.length, 1);
  const outcome = calls[0];
  const applied = (dealId, kind) => outcome.applied.find(a => a.deal_id === dealId && a.kind === kind);
  // The deterministic stand-in states all three plainly. Live Sonnet makes its
  // own call: whatever it did not apply must be waiting as an approval card.
  for (const [target, kind] of [[bayside, 'phase_move'], [bayside, 'next_step'], [harbor, 'next_step']]) {
    if (applied(target.id, kind)) continue;
    assert.ok(SONNET, `${kind} on ${target.id} should have applied: ${JSON.stringify(outcome)}`);
    assert.ok(outcome.open_cards.some(card => card.deal_id === target.id && card.kind === kind), JSON.stringify(outcome.open_cards));
  }
  assert.ok(outcome.applied.length >= 1, 'the call applied nothing');
  for (const change of outcome.applied) {
    assert.ok(change.evidence_quote.trim(), 'every applied change carries its transcript evidence');
    assert.equal(change.undone_at, null);
  }

  // 5. The cards Joe and Dell discussed plainly are already updated.
  if (applied(bayside.id, 'phase_move')) {
    assert.equal(bayside.phase, 'negotiation');
    const started_on = (await c.query("select started_on::text from v_deal_room_phase_start where deal_id=$1 and phase='negotiation'", [bayside.id])).rows[0]?.started_on;
    assert.equal(started_on, '2026-10-20');
  } else assert.equal(bayside.phase, 'research');
  if (applied(bayside.id, 'next_step')) {
    assert.match(bayside.next_step, /lease draft/i);
    assert.equal(bayside.next_date, '2026-11-06');
  }
  if (applied(harbor.id, 'next_step')) {
    assert.match(harbor.next_step, /second walkthrough/i);
    assert.equal(harbor.next_date, '2026-11-03');
  } else assert.equal(harbor.next_step, 'Old Harbor step');
  // The disputed deal did not move; the disagreement is a card, not a write.
  assert.equal(pinecrest.phase, 'site_selection');
  assert.equal(pinecrest.next_step, 'Old Pinecrest step');
  assert.ok(!outcome.applied.some(a => a.deal_id === pinecrest.id));
  if (!SONNET) assert.ok(outcome.open_cards.some(card => card.kind === 'phase_move' && card.deal_id === pinecrest.id));
  const openKinds = outcome.open_cards.map(card => card.kind);
  assert.ok(openKinds.includes('email_draft'), JSON.stringify(outcome.open_cards));
  assert.ok(openKinds.includes('assigned_action'));
  // Vendor email stays a draft: nothing created in Outlook, nothing sent.
  assert.ok(!existsSync(path.join(session, 'outlook-drafts.json')));

  // 6. Undo reverts through the record layer, guarded by the deal's version.
  const phase = applied(bayside.id, 'phase_move');
  if (phase) {
    await assert.rejects(verb(joe, 'undo-call-change', { candidate_id: phase.candidate_id, base_version: bayside.version - 1 }),
      error => error.payload?.error === 'version_conflict' || error.payload?.error === 'stale_base_version');
    await verb(joe, 'undo-call-change', { candidate_id: phase.candidate_id, base_version: (await deal('Synthetic Bayside Dental Relocation')).version });
    assert.equal((await deal('Synthetic Bayside Dental Relocation')).phase, 'research');
    const afterUndo = (await verb(joe, 'read-call-outcomes')).calls[0];
    assert.ok(afterUndo.applied.find(a => a.candidate_id === phase.candidate_id).undone_at);
  }
  const step = applied(harbor.id, 'next_step');
  if (step) {
    await verb(joe, 'undo-call-change', { candidate_id: step.candidate_id, base_version: (await deal('Synthetic Harbor Clinic Expansion')).version });
    const restored = await deal('Synthetic Harbor Clinic Expansion');
    assert.equal(restored.next_step, 'Old Harbor step');
    assert.equal(restored.next_date, '2026-10-30');
    const items = (await c.query("select status from next_action where source_ref=$1 and item_title is not null", [`capture_candidate:${step.candidate_id}`])).rows;
    assert.ok(items.length && items.every(i => i.status === 'dropped'));
  }

  // 7. Once a partner has looked it over, the outcome leaves Home.
  await verb(joe, 'review-call-outcome', { session_id: outcome.session_id });
  assert.equal((await verb(joe, 'read-call-outcomes')).calls.length, 0);
});
