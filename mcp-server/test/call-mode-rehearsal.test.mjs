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
import { NOW } from './helpers/fixed-clock.mjs';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { execFile } from 'node:child_process';
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
import { CALL, PYTHON, callContext, openCallSession, seedSyntheticBook } from './helpers/call-rehearsal.mjs';

const REPO = fileURLToPath(new URL('../../', import.meta.url));
const BIN = path.join(REPO, 'tools/dictation-rig/bin');
const AUDIO = process.env.CARR_REHEARSAL_AUDIO === '1';
const SONNET = AUDIO || process.env.CARR_REHEARSAL_SONNET === '1';
const run = promisify(execFile);

let fixture, admin, database, pool, c, server, baseUrl, call, root, deals = {};
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
  try {
    await run(PYTHON, ['-c', 'import sys; sys.path.insert(0, sys.argv[1]); import post_call_jev', BIN]);
  } catch (error) {
    throw new Error(`Call Mode rehearsal Python ${PYTHON} cannot import post_call_jev. `
      + 'Install requirements.lock in a virtual environment and set CARR_POST_CALL_PYTHON to its Python executable.\n'
      + (error.stderr || error.message), { cause: error });
  }
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
  const files = ['db/schema.sql'].map(f => path.join(REPO, f));
  for (const file of files) {
    const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', file];
    if (ciDsn) await run('psql', [connection.connectionString, ...restore], { maxBuffer: 4 * 1024 * 1024 });
    else await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
  }
  const dsn = connection.connectionString
    || `postgresql://carr_ci@localhost/carr_ci?host=${encodeURIComponent(fixture.root)}`;
  await run(PYTHON, [path.join(REPO, 'tools/migrate.py'), '--apply', '--yes'],
    { env: { ...process.env, DATABASE_URL: dsn }, maxBuffer: 4 * 1024 * 1024 });
  pool = new pg.Pool({ ...connection, max: 4 });
  for (const actor of [joe, joeLocal]) actor.id = (await c.query('select id from actor where slug=$1', [actor.slug])).rows[0].id;

  deals = await seedSyntheticBook({ query: (sql, params) => c.query(sql, params), actorId: joe.id,
    setNextStep: async (id, text, next_date) => verb(joe, 'set-next-step',
      { deal: id, text, next_date, base_version: (await c.query('select version from deal where id=$1', [id])).rows[0].version }) });

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
  call?.stop();
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

