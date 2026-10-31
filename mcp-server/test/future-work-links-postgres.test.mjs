import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { publicError } from '../src/public-errors.ts';
import { applyPendingDealRoomReads } from './helpers/snapshot-schema.mjs';

let fixture, c, actor, connection;
before(async () => {
  const bin = ['/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin',
    '/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin'].find(p => existsSync(`${p}/initdb`));
  assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
  fixture = await acquireDisposablePostgres({ prefix: 'complete-wr-', pgCtl: `${bin}/pg_ctl` });
  await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
  await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
  await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
  connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  c = new pg.Client(connection);
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname];
  await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
  await c.query(readFileSync(new URL('../../migrations/1006_complete_work_request.sql', import.meta.url), 'utf8'));
  for (const name of ['1017_work_request_queue.sql', '1025_work_request_job_finished.sql', '1060_work_request_slice_outcome.sql', '1015_team_memory_store.sql'])
    if (!(await c.query('select 1 from schema_migrations where filename=$1', [name])).rows.length)
      await c.query(readFileSync(new URL('../../migrations/' + name, import.meta.url), 'utf8'));
  await c.query(readFileSync(new URL('../../migrations/1119_future_work_links.sql', import.meta.url), 'utf8'));
  await applyPendingDealRoomReads(c);
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });

async function command(name, args = {}, identity = actor, role = null, client = c) {
  await client.query('begin');
  try {
    if (role) await client.query(`set local role ${role}`);
    const result = await executeRegisteredTool(client, identity, name, args);
    await client.query('commit');
    return result;
  } catch (error) { await client.query('rollback'); throw error; }
}

test('a defect without a WR captures the full source and replays its link', async () => {
  const args={idempotency_key:randomUUID(),defect_class:'synthetic-wr-link',claimed:'All work was captured',
    actual:'A follow-up was lost. '+ 'Full source text. '.repeat(300),detected_by:'self'};
  const result=await command('record-defect',args,actor,'carr_writer');
  assert.match(result.work_request,/^WR-[0-9]+$/);
  const card=await command('work-request-card',{work_request:result.work_request},actor,'carr_reader');
  assert.equal(card.state,'captured');
  assert.equal(card.acceptance_criteria.length,1);
  assert.equal(JSON.parse(card.desired_outcome.split('Source record (verbatim JSON):\n')[1]).record.actual,args.actual.trim());
  assert.ok(card.source.provenance.record_ref);
  assert.equal((await command('record-defect',args,actor,'carr_writer')).work_request,result.work_request);
  assert.equal((await c.query('select count(*) from ops.future_work_link')).rows[0].count,'1');
});

