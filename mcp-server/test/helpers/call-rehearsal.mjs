// The synthetic weekly call, its deal book and the real Call Mode filing path,
// shared by the server rehearsal and the app's live journey harness.
//
// Filing runs the shipped Python pieces: the capture bridge claims the
// session, the companion (call-mode.py serve) takes the Deal Room's context,
// and its post-call worker distils the written transcript with the
// deterministic stand-in, files it and auto-applies plain changes. The caller
// supplies where those pieces reach the record layer.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = fileURLToPath(new URL('../../../', import.meta.url));
const BIN = path.join(REPO, 'tools/dictation-rig/bin');
const FIXTURES = path.join(REPO, 'tools/dictation-rig/tests/fixtures');
const run = promisify(execFile);

export const PYTHON = process.env.CARR_POST_CALL_PYTHON || process.env.CARR_CI_PYTHON
  || (existsSync(path.join(REPO, '.venv/bin/python3')) ? path.join(REPO, '.venv/bin/python3') : 'python3');

// Who says what. Joe is on the microphone track, Dell on system audio.
export const CALL = [
  ['Joe', "Let's start with Synthetic Bayside Dental. The LOI was signed on October 20, 2026, so Bayside moves to negotiation."],
  ['Dell', 'Agreed, Bayside is in negotiation as of October 20, 2026.'],
  ['Joe', 'Next step for Bayside: send the lease draft to the landlord by November 6, 2026.'],
  ['Dell', 'Synthetic Harbor Clinic toured both sites and liked the second one. Next step for Harbor: schedule the second walkthrough on November 3, 2026.'],
  ['Joe', 'Yes, agreed on the Harbor walkthrough for November 3.'],
  ['Joe', 'On the Pinecrest lab, I think it goes to legal.'],
  ['Dell', 'No, keep Pinecrest in site selection for now. We have not agreed on that one.'],
  ['Joe', 'Please email the Bayside landlord contact a short update.'],
  ['Dell', 'I will call the Harbor lender this week.'],
  ['Dell', 'Next step for Gulf Imaging: confirm the floor plan with the architect by November 10, 2026.'],
  ['Joe', 'Yes, agreed on the Gulf Imaging floor plan.'],
  ['Joe', 'Next step for Magnolia Surgery: order the space plan from the designer by November 14, 2026.'],
];

// Name, phase, and the step Joe set by hand before the call.
const BOOK = [
  ['Synthetic Bayside Dental Relocation', 'research', 'Old Bayside step', '2026-10-01'],
  ['Synthetic Harbor Clinic Expansion', 'site_selection', 'Old Harbor step', '2026-10-30'],
  ['Synthetic Pinecrest Lab Lease', 'site_selection', 'Old Pinecrest step', '2026-10-30'],
  ['Synthetic Gulf Imaging Center', 'site_selection', 'Old Gulf step', '2026-10-01'],
  ['Synthetic Magnolia Surgery Center', 'site_selection', 'Old Magnolia step', '2026-10-30'],
];

/** One Demo practice with five active deals and an addressable landlord contact. */
export async function seedSyntheticBook({ query, setNextStep, actorId }) {
  const one = async (sql, params) => (await query(sql, params)).rows[0];
  const party = (await one("insert into party(kind,name,created_by,updated_by) values('person','Demo Practice',$1,$1) returning id", [actorId])).id;
  await query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  const client = (await one("insert into client(party_id,status,created_by,updated_by) values($1,'fixture_active',$2,$2) returning id", [party, actorId])).id;
  const deals = {};
  for (const [name, phase] of BOOK) {
    deals[name] = (await one(`insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by)
      values($1,$2,'lease',$3,'joe',$4,$4) returning id`, [name, client, phase, actorId])).id;
    await query("insert into deal_participant(deal_id,actor_id,role,set_by) values($1,$2,'lead',$2)", [deals[name], actorId]);
  }
  const landlord = (await one(`insert into party(kind,name,email,ref,created_by,updated_by)
    values('person','Synthetic Bayside Landlord','landlord@example.test','P-' || nextval('ref_party_seq'),$1,$1) returning id`, [actorId])).id;
  await query("insert into deal_participant(deal_id,party_id,role,set_by) values($1,$2,'client_contact',$3)",
    [deals['Synthetic Bayside Dental Relocation'], landlord, actorId]);
  for (const [name, , text, date] of BOOK) await setNextStep(deals[name], text, date);
  return deals;
}

/** The Deal Room's context index for these deals, shaped as Call Mode posts it. */
export function callContext(session, started, index) {
  const deals = index.map(d => ({ id: d.id, name: d.name, owner: d.owner || '', operating_state: d.operating_state,
    participants: d.participants.filter(p => p.party_id && p.ref).map(p => ({ party_id: p.party_id, ref: p.ref,
      name: p.name || '', email: p.email || '', role: p.role || '' })) }));
  return { session: path.basename(session), workspace_kind: 'team', generated_at: started, deals };
}