test('a recorded weekly call updates every confidently discussed card and leaves the rest as cards', { timeout: SONNET ? 900_000 : 180_000 }, async () => {
  root = mkdtempSync(path.join(tmpdir(), 'carr-call-rehearsal-'));
  const started = new Date(NOW).toISOString().replace(/\.\d+Z$/u, 'Z');
  call = await openCallSession({ root, started, captureUrl: baseUrl, deviceToken: 'synthetic-device-token',
    distiller: SONNET ? 'live' : 'stand-in',
    env: { CARR_POST_CALL_VERB_COMMAND: `node ${fileURLToPath(new URL('./helpers/rehearsal-verb.mjs', import.meta.url))} ${baseUrl}/rehearsal/verb` } });

  // The Deal Room hands the companion the exact deal index.
  const { deals: index } = await verb(joe, 'get-call-context', { deal_ids: Object.values(deals) });
  assert.deepEqual(index.find(d => d.id === deals['Synthetic Bayside Dental Relocation']).participants
    .filter(p => p.party_id && p.ref).map(p => p.name), ['Synthetic Bayside Landlord'], JSON.stringify(index));
  const context = callContext(call.session, started, index);

  // The call itself, then transcription, distillation, filing and auto-apply.
  if (AUDIO) {
    await call.request('/api/call-context', context);
    for (const [speaker, track] of [['Joe', 'mic'], ['Dell', 'system']]) {
      const lines = CALL.map(([who, text]) => (who === speaker ? text : '[[slnc 4000]]')).join(' ');
      await run('/usr/bin/say', ['-o', path.join(call.session, `${track}.caf`), lines]);
    }
    await run(PYTHON, [path.join(BIN, 'transcribe_session.py'), call.session], { env: call.env, timeout: 600_000 });
  } else {
    call.writeTranscript();
    await call.request('/api/call-context', context);
  }
  await call.waitFor(r => r.report?.automatic?.finished_at, { attempts: SONNET ? 600 : 240 });
  const logs = call.logs;

  // 4. One durable call outcome lists every applied change with its evidence
  //    and every card still waiting (the task, the draft, anything unsettled).
  const bayside = await deal('Synthetic Bayside Dental Relocation');
  const harbor = await deal('Synthetic Harbor Clinic Expansion');
  const pinecrest = await deal('Synthetic Pinecrest Lab Lease');
  const gulf = await deal('Synthetic Gulf Imaging Center');
  const magnolia = await deal('Synthetic Magnolia Surgery Center');
  const { calls } = await verb(joe, 'read-call-outcomes');
  assert.equal(calls.length, 1);
  const outcome = calls[0];
  assert.equal(outcome.call_date, '2026-10-27');
  const applied = (dealId, kind) => outcome.applied.find(a => a.deal_id === dealId && a.kind === kind);
  // The deterministic stand-in states all three plainly. Live Sonnet makes its
  // own call: whatever it did not apply must be waiting as an approval card.
  for (const [target, kind] of [[bayside, 'phase_move'], [bayside, 'next_step'], [harbor, 'next_step'], [gulf, 'next_step'], [magnolia, 'next_step']]) {
    if (applied(target.id, kind)) continue;
    assert.ok(SONNET, `${kind} on ${target.id} should have applied: ${JSON.stringify(outcome)}\n${logs()}`);
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
  assert.ok(!existsSync(path.join(call.session, 'outlook-drafts.json')));

  // 6. Undo reverts through the record layer, guarded by the deal's version,
  //    and never overwrites work done on the card after the call.
  const items = async candidateId => (await c.query(
    "select id, status from next_action where source_ref=$1 and item_title is not null order by created_at, id",
    [`capture_candidate:${candidateId}`])).rows;
  const gulfStep = applied(gulf.id, 'next_step');
  if (gulfStep) {
    // The call's step superseded Joe's overdue Gulf step; undo puts that ball back.
    const undo = { candidate_id: gulfStep.candidate_id, base_version: (await deal('Synthetic Gulf Imaging Center')).version };
    const result = await verb(joe, 'undo-call-change', undo);
    const restored = await deal('Synthetic Gulf Imaging Center');
    assert.equal(restored.next_step, 'Old Gulf step');
    assert.equal(restored.next_date, '2026-10-01');
    assert.ok(result.reverted.reopened_action_ids.length >= 1, JSON.stringify(result));
    assert.ok((await items(gulfStep.candidate_id)).every(i => i.status === 'dropped'));
    // A second undo of the same change is a no-op, not a second write.
    assert.equal((await verb(joe, 'undo-call-change', { ...undo, base_version: restored.version })).already, 'undone');
    assert.equal((await deal('Synthetic Gulf Imaging Center')).version, restored.version);
  }
  const baysideStep = applied(bayside.id, 'next_step');
  if (baysideStep && applied(bayside.id, 'phase_move')) {
    // The call's phase move cleared Joe's overdue Bayside step before the new
    // step landed, so the board showed no step: undo must not bring it back.
    await verb(joe, 'undo-call-change', { candidate_id: baysideStep.candidate_id,
      base_version: (await deal('Synthetic Bayside Dental Relocation')).version });
    const restored = await deal('Synthetic Bayside Dental Relocation');
    assert.equal(restored.next_step, null, 'undo revived a step the board had already hidden');
    assert.equal(restored.next_date, null);
  }
  const magnoliaStep = applied(magnolia.id, 'next_step');
  if (magnoliaStep) {
    // Joe replaced the step by hand after the call: undo refuses and keeps his edit.
    const current = await deal('Synthetic Magnolia Surgery Center');
    await verb(joe, 'set-next-step', { deal: magnolia.id, text: 'Later Magnolia step', next_date: '2026-11-20', base_version: current.version });
    await assert.rejects(verb(joe, 'undo-call-change', { candidate_id: magnoliaStep.candidate_id,
      base_version: (await deal('Synthetic Magnolia Surgery Center')).version }), error => error.payload?.error === 'newer_change_exists');
    assert.equal((await deal('Synthetic Magnolia Surgery Center')).next_step, 'Later Magnolia step');
  }
  const phase = applied(bayside.id, 'phase_move');
  if (phase) {
    await assert.rejects(verb(joe, 'undo-call-change', { candidate_id: phase.candidate_id, base_version: bayside.version - 1 }),
      error => error.payload?.error === 'version_conflict' || error.payload?.error === 'stale_base_version');
    await verb(joe, 'undo-call-change', { candidate_id: phase.candidate_id, base_version: (await deal('Synthetic Bayside Dental Relocation')).version });
    assert.equal((await deal('Synthetic Bayside Dental Relocation')).phase, 'research');
    const afterUndo = (await verb(joe, 'read-call-outcomes')).calls[0];
    assert.ok(afterUndo.applied.find(a => a.candidate_id === phase.candidate_id).undone_at);
  }
  const harborStep = applied(harbor.id, 'next_step');
  if (harborStep) {
    // Joe ticked off a checklist item after the call: undo refuses, the tick stays.
    const [item] = await items(harborStep.candidate_id);
    assert.ok(item, 'the applied step carries its checklist items');
    await verb(joe, 'complete-deal-item', { deal: harbor.id, item_id: item.id });
    await assert.rejects(verb(joe, 'undo-call-change', { candidate_id: harborStep.candidate_id,
      base_version: (await deal('Synthetic Harbor Clinic Expansion')).version }), error => error.payload?.error === 'newer_change_exists');
    assert.equal((await items(harborStep.candidate_id)).find(i => i.id === item.id).status, 'done');
    assert.match((await deal('Synthetic Harbor Clinic Expansion')).next_step, /second walkthrough/i);
  }

  // 7. Once a partner has looked it over, the outcome leaves Home.
  await verb(joe, 'review-call-outcome', { session_id: outcome.session_id });
  assert.equal((await verb(joe, 'read-call-outcomes')).calls.length, 0);
});
