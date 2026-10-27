// Prove whole-call undo through registered verbs on synthetic PostgreSQL records.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID, createHash } from 'node:crypto';
import { createCaptureHandler } from '../src/capture.js';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { applyPendingPortalLink } from './helpers/snapshot-schema.mjs';
import { executeRegisteredTool } from '../src/tools.js';

let fixture, admin, database, c, actor, deal, clientRef, connection;
before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
    assert.equal(url.search, '', 'fixture connection must not override its loopback host');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    const name = `whole_call_undo_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${name}" template template0`);
    database = name;
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin',
      '/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'whole-call-undo-', pgCtl: `${bin}/pg_ctl` });
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
    connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  }
  c = new pg.Client(connection);
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname];
  if (ciDsn) await promisify(execFile)('psql', [connection.connectionString, ...restore], { maxBuffer: 2 * 1024 * 1024 });
  else await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
  await applyPendingPortalLink(c);
  if (process.env.CARR_CALL_UNDO_PHASE_MIGRATION)
    await c.query(readFileSync(process.env.CARR_CALL_UNDO_PHASE_MIGRATION,'utf8'));
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic physician',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  const client = (await c.query("insert into client(party_id,status,created_by,updated_by,roster_ref) values($1,'fixture_active',$2,$2,'C-999441') returning id,roster_ref", [party, actor.id])).rows[0];
  clientRef = client.roster_ref;
  deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic coastal purchase',$1,'purchase','site_selection','joe',$2,$2) returning id", [client.id, actor.id])).rows[0].id;
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

async function command(name, args = {}, identity = actor, role = null, client = c) {
  await client.query('begin');
  try {
    if (role) await client.query(`set local role ${role}`);
    const result = await executeRegisteredTool(client, identity, name, args);
    await client.query('commit');
    return result;
  } catch (error) { await client.query('rollback'); throw error; }
}

const sessionTokens = new Map();
async function filedCall() {
  const token = randomUUID();
  const session = (await c.query(`insert into capture_session(nonce,device_id,actor_id,mode,started_at,consent_announced_at,
    session_token_hash,expires_at,state,state_at,post_call) values($1,'fixture',$2,'meeting',now(),now(),$1,
    now()+interval '1 day','distilling',now(),true) returning id`, [createHash('sha256').update(token).digest('hex'),actor.id])).rows[0].id;
  sessionTokens.set(session, token);
  return session;
}
async function fileCandidate(session, item, key = randomUUID()) {
  const handler = createCaptureHandler({ withWriter: async fn => {
    await c.query('begin');
    await c.query('set local role carr_writer');
    try { const result = await fn(c); await c.query('commit'); return result; }
    catch(error) { await c.query('rollback'); throw error; }
  } });
  const response = await handler.fetch(new Request('http://127.0.0.1/capture/candidates', {
    method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({
      session_token:sessionTokens.get(session), idempotency_key:key, items:[item]
    })
  }), {});
  const body = await response.json();
  assert.equal(response.status,200,JSON.stringify(body));
  return body.candidate_ids[0];
}
const fileStep = (session, text, ref = deal, key = randomUUID()) => fileCandidate(session, {
  kind:'next_step', payload:{deal:ref,text,next_date:'2027-01-08'},
  evidence_quote:'Send the synthetic floor plan next week', confidence:1,
}, key);
const filePhase = (session, value, ref = deal, key = randomUUID()) => fileCandidate(session, {
  kind:'phase_move', payload:{deal:ref,field:'phase',value,started_on:'2026-10-01'},
  evidence_quote:'Move the synthetic clinic into negotiation', confidence:1,
}, key);
async function candidate(session, text, automatic = true, ref = deal) {
  const named = ref === 'Synthetic coastal purchase';
  const id = await fileStep(session, text, named ? deal : ref);
  if (named) await c.query("update capture_candidate set payload=jsonb_set(payload,'{deal}',to_jsonb($2::text)) where id=$1", [id,ref]);
  await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true,automatic});
  return id;
}
const room = (ref=deal) => command('get-deal-room',{deal:ref});
const setStep = async (text,ref=deal) => command('set-next-step',{idempotency_key:randomUUID(),deal:ref,text,next_date:'2027-01-01',base_version:(await room(ref)).base_version});

