// Exercise the real registered contracts and migration on isolated synthetic PostgreSQL.
import test, {before,after} from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {acquireDisposablePostgres} from './helpers/disposable-postgres.mjs';
import {executeRegisteredTool} from '../src/tools.js';

let fixture,c,actor;
before(async () => {
  const bin=['/opt/homebrew/opt/postgresql@18/bin','/usr/lib/postgresql/18/bin',
    '/opt/homebrew/opt/postgresql@17/bin','/usr/lib/postgresql/17/bin'].find(p=>existsSync(`${p}/initdb`));
  assert.ok(bin,'isolated PostgreSQL required');
  fixture=await acquireDisposablePostgres({prefix:'practice-origination-',pgCtl:`${bin}/pg_ctl`});
  await fixture.run(`${bin}/initdb`,['-D',`${fixture.root}/data`,'-U','carr_ci','--auth=trust','--no-locale']);
  await fixture.run(`${bin}/pg_ctl`,['-D',`${fixture.root}/data`,'-l',`${fixture.root}/server.log`,
    '-o',`-k ${fixture.root} -h ''`,'-w','start']);
  await fixture.run(`${bin}/createdb`,['-h',fixture.root,'-U','carr_ci','carr_ci']);
  c=new pg.Client({host:fixture.root,user:'carr_ci',database:'carr_ci'});
  await c.connect();
  await c.query('create role neondb_owner');
  await fixture.run(`${bin}/psql`,['-h',fixture.root,'-U','carr_ci','-d','carr_ci','-X','-q','-v','ON_ERROR_STOP=1',
    '-f',new URL('../../db/schema.sql',import.meta.url).pathname]);
  await c.query(readFileSync(new URL('../../migrations/1071_practice_origination.sql',import.meta.url),'utf8'));
  await c.query(readFileSync(new URL('../../migrations/1080_practice_address_key.sql',import.meta.url),'utf8'));
  actor={...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
});
after(async()=>{try {await c?.end();} finally {await fixture?.close();}});
async function command(verb,args,role='carr_writer') {
  await c.query('begin');
  try {
    await c.query(`set local role ${role}`);
    const out=await executeRegisteredTool(c,actor,verb,args);
    await c.query('commit');
    return out;
  } catch(e) {await c.query('rollback');throw e;}
}
const read=(args={})=>command('list-practice-origination',args,'carr_reader');
const evaluation=JSON.parse(readFileSync(new URL('../../evals/lead-dedup/origination.v1.json',import.meta.url),'utf8'));
const record=(patch={})=>({...structuredClone(evaluation.records.nppes),...patch});
async function ingest(source,records,patch={}) {
  const coverage=(await read()).coverage.find(x=>x.source===source);
  return command('record-practice-origination-feed',{idempotency_key:randomUUID(),source,
    expected_checkpoint:coverage.checkpoint,checkpoint:randomUUID(),published_on:'2026-10-21',
    status:'available',detail:'Synthetic public-file import',records,...patch});
}

test('replayed evidence creates one dated card; exact NPI clients route to follow-up; ambiguity holds',async()=>{
  const before=(await c.query('select (select count(*) from party) parties,(select count(*) from client) clients')).rows;
  const first=await ingest('nppes',[record()]);
  assert.equal(first.inserted,1);
  assert.equal((await ingest('nppes',[record()])).inserted,0);
  assert.deepEqual((await c.query('select (select count(*) from party) parties,(select count(*) from client) clients')).rows,before);
  let result=await read();
  assert.equal(result.cards.length,1);
  assert.equal(result.cards[0].evidence_date,'2026-10-20');
  assert.match(result.cards[0].why_now,/registry updated on 2026-10-20/);
  assert.equal(result.cards[0].route,'research');
  assert.equal(result.outreach_started,false);
  const p=(await c.query(`insert into party(kind,name,npi,city,state,created_by,updated_by)
    values('org','Synthetic Dental','1234567890','Pensacola','FL',$1,$1) returning id`,[actor.id])).rows[0].id;
  await c.query(`insert into client(party_id,roster_ref,created_by,updated_by) values($1,'C-SYNTHETIC',$2,$2)`,[p,actor.id]);
  assert.equal((await read()).cards[0].route,'relationship_follow_up');
  await c.query(`insert into party(kind,name,city,state,created_by,updated_by)
    values('person','Synthetic Dental','Pensacola','FL',$1,$1)`,[actor.id]);
  assert.equal((await read()).cards[0].route,'identity_review');
  await ingest('nppes',[record({evidence_date:'2026-10-21'})]);
  result=await read();
  assert.equal(result.cards.length,1);
  assert.equal(result.cards[0].evidence_date,'2026-10-21');
  assert.equal(result.coverage.find(x=>x.source==='nppes').status,'available');
});

test('PECOS corrections use publication dates and unchanged later releases deduplicate',async()=>{
  const row=structuredClone(evaluation.records.pecos);
  assert.equal((await ingest('pecos',[row])).inserted,1);
  const later={...row,evidence_date:'2026-10-22'};
  assert.equal((await ingest('pecos',[later],{published_on:'2026-10-22'})).inserted,0);
  assert.equal((await ingest('pecos',[{...later,facts:{...row.facts,provider_type:'Group Practice'}}],
    {published_on:'2026-10-22'})).inserted,1);
  assert.equal((await ingest('pecos',[{...row,evidence_date:'2026-10-23'}],
    {published_on:'2026-10-23'})).inserted,1);
  const card=(await read()).cards.find(x=>x.source==='pecos');
  assert.equal(card.kind,'pecos_correction');
  assert.equal(card.date_basis,'publication');
  assert.equal(card.evidence_date,'2026-10-23');
  assert.equal((await read({limit:1})).next_cursor,card.id);
  assert.equal((await read({before_id:card.id})).cards.length,1);
});

test('address clusters require review and merged identities cannot supply a match',async()=>{
  const party=(await c.query(`insert into party(kind,name,city,state,created_by,updated_by)
    values('org','Synthetic Separate Practice','Pensacola','FL',$1,$1) returning id`,[actor.id])).rows[0].id;
  await c.query(`insert into record_flag(subject_type,subject_id,kind,value,source,created_by)
    values('party',$1,'address','{"found":true,"epistemic_status":"source_backed","street_address":"SYNTHETIC_ADDRESS_A"}',
      'https://example.invalid/synthetic',$2)`,[party,actor.id]);
  const street=['123','Main Street, Suite 200, Pensacola FL 32501-1234'].join(' ');
  await c.query(`update record_flag set value=jsonb_set(value,'{street_address}',to_jsonb($2::text)) where subject_id=$1`,[party,street]);
  const row=record({record_key:'1111111111',npi:'1111111111',name:'Synthetic New Practice',
    source_url:'https://npiregistry.cms.hhs.gov/provider-view/1111111111',
    facts:{...record().facts,address:['123','MAIN ST STE 200 PENSACOLA FL 32501'].join(' ')}});
  await ingest('nppes',[row]);
  assert.equal((await read()).cards.find(x=>x.npi==='1111111111').route,'identity_review');
  await c.query(`update record_flag set value=jsonb_set(value,'{street_address}',to_jsonb($2::text)) where subject_id=$1`,[party,['124','Main Street Suite 200 Pensacola FL 32501'].join(' ')]);
  assert.equal((await read()).cards.find(x=>x.npi==='1111111111').route,'research');
  await c.query('update party set merged_into=$1 where id=$2',[(await c.query("select id from party where npi='1234567890'")).rows[0].id,party]);
  assert.equal((await read()).cards.find(x=>x.npi==='1111111111').route,'research');
});

test('schema drift blocks the affected feed and preserves recovery; stale writers and invalid batches are atomic',async()=>{
  const before=(await read()).coverage.find(x=>x.source==='nppes');
  const blocked=await ingest('nppes',[],{status:'blocked',detail:'NPPES schema drift; missing Last Update Date'});
  assert.equal(blocked.checkpoint,before.checkpoint);
  const coverage=(await read()).coverage;
  assert.equal(coverage.find(x=>x.source==='nppes').status,'blocked');
  assert.equal(coverage.find(x=>x.source==='pecos').status,'available');
  await assert.rejects(()=>ingest('nppes',[record()],{expected_checkpoint:'stale'}));
  const cards=(await read()).cards;
  await assert.rejects(()=>ingest('nppes',[record(),record({npi:'not-an-npi'})]));
  assert.deepEqual((await read()).cards,cards);
  await assert.rejects(()=>ingest('formation-al',[]));
  await assert.rejects(()=>ingest('nppes',[record({source_url:'https://example.invalid/synthetic'})]));
  await assert.rejects(()=>ingest('pecos',[],{published_on:'2026-10-20'}));
});