let sharedWr;
async function verify(verb, args, explicit = false) {
  const key = randomUUID();
  const call = {...args,idempotency_key:key,...(explicit ? {work_request:sharedWr} : {})};
  const out=await command(verb,call,actor,'carr_writer');
  assert.match(out.work_request,/^WR-[0-9]+$/);
  if (explicit) assert.equal(out.work_request,sharedWr);
  sharedWr ||= out.work_request;
  const read=await command('work-request-card',{work_request:out.work_request},actor,'carr_reader');
  assert.equal(read.state,'captured');
  for (const link of out.work_request_links) {
    const stored=await c.query(`select w.ref from ops.future_work_link f join ops.work_request w on w.id=f.work_request_id where f.source_ref=$1`,[link.source_ref]);
    assert.equal(stored.rows[0].ref,out.work_request);
  }
  const replay=await command(verb,call,actor,'carr_writer');
  assert.equal(replay.work_request,out.work_request);
  assert.deepEqual(replay.work_request_links,out.work_request_links);
  return out;
}
for (const explicit of [false,true]) {
  test(`record-defect stores ${explicit ? 'explicit' : 'minted'} WR`, async()=>{
    await verify('record-defect',{defect_class:'synthetic-defect',claimed:'No follow-up needed',actual:'A correction is needed',detected_by:'check'},explicit);
  });
  test(`record-finding stores ${explicit ? 'explicit' : 'minted'} WR`, async()=>{
    await verify('record-finding',{subject:'repo:DoctorCRE/carr-system',kind:'discrepancy',value:{follow_up:'Resolve synthetic discrepancy'},source:'Synthetic local test',internal:true},explicit);
  });
  test(`decision creation and amendment preserve ${explicit ? 'explicit' : 'minted'} WR`, async()=>{
    const out=await verify('log-decision',{title:'Implement synthetic follow-up '+randomUUID(),rationale:'Build the recorded behavior and verify it.'},explicit);
    const amended=await verify('update-decision',{decision_id:out.decision_id,rationale:'Implement and verify the amended behavior.',reason:'Synthetic clarification',work_request:out.work_request});
    assert.equal(amended.work_request,out.work_request);
  });
  for (const kind of ['open_loop','team_loop','action_required','idea']) {
    test(`${kind} creation and amendment preserve ${explicit ? 'explicit' : 'minted'} WR`, async()=>{
      const section=kind==='open_loop'?'backlog':kind==='idea'?'parked':'open';
      await c.query(`insert into loop_block(rel_path,kind,seq,block_key,col_order,created_by,updated_by)
        select $1,$2,1,$3,'{}',$4,$4 where not exists(select 1 from loop_block where kind=$2 and block_key=$3)`,
        ['synthetic/'+kind+'.md',kind,section,actor.id]);
      const args={kind,owner:'joe',body:'Deliver the synthetic product behavior',title:'Deliver synthetic behavior',
        ...(kind==='open_loop'?{blocker:'other_lane',blocker_detail:'Synthetic dependency WR628.S2'}:{})};
      const out=await verify('add-loop',args,explicit);
      const stored=(await c.query('select tier,personal_to,version from loop_item where id=$1',[out.loop_id])).rows[0];
      if (['open_loop','idea'].includes(kind)) { assert.equal(stored.tier,'personal'); assert.equal(stored.personal_to,actor.id); }
      const edited=await verify('update-loop',{loop_id:out.loop_id,base_version:Number(stored.version),body:'Deliver and verify the amended synthetic behavior',work_request:out.work_request});
      assert.equal(edited.work_request,out.work_request);
    });
  }
  test(`convention single and batch writes store ${explicit ? 'explicit' : 'minted'} WR`,async()=>{
    await c.query(`insert into doctrine_document(slug,title,content_class,visibility) values('team-memory','Team memory','distillation','shared') on conflict(slug) do nothing`);
    const attached=await command('attach-memory',{surface:'codex',access:'read_write',idempotency_key:randomUUID()},actor);
    const section_key='conventions/proposed/synthetic-'+randomUUID();
    await verify('write-doctrine-section',{document:'team-memory',section_key,title:'Synthetic proposed convention',body_text:'Preserve the original source when capturing a follow-up.',base_version:0,attach_id:attached.attach_id},explicit);
    await verify('change-doctrine-sections',{document:'team-memory',attach_id:attached.attach_id,items:[{section_key:'conventions/proposed/batch-'+randomUUID(),title:'Synthetic batch proposal',body_text:'Keep the acceptance evidence with the work record.',base_version:0}]},explicit);
  });
}
test('an invalid explicit WR rolls back the record, receipt and pending link',async()=>{
  const args={idempotency_key:randomUUID(),defect_class:'synthetic-rollback',claimed:'Captured',actual:'Lost',detected_by:'self',work_request:'WR-999999999999'};
  await assert.rejects(command('record-defect',args,actor,'carr_writer'),error=>error.payload?.error==='work_request_not_found');
  assert.equal((await c.query('select count(*) from tool_call where idempotency_key=$1',[args.idempotency_key])).rows[0].count,'0');
  assert.equal((await c.query("select count(*) from defect where defect_class='synthetic-rollback'")).rows[0].count,'0');
});
test('existing refusal leaves no capture or replay',async()=>{
  const key=randomUUID();
  await assert.rejects(command('record-defect',{idempotency_key:key,defect_class:'synthetic-refusal',claimed:'Same',actual:'Same',detected_by:'self'},actor,'carr_writer'),e=>e.payload?.error==='no_contradiction_stated');
  assert.equal((await c.query('select count(*) from tool_call where idempotency_key=$1',[key])).rows[0].count,'0');
  assert.equal((await c.query('select count(*) from ops.future_work_link where source_key=$1',[key])).rows[0].count,'0');
});