for (const field of ['next_step', 'next_date']) test(`automatic phase move preserves a newer overdue ${field} after filing replay`, async () => {
  const ref=await secondDeal();
  await command('set-next-step',{idempotency_key:randomUUID(),deal:ref,text:'Review the original overdue plan',
    next_date:'2020-01-01',base_version:(await room(ref)).base_version});
  const session=await filedCall();
  const key=randomUUID();
  const id=await filePhase(session,'negotiation',ref,key);
  if (field==='next_step') {
    await command('set-next-step',{idempotency_key:randomUUID(),deal:ref,text:'Keep the newer overdue partner plan',
      next_date:'2020-02-02',base_version:(await room(ref)).base_version});
  } else {
    await command('patch-deal-field',{idempotency_key:randomUUID(),deal:ref,field:'next_date',value:'2020-02-02',
      base_event_id:(await room(ref)).events.find(event=>event.field==='next_date').id});
  }
  assert.equal(await filePhase(session,'negotiation',ref,key),id);
  const before=await room(ref);
  await assert.rejects(command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true,automatic:true},actor,'carr_writer'),
    error=>error.payload?.error==='newer_change_exists');
  const after=await room(ref);
  assert.equal(after.phase,before.phase);
  assert.equal(after.next_step,before.next_step);
  assert.equal(after.next_date,before.next_date);
  assert.deepEqual(after.items,before.items);
  assert.deepEqual(after.events,before.events);
  assert.equal(after.base_version,before.base_version);
  const outcome=(await command('read-call-outcomes')).calls.find(call=>call.session_id===session);
  assert.ok(outcome.open_cards.some(card=>card.candidate_id===id));
  await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true});
  assert.equal((await room(ref)).phase,'negotiation');
});

test('a phase proposal without a filed plan base requires review', async () => {
  const ref=await secondDeal();
  const session=await filedCall();
  const id=await filePhase(session,'negotiation',ref);
  await c.query("update capture_candidate set payload=payload-'plan_base' where id=$1",[id]);
  const before=await room(ref);
  await assert.rejects(command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true,automatic:true}),
    error=>error.payload?.error==='missing_plan_base');
  assert.equal((await room(ref)).phase,before.phase);
  const outcome=(await command('read-call-outcomes')).calls.find(call=>call.session_id===session);
  assert.ok(outcome.open_cards.some(card=>card.candidate_id===id));
  await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true});
  assert.equal((await room(ref)).phase,'negotiation');
});

test('an unrelated edit does not hold an automatic phase move or its undo', async () => {
  const ref=await secondDeal();
  const session=await filedCall();
  const id=await filePhase(session,'negotiation',ref);
  await command('patch-deal-field',{idempotency_key:randomUUID(),deal:ref,field:'attention',value:true,base_event_id:null});
  const result=await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true,automatic:true},actor,'carr_writer');
  assert.equal(result.automatic,true);
  assert.equal((await room(ref)).phase,'negotiation');
  assert.equal((await room(ref)).attention,true);
  const outcome=(await command('read-call-outcomes')).calls.find(call=>call.session_id===session);
  assert.ok(outcome.applied.some(change=>change.candidate_id===id));
  await command('undo-call-change',{idempotency_key:randomUUID(),candidate_id:id,base_version:(await room(ref)).base_version});
  assert.equal((await room(ref)).phase,'site_selection');
  assert.equal((await room(ref)).attention,true);
});

test('a same-call automatic plan does not hold a filed phase move', async () => {
  const ref=await secondDeal();
  const session=await filedCall();
  const phase=await filePhase(session,'negotiation',ref);
  await candidate(session,'Send the same call floor plan',true,ref);
  const result=await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:phase,accept:true,automatic:true});
  assert.equal(result.automatic,true);
  const after=await room(ref);
  assert.equal(after.phase,'negotiation');
  assert.equal(after.next_step,'Send the same call floor plan');
  assert.equal(after.next_date,'2027-01-08');
});

for (const field of ['next_step', 'next_date']) test(`automatic next step preserves a newer ${field} edit and stays pending after replay`, async () => {
  await setStep('Keep the original plan');
  const session=await filedCall();
  const key=randomUUID();
  const id=await fileStep(session,'Send the captured plan',deal,key);
  if(field==='next_step') await setStep('Keep the partners newer plan');
  else {
    const current=await room();
    await command('patch-deal-field',{idempotency_key:randomUUID(),deal,field:'next_date',value:'2027-02-02',
      base_event_id:current.events.find(e=>e.field==='next_date')?.id ?? null});
  }
  assert.equal(await fileStep(session,'Send the captured plan',deal,key),id);
  const before=await room();
  await assert.rejects(command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true,automatic:true}),
    error=>error.payload?.error==='newer_change_exists');
  const after=await room();
  assert.equal(after.next_step,before.next_step);
  assert.equal(after.next_date,before.next_date);
  assert.deepEqual(after.items,before.items);
  const outcome=(await command('read-call-outcomes')).calls.find(call=>call.session_id===session);
  assert.ok(outcome.open_cards.some(card=>card.candidate_id===id));
  await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true});
  assert.equal((await room()).next_step,'Send the captured plan');
});