const freePort = () => new Promise(resolve => { const probe = createServer().listen(0, '127.0.0.1', () => {
  const { port } = probe.address(); probe.close(() => resolve(port)); }); });

/**
 * A recorded weekly-call session under `root`, claimed through the capture
 * bridge, with the companion serving it. The distiller is the deterministic
 * stand-in, live headless Sonnet, or one that exits non-zero as an outage would.
 */
export async function openCallSession({ root, started, captureUrl, deviceToken, env: extra = {}, distiller = 'stand-in' }) {
  const recordings = path.join(root, 'Recordings');
  const stamp = started.slice(0, 16).replace(/-/g, '.').replace('T', '-').replace(':', '');
  const session = path.join(recordings, stamp);
  mkdirSync(session, { recursive: true });
  const write = (file, value) => writeFileSync(path.join(session, file), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  write('announcement.json', { announcement_fired_at: started });
  write('meta.json', { started, files: { mic: 'mic.caf', system: 'system.caf' }, start_offset_ms: { mic: 0, system: 0 } });
  write('call-context.json', { schema: 1, mode: 'weekly_deal_call', started_at: started, recorder: 'Joe',
    speaker_labels: { mic: 'Joe', system: 'Dell' }, speaker_method: 'separate audio channels; no third-party voiceprint' });
  writeFileSync(path.join(root, 'capture.json'), JSON.stringify({ base_url: captureUrl, device_id: 'quill-joe-mac', token: deviceToken }));
  const env = { ...process.env, CARR_RECORDINGS_DIR: recordings, CARR_CAPTURE_CONFIG: path.join(root, 'capture.json'),
    CARR_CAPTURE_LOG: path.join(root, 'capture-bridge.log'), CARR_CALL_MODE_LOG: path.join(root, 'call-mode.log'),
    CARR_POST_CALL_PYTHON: PYTHON, HOME: root, ...extra };
  if (distiller !== 'live') {
    env.CARR_POST_CALL_DISTILLER_COMMAND = distiller === 'failing' ? `${PYTHON} -c "raise SystemExit(3)"`
      : `${PYTHON} ${path.join(FIXTURES, 'rehearsal_distiller.py')}`;
    env.CARR_POST_CALL_JEV_FIXTURE = path.join(FIXTURES, 'rehearsal_jev.py');
  }
  const logs = () => ['capture-bridge.log', 'call-mode.log'].map(f => existsSync(path.join(root, f))
    ? readFileSync(path.join(root, f), 'utf8') : '').join('\n');

  // Recording start: the capture rig claims the session with consent proof.
  await run(PYTHON, [path.join(BIN, 'capture-bridge.py'), 'claim', session], { env });
  if (!existsSync(path.join(session, '.capture.json'))) throw new Error(`capture claim failed:\n${logs()}`);

  const port = await freePort();
  const companion = spawn(PYTHON, [path.join(BIN, 'call-mode.py'), 'serve', '--port', String(port)], { env, stdio: 'ignore' });
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 100; i++) {
    try { await fetch(`${url}/api/state`); break; } catch { await new Promise(r => setTimeout(r, 100)); }
  }
  async function request(route, body) {
    const response = await fetch(`${url}${route}`, body === undefined
      ? { headers: { origin: 'https://app.doctorcre.com', 'x-carr-call-mode': 'deal-room-v1' } }
      : { method: 'POST', body: JSON.stringify(body), headers: { origin: 'https://app.doctorcre.com',
        'x-carr-call-mode': 'deal-room-v1', 'content-type': 'application/json' } });
    const payload = await response.json();
    if (response.status !== 200) throw new Error(`${route} -> ${response.status} ${JSON.stringify(payload)}`);
    return payload;
  }
  const name = path.basename(session);
  return {
    session, name, url, env, logs, request,
    /** As transcribe_session.py records it: each track diarized to one voice. */
    writeTranscript() {
      write('transcript.json', { speaker_tracks: { mic: { diarized: true, speakers: 1 }, system: { diarized: true, speakers: 1 } },
        segments: CALL.map(([speaker, text], i) => ({ start_ms: i * 5000, end_ms: i * 5000 + 4000, speaker, text })) });
    },
    /** Poll the companion until `done(review)` holds. */
    async waitFor(done, { attempts = 240 } = {}) {
      let review;
      for (let i = 0; i < attempts; i++) {
        review = await request(`/api/post-call?session=${encodeURIComponent(name)}`);
        if (done(review)) return review;
        await new Promise(r => setTimeout(r, 500));
      }
      throw new Error(`post-call never settled: ${JSON.stringify(review?.status)}\n${logs()}`);
    },
    stop() { companion.kill(); },
  };
}