let syntheticClient, syntheticDeal;
async function businessRecords() {
  if (!syntheticClient) {
    const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic follow-up owner',$1,$1) returning id",[actor.id])).rows[0].id;
    syntheticClient=(await c.query("insert into client(party_id,roster_ref,created_by,updated_by) values($1,'C-999628',$2,$2) returning id",[party,actor.id])).rows[0].id;
    syntheticDeal=(await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic WR628 purchase',$1,'purchase','research','joe',$2,$2) returning id",[syntheticClient,actor.id])).rows[0].id;
  }
}
for (const explicit of [false,true]) {
  test(`ordinary next actions and outreach store ${explicit ? 'explicit' : 'minted'} WR`,async()=>{
    await businessRecords();
    await verify('set-next-action',{ref:'C-999628',description:'Call synthetic client',due_on:'2026-11-02'},explicit);
    await verify('log-outreach',{ref:'C-999628',channel:'call',outcome:'left_message',summary:'Synthetic voicemail',next_on:'2026-11-03',next_step:'Call the synthetic client again'},explicit);
  });
  test(`deal next steps, notes, items and instructions store ${explicit ? 'explicit' : 'minted'} WR`,async()=>{
    await businessRecords();
    const version=Number((await c.query('select version from deal where id=$1',[syntheticDeal])).rows[0].version);
    await verify('set-next-step',{deal:syntheticDeal,text:'Request synthetic title commitment',next_date:'2026-11-02',base_version:version},explicit);
    await verify('add-deal-note',{deal:syntheticDeal,text:'Synthetic update with remaining work',next_step:'Review synthetic title commitment',next_date:'2026-11-03'},explicit);
    const item=await verify('record-deal-item',{deal:syntheticDeal,title:'Synthetic checklist '+randomUUID(),source_ref:'synthetic:'+randomUUID(),due_on:'2026-11-04',bullets:[{kind:'promised',text:'Deliver the synthetic document'}]},explicit);
    await verify('instruct-deal-item',{deal:syntheticDeal,item_id:item.item.id,text:'Verify the synthetic document',work_request:item.work_request});
  });
  test(`board questions and revisions store ${explicit ? 'explicit' : 'minted'} WR`,async()=>{
    await command('publish-board-snapshot',{board_id:'wr628',base_version:0,snapshot:{tasks:{}},idempotency_key:randomUUID()},actor,'carr_writer').catch(error=>{if(error.payload?.error!=='board_version_conflict')throw error;});
    const question_id='synthetic-'+randomUUID();
    const out=await verify('ask-board-question',{board_id:'wr628',question_id,base_version:0,prompt:'Choose the synthetic follow-up',asker_ref:'synthetic-orchestrator',allow_free_text:true},explicit);
    await verify('revise-board-question',{board_id:'wr628',question_id,base_version:1,prompt:'Choose the updated synthetic follow-up',asker_ref:'synthetic-orchestrator',allow_free_text:true,work_request:out.work_request});
  });
  test(`industry event plans preserve ${explicit ? 'explicit' : 'minted'} WR`,async()=>{
    const out=await verify('add-industry-event',{title:'Synthetic conference '+randomUUID(),owner_partner:'joe',organizer:'Synthetic organizer',kind:'conference',starts_at:'2026-11-03T10:00:00-06:00',ends_at:'2026-11-03T12:00:00-06:00',source:'Synthetic fixture'},explicit);
    await verify('update-industry-event',{event_id:out.event.id,base_version:1,title:'Synthetic revised conference',work_request:out.work_request});
  });
}

for (const explicit of [false,true]) {
  test(`board task publication stores ${explicit ? 'explicit' : 'minted'} WR and ignores completed tasks`,async()=>{
    const board_id='synthetic-'+randomUUID();
    const out=await verify('publish-board-snapshot',{board_id,base_version:0,snapshot:{tasks:{pending:{title:'Deliver synthetic work',status:'queued'},done:{title:'Already finished',status:'done'}}}},explicit);
    assert.equal(out.work_request_links.length,1);
    assert.ok(out.work_request_links[0].source_ref.endsWith(':pending'));
  });
  test(`retrieval concept proposals store ${explicit ? 'explicit' : 'minted'} WR`,async()=>{
    await verify('propose-retrieval-concept',{reason:'Use the synthetic situation for future retrieval',concept_key:'synthetic-'+randomUUID(),label:'Synthetic situation',definition:'A synthetic fixture situation.'},explicit);
  });
  test(`critical deadlines store ${explicit ? 'explicit' : 'minted'} WR`,async()=>{
    await businessRecords();
    await verify('add-critical-date',{deal:syntheticDeal,kind:'loi_expiry',due_on:'2026-11-04',note:'Synthetic deadline',source:'Synthetic fixture'},explicit);
  });
}
test('a captured record appears in the current Work Request queue',async()=>{
  const list=await command('current-work-requests',{},actor,'carr_reader');
  assert.ok(list.items.some(row=>row.source.freshness==='recorded'));
});

for (const explicit of [false,true]) {
  test(`retrieval phrase, mapping and retirement proposals store ${explicit ? 'explicit' : 'minted'} WR`,async()=>{
    const concept_key='synthetic-'+randomUUID();
    await verify('propose-retrieval-phrase',{reason:'Capture the synthetic phrase',concept_key,phrase:'synthetic follow up',match_mode:'exact',weight:1,source:'session_proposal',source_ref:'synthetic:source'},explicit);
    await verify('propose-retrieval-mapping',{reason:'Capture the synthetic mapping',concept_key,section_address:'team-memory#synthetic',role:'supports',weight:1,rationale:'Use this synthetic source.'},explicit);
    await verify('propose-retrieval-retirement',{reason:'Retire the synthetic mapping',target_type:'concept',target_id:randomUUID(),base_version:1},explicit);
  });
}

test('a conflicting explicit link refuses publicly and preserves the original association',async()=>{
  const out=await verify('log-decision',{title:'Synthetic link conflict',rationale:'Build the synthetic result.'});
  const key=randomUUID();
  await assert.rejects(command('update-decision',{decision_id:out.decision_id,rationale:'Keep the current association.',work_request:sharedWr,idempotency_key:key},actor,'carr_writer'),error=>{
    assert.equal(error.payload?.error,'future_work_link_conflict');
    assert.equal(publicError(error).error,'future_work_link_conflict');
    return true;
  });
  assert.equal((await c.query('select count(*) from tool_call where idempotency_key=$1',[key])).rows[0].count,'0');
});