test('a legacy proposal without a filed plan base requires human resolution', async () => {
  const session=await filedCall();
  const id=await fileStep(session,'Review the legacy call proposal');
  await c.query("update capture_candidate set payload=payload-'plan_base' where id=$1",[id]);
  const before=await room();
  await assert.rejects(command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true,automatic:true}),
    error=>error.payload?.error==='missing_plan_base');
  assert.equal((await room()).next_step,before.next_step);
  await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true});
  assert.equal((await room()).next_step,'Review the legacy call proposal');
});

test('a same-call phase change can supersede the overdue plan before the automatic next step', async () => {
  await command('set-next-step',{idempotency_key:randomUUID(),deal,text:'Review the overdue plan',
    next_date:'2020-01-01',base_version:(await room()).base_version});
  const session=await filedCall();
  const id=await fileStep(session,'Send the current call plan');
  await phaseCandidate(session,'negotiation');
  const args={idempotency_key:randomUUID(),candidate_id:id,accept:true,automatic:true};
  const result=await command('resolve-candidate',args);
  assert.equal(result.automatic,true);
  const after=await room();
  assert.equal(after.next_step,'Send the current call plan');
  assert.equal((await command('resolve-candidate',args)).replayed,true);
  assert.deepEqual((await room()).items,after.items);
});

test('an unrelated deal edit does not hold the automatic plan', async () => {
  const ref=await secondDeal();
  const session=await filedCall();
  const id=await fileStep(session,'Send the first stated plan',ref);
  await command('patch-deal-field',{idempotency_key:randomUUID(),deal:ref,field:'attention',value:true,base_event_id:null});
  const result=await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true,automatic:true});
  assert.equal(result.automatic,true);
  assert.equal((await room(ref)).next_step,'Send the first stated plan');
});

test('one action restores the plan and checklist across repeated call changes with replayable evidence', async () => {
  await setStep('Review the original floor plan');
  const original = await room();
  const session = await filedCall();
  const first = await candidate(session,'Send the synthetic floor plan');
  const second = await candidate(session,'Call the synthetic architect');
  assert.equal((await room()).next_step,'Call the synthetic architect');
  const args={idempotency_key:randomUUID(),session_id:session};
  const undone=await command('undo-call',args);
  assert.equal(undone.undone,true);
  assert.deepEqual(undone.changes.map(row=>row.candidate_id).sort(),[first,second].sort());
  assert.ok(undone.changes.every(row=>row.evidence_quote && row.event_ids.length));
  const restored=await room();
  assert.equal(restored.next_step,original.next_step);
  assert.equal(restored.next_date,original.next_date);
  assert.deepEqual(restored.items,original.items);
  assert.ok(restored.thread.some(note=>note.text==='Review the original floor plan'));
  assert.ok(restored.thread.some(note=>note.text==='Call the synthetic architect'));
  assert.ok(restored.events.some(event=>event.verb==='undo-call' && event.new_value.session_id===session));
  const outcomes=(await command('read-call-outcomes')).calls.find(call=>call.session_id===session);
  assert.ok(outcomes.applied.every(change=>change.undone_at));
  assert.equal((await command('undo-call',args)).replayed,true);
  assert.equal((await command('undo-call',{...args,idempotency_key:randomUUID()})).already,'undone');
});

async function secondDeal() {
  return (await c.query(`insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by)
    select 'Synthetic second clinic',client_id,'purchase','site_selection','joe',$2,$2 from deal where id=$1 returning id`,
    [deal,actor.id])).rows[0].id;
}
test('a newer edit on any deal refuses the entire call without dropping another deal items', async () => {
  const refs=[deal,await secondDeal()].sort();
  await setStep('Review the first original plan',refs[0]);
  await setStep('Review the second original plan',refs[1]);
  const session=await filedCall();
  await candidate(session,'Send the first synthetic plan',true,refs[0]);
  await candidate(session,'Send the second synthetic plan',true,refs[1]);
  await setStep('Keep the partners newer plan',refs[1]);
  const before=await Promise.all(refs.map(room));
  await assert.rejects(command('undo-call',{idempotency_key:randomUUID(),session_id:session}),
    error=>error.payload?.error==='newer_change_exists');
  for (const [index,ref] of refs.entries()) {
    const after=await room(ref);
    assert.equal(after.next_step,before[index].next_step);
    assert.deepEqual(after.items,before[index].items);
    assert.equal(after.events.filter(e=>e.verb==='undo-call').length,before[index].events.filter(e=>e.verb==='undo-call').length);
  }
  const outcome=(await command('read-call-outcomes')).calls.find(call=>call.session_id===session);
  assert.ok(outcome.applied.every(change=>change.undone_at===null));
});

test('completed call tasks prevent whole-call undo and remain completed', async () => {
  const session=await filedCall();
  const applied=await candidate(session,'Send the completion fixture plan');
  const item=(await room()).items.find(item=>item.source_ref===`capture_candidate:${applied}`);
  await command('complete-deal-item',{idempotency_key:randomUUID(),deal,item_id:item.id});
  await assert.rejects(command('undo-call',{idempotency_key:randomUUID(),session_id:session}),
    error=>error.payload?.error==='newer_change_exists');
  assert.equal((await room()).items.find(row=>row.id===item.id).status,'done');
});

test('pending and human-confirmed proposals survive undo while automatic retries cannot reapply the call', async () => {
  const other=await secondDeal();
  const session=await filedCall();
  await candidate(session,'Keep the human confirmed plan',false,other);
  const automatic=await candidate(session,'Send the automatic fixture plan');
  const pending=(await c.query(`insert into capture_candidate(session_id,idempotency_key,batch_hash,item_index,kind,payload,evidence_quote,confidence)
    values($1,$2,$2,0,'next_step',$3,'Send the pending synthetic floor plan',1) returning id`,
    [session,randomUUID(),{deal,text:'Send the pending synthetic floor plan',next_date:'2027-01-08'}])).rows[0].id;
  const human=await room(other);
  const result=await command('undo-call',{idempotency_key:randomUUID(),session_id:session});
  assert.deepEqual(result.changes.map(change=>change.candidate_id),[automatic]);
  assert.deepEqual((await room(other)).items,human.items);
  assert.equal((await room(other)).next_step,human.next_step);
  const outcome=(await command('read-call-outcomes')).calls.find(call=>call.session_id===session);
  assert.ok(outcome.open_cards.some(card=>card.candidate_id===pending));
  await assert.rejects(command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:pending,accept:true,automatic:true}),
    error=>error.payload?.error==='call_already_undone');
  await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:pending,accept:true});
  assert.equal((await room()).next_step,'Send the pending synthetic floor plan');
});

test('an unverified agent cannot undo a call', async () => {
  const session=await filedCall();
  await assert.rejects(command('undo-call',{idempotency_key:randomUUID(),session_id:session},{...actor,human:false,slug:'synthetic-agent'}),
    error=>error.payload?.error==='human_only_verb_requires_verified_partner');
});

test('verified sponsored partners can undo and unattended sessions cannot', async () => {
  const session=await filedCall();
  await candidate(session,'Send the sponsored partner plan');
  const sponsor={...actor,human:false,slug:'codex',native_agent_verified:true,sponsoring_human_slug:'joe'};
  await assert.rejects(command('undo-call',{idempotency_key:randomUUID(),session_id:session},
    {...sponsor,via:'unattended-token'}),error=>error.payload?.error==='unattended_runner_authority_not_admitted');
  const result=await command('undo-call',{idempotency_key:randomUUID(),session_id:session},sponsor);
  assert.equal(result.undone,true);
});

async function phaseCandidate(session, value) {
  const id=await filePhase(session,value);
  await command('resolve-candidate',{idempotency_key:randomUUID(),candidate_id:id,accept:true,automatic:true});
  return id;
}
test('phase calls restore dates when supported and otherwise refuse atomically', async () => {
  const session=await filedCall();
  await command('set-next-step',{idempotency_key:randomUUID(),deal,text:'Review the overdue original phase plan',
    next_date:'2026-01-01',base_version:(await room()).base_version});
  const before=await room();
  await phaseCandidate(session,'negotiation');
  assert.equal((await room()).phase,'negotiation');
  const args={idempotency_key:randomUUID(),session_id:session};
  const supported=(await c.query(`select position('revert-deal-field' in
    pg_get_viewdef('v_deal_room_phase_start'::regclass)) > 0 as supported`)).rows[0].supported;
  if (!supported) {
    await assert.rejects(command('undo-call',args),error=>error.payload?.error==='phase_start_undo_unavailable');
    assert.equal((await room()).phase,'negotiation');
  }
  if (supported) {
    const result=await command('undo-call',args);
    assert.equal(result.undone,true);
    const restored=await room();
    assert.equal(restored.phase,before.phase);
    assert.equal(restored.next_step,before.next_step);
    assert.equal(restored.next_date,before.next_date);
    assert.deepEqual(restored.phase_started_on,before.phase_started_on);
  }
});

test('a human edit between automatic changes is preserved even when a call change is latest', async () => {
  const session=await filedCall();
  await candidate(session,'Send the first interleaved plan');
  await setStep('Preserve the partners interleaved plan');
  await candidate(session,'Send the last interleaved plan');
  const before=await room();
  await assert.rejects(command('undo-call',{idempotency_key:randomUUID(),session_id:session}),
    error=>error.payload?.error==='newer_change_exists');
  assert.equal((await room()).next_step,before.next_step);
  assert.deepEqual((await room()).items,before.items);
});

test('a previously undone sibling change causes a safe whole-call refusal', async () => {
  const session=await filedCall();
  await candidate(session,'Send the earlier sibling plan');
  const latest=await candidate(session,'Send the later sibling plan');
  const outcomes=(await command('read-call-outcomes')).calls.find(call=>call.session_id===session);
  await command('undo-call-change',{idempotency_key:randomUUID(),candidate_id:latest,
    base_version:outcomes.applied.find(change=>change.candidate_id===latest).deal_version});
  const before=await room();
  await assert.rejects(command('undo-call',{idempotency_key:randomUUID(),session_id:session}),
    error=>error.payload?.error==='newer_change_exists');
  assert.equal((await room()).next_step,before.next_step);
  assert.deepEqual((await room()).items,before.items);
});

test('multiple phase moves on one deal refuse without restoring only part of the call', async () => {
  const session=await filedCall();
  await phaseCandidate(session,'negotiation');
  await phaseCandidate(session,'due_diligence');
  const before=await room();
  await assert.rejects(command('undo-call',{idempotency_key:randomUUID(),session_id:session}),
    error=>error.payload?.error==='phase_history_undo_unavailable');
  assert.equal((await room()).phase,before.phase);
  assert.deepEqual((await room()).phase_started_on,before.phase_started_on);
});

test('a later ordinary action with the same date is preserved', async () => {
  const session=await filedCall();
  await candidate(session,'Send the original same date plan');
  await command('set-next-action',{idempotency_key:randomUUID(),ref:deal,description:'Keep the later ordinary action',due_on:'2027-01-08'});
  await assert.rejects(command('undo-call',{idempotency_key:randomUUID(),session_id:session}),
    error=>error.payload?.error==='newer_change_exists');
  assert.equal((await room()).next_step,'Keep the later ordinary action');
});

test('concurrent same-key undo requests return one receipt and a replay', async () => {
  const session=await filedCall();
  await candidate(session,'Send the concurrent fixture plan');
  const clients=[new pg.Client(connection),new pg.Client(connection)];
  try {
    await Promise.all(clients.map(client=>client.connect()));
    const args={idempotency_key:randomUUID(),session_id:session};
    const results=await Promise.all(clients.map(client=>command('undo-call',args,actor,null,client)));
    assert.equal(results.filter(result=>result.replayed).length,1);
    assert.deepEqual(results[0].changes,results[1].changes);
    const undoEvents=(await room()).events.filter(event=>event.verb==='undo-call'
      && event.field==='call_change_undo' && event.new_value.session_id===session);
    assert.equal(undoEvents.length,1);
  } finally { await Promise.all(clients.map(client=>client.end())); }
});

test('named and UUID references to the same deal undo as one recorded call journal', async () => {
  await setStep('Review the canonical original plan');
  const before=await room();
  const session=await filedCall();
  await candidate(session,'Send the named reference plan',true,'Synthetic coastal purchase');
  await candidate(session,'Send the UUID reference plan');
  const result=await command('undo-call',{idempotency_key:randomUUID(),session_id:session});
  assert.equal(result.changes.length,2);
  assert.ok(result.changes.every(change=>change.deal_id===deal));
  assert.equal((await room()).next_step,before.next_step);
  assert.equal((await room()).next_date,before.next_date);
});
