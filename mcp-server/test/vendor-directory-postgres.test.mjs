import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import pg from 'pg';
import { randomUUID } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { TOOLS } from '../src/tools.js';
import { parseBusinessQuery, readBusinessList, readBusinessRecord } from '../src/workspace-business-read.js';
function disposableClient(dsn) {
  const refuse = () => { throw new Error('REFUSED: disposable database requires loopback with an explicit port and no connection overrides'); };
  let url, db;
  try { url = new URL(dsn); db = new pg.Client({connectionString:dsn}); }
  catch { refuse(); }
  const hosts = ['127.0.0.1','localhost'];
  if (!['postgres:','postgresql:'].includes(url.protocol) || !hosts.includes(url.hostname) || !url.port || url.search || url.hash || !hosts.includes(db.connectionParameters.host) || db.connectionParameters.host !== url.hostname || db.connectionParameters.port !== Number(url.port)) refuse();
  return db;
}

test('disposable guard refuses effective host and port overrides before connecting', () => {
 for(const host of ['127.0.0.1','localhost']) {
  assert.equal(disposableClient(`postgres://demo@${host}:5432/demo`).connectionParameters.host,host);
  for(const query of ['host=example.invalid','host=127.0.0.1','port=5433','port=5432'])
   assert.throws(()=>disposableClient(`postgres://demo@${host}:5432/demo?${query}`));
 }
});

test('refusal output never discloses database userinfo or the full URL', () => {
 const password=`synthetic-${randomUUID()}`, dsn=`postgres://synthetic-user:${password}@example.invalid:5432/demo`; // ci-secret-scan: allow — generated refusal-test credential, never connected
 const env={...process.env,DATABASE_URL:dsn}; delete env.NODE_TEST_CONTEXT;
 const result=spawnSync(process.execPath,['--test','--test-name-pattern=^W5 PostgreSQL:',new URL(import.meta.url).pathname],{env,encoding:'utf8',timeout:10000});
 assert.notEqual(result.status,0);
 const output=result.stdout+result.stderr;
 for(const secret of [password,dsn,'synthetic-user']) assert.equal(output.includes(secret),false,'refusal must be sanitized');
});

test('W5 database proof accepts both loopback names and refuses external hosts', () => {
  for (const host of ['127.0.0.1','localhost']) assert.ok(disposableClient(`postgres://demo@${host}:5432/demo`));
  for (const host of ['example.com','localhost.example.com','127.0.0.1.example.com','postgres']) assert.throws(()=>disposableClient(`postgres://demo@${host}:5432/demo`));
});

test('vendor contract migration set has no duplicate constraint installations', async () => {
 const folder=new URL('../../migrations/',import.meta.url),owners=new Map();
 for(const name of (await readdir(folder)).filter(name=>name.endsWith('_vendor_relationship_contract.sql')).sort()) {
  for(const [,constraint] of (await readFile(new URL(name,folder),'utf8')).matchAll(/alter table public\.vendor add constraint ([a-z_]+)/g)) {
   assert.ok(!owners.has(constraint),`${constraint} is installed by both ${owners.get(constraint)} and ${name}`);
   owners.set(constraint,name);
  }
 }
 assert.deepEqual([...owners.keys()].sort(),['vendor_deal_evidence_array','vendor_trust_override_shape']);
});

test('W5 PostgreSQL: vendor contract migrations install together on an empty schema', async t => {
 const dsn=process.env.DATABASE_URL;
 if(!dsn){assert.notEqual(process.env.CARR_VENDOR_DIRECTORY_DB_REQUIRED,'1');t.skip('disposable PostgreSQL required');return;}
 const db=disposableClient(dsn);await db.connect();
 const schema='vendor_contract_'+randomUUID().replaceAll('-','');
 try {
  await db.query('begin');await db.query(`create schema ${schema}`);
  await db.query(`create table ${schema}.vendor(id uuid);create table ${schema}.activity(id uuid,vendor_id uuid,occurred_at timestamptz);create table ${schema}.party_link(id uuid);create table ${schema}.deal(id uuid)`);
  const folder=new URL('../../migrations/',import.meta.url);
  const migrations=(await readdir(folder)).filter(name=>name.endsWith('_vendor_relationship_contract.sql')).sort();
  assert.ok(migrations.length,'vendor schema installation must be present');
  for(const name of migrations)await db.query((await readFile(new URL(name,folder),'utf8')).replaceAll('public.',schema+'.'));
  assert.deepEqual((await db.query('select conname from pg_constraint where connamespace=$1::regnamespace order by conname',[schema])).rows.map(row=>row.conname),['vendor_deal_evidence_array','vendor_trust_override_shape']);
 } finally {await db.query('rollback');await db.end();}
});

test('W5 PostgreSQL: sourced stats, partner override, audit, replay, CAS and reader grants', async t => {
  const dsn = process.env.DATABASE_URL;
  if (!dsn) { assert.notEqual(process.env.CARR_VENDOR_DIRECTORY_DB_REQUIRED, '1'); t.skip('disposable PostgreSQL required'); return; }
  const db = disposableClient(dsn); await db.connect();
  await db.query('begin');
  try {
    const actor = {...(await db.query("select id,slug from public.actor where slug='joe'")).rows[0],human:true};
    const partner = {...(await db.query("select id,slug from public.actor where slug='dell'")).rows[0],human:true};
    const party = async name => (await db.query("insert into public.party(kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id",[name,actor.id])).rows[0].id;
    const suffix=randomUUID(), vp=await party('Demo vendor '+suffix), cp=await party('Demo practice '+suffix);
    const client=(await db.query("insert into public.client(party_id,roster_ref,created_by,updated_by,vertical,deal_type_label,owner_id) values ($1,$2,$3,$3,'dental','Lease',$3) returning id",[cp,'C-DEMO-'+suffix,actor.id])).rows[0].id;
    const vendor=(await db.query("insert into public.vendor(party_id,vendor_ref,category,territory,owner_id,created_by,updated_by,verticals) values ($1,$2,'banker','Demo North',$3,$3,$3,array['dental']) returning id",[vp,'V-DEMO-'+suffix,actor.id])).rows[0].id;
    const deal=(await db.query("insert into public.deal(client_id,name,deal_type,phase,outcome,closed_on,created_by,updated_by,salesforce_id) values ($1,$2,'lease','closed','won','2026-09-01',$3,$3,$4) returning id",[client,'Demo deal '+suffix,actor.id,'DEMO-SF-'+suffix])).rows[0].id;
    await db.query("insert into public.activity(occurred_at,actor_id,kind,summary,detail,vendor_id,source) values ('2026-09-30',$1,'email_in','Demo financing conversation','Original demo email entry',$2,'email'),('2026-09-29',$1,'meeting','Demo calendar meeting','Original demo calendar entry',$2,'calendar')",[actor.id,vendor]);
    await db.query("insert into public.party_link(from_party,to_party,kind,note,source,created_by,occurred_on) values ($1,$2,'intro','Demo introduction','stated',$3,'2026-09-23'),($1,$2,'can_introduce','Demo suggested introduction','stated',$3,null)",[vp,cp,actor.id]);
    const call = async (who, args) => {await db.query('savepoint action');try {const r=await TOOLS['update-vendor'].handler(db,who,structuredClone(args));await db.query('release savepoint action');return r;}catch(e){await db.query('rollback to savepoint action');throw e;}};
    const version = async () => (await db.query('select version from public.vendor where id=$1',[vendor])).rows[0].version;
    const request = async fields => ({vendor,base_version:await version(),fields,idempotency_key:randomUUID()});
    const read = async () => readBusinessRecord({client:db,actor,dataset:'vendors',id:vendor,contract:'vendor-directory.v1',correlationId:'demo',now:()=>new Date('2026-10-01')});
    assert.equal((await read()).record.relationship.deals_worked,null,'unknown history does not become zero');
    for(const occurred_at of [0,'1','2026-02-30']) await t.test(`invalid ${JSON.stringify(occurred_at)} refuses before persisted reads`,async()=>{
      await db.query('savepoint invalid_fixture');
      try {
        await assert.rejects(call(actor,await request({deal_evidence:[{deal_id:deal,role:'worked',occurred_at,evidence_kind:'entry',evidence_ref:'synthetic'}]})),e=>e.payload?.error==='deal_evidence_invalid');
        for(const contract of [undefined,'vendor-directory.v1']) {
          const list=await readBusinessList({client:db,actor,query:parseBusinessQuery('vendors',new URLSearchParams(contract?{contract}:{}),'joe'),correlationId:'synthetic'});
          assert.ok(list.rows);
          assert.equal((await readBusinessRecord({client:db,actor,dataset:'vendors',id:vendor,contract,correlationId:'synthetic'})).record.id,vendor);
        }
      } finally {await db.query('rollback to savepoint invalid_fixture');}
    });
    for(const role of ['worked','referred']) await t.test(`mixed-case ${role} evidence refuses`,async()=>{
      await db.query('savepoint duplicate_fixture');
      try {
        const entry={deal_id:deal,role,occurred_at:'2022-01-01',evidence_kind:'entry',evidence_ref:'synthetic'};
        await assert.rejects(call(actor,await request({deal_evidence:[entry,{...entry,deal_id:deal.toUpperCase()}]})),e=>e.payload?.error==='deal_evidence_duplicate');
      } finally {await db.query('rollback to savepoint duplicate_fixture');}
    });
    for(const who of [{...actor,human:false},{...actor,slug:'automation'}]) for(const fields of [{verify_deal_history:true},{trust_override:{tier:'Trial',reason:'Synthetic'}}])
      await assert.rejects(call(who,await request(fields)),e=>['AUTHORIZATION_REFUSED','deal_history_verification_refused'].includes(e.payload?.error));
    const evidence={deal_id:deal,role:'worked',occurred_at:'2022-01-01',evidence_kind:'salesforce',evidence_ref:'DEMO-SF-'+suffix};
    await call(actor,await request({deal_evidence:[evidence,{...evidence,role:'referred',evidence_kind:'mail',evidence_ref:'demo-mail-entry'}],verify_deal_history:true,loan_programs:['Demo equipment financing']}));
    let result=await read();assert.equal(result.record.relationship.deals_worked,1);assert.equal(result.record.relationship.deals_referred,1);assert.equal(result.record.relationship.win_rate,1);assert.equal(result.record.relationship.computed_tier,'Trial');assert.equal(result.record.relationship.last_contact_note,'Demo financing conversation');assert.equal(result.record.relationship.recent_entries[0].detail,'Original demo email entry');assert.deepEqual(result.record.loan_programs,['Demo equipment financing']);assert.deepEqual(result.record.relationship.introductions.map(item=>item.kind).sort(),['can_introduce','intro']);
    await t.test('evidence event new value matches the stored JSON array',async()=>{
      const row=(await db.query("select v.deal_evidence,e.new_value->'deal_evidence' as logged from public.vendor v join public.event e on e.subject_id=v.id where v.id=$1 and e.field='deal_evidence' order by e.id desc limit 1",[vendor])).rows[0];
      assert.ok(Array.isArray(row.logged));assert.deepEqual(row.logged,row.deal_evidence);
    });
    const override=await request({trust_override:{tier:'Established',reason:'Demo reviewed exception'}});
    await call(partner,override);const firstVersion=await version();assert.equal((await call(partner,override)).replayed,true);assert.equal(await version(),firstVersion);
    result=await read();assert.equal(result.record.relationship.override.recorded_by,'dell');assert.equal(result.record.relationship.override.reason,'Demo reviewed exception');assert.equal(result.record.relationship.computed_tier,'Trial');
    const audit=await db.query("select new_value from public.event where subject_id=$1 and field='trust_override'",[vendor]);assert.equal(audit.rows.length,1);assert.equal(audit.rows[0].new_value.trust_override.recorded_by,'dell');
    await assert.rejects(call(actor,{...override,idempotency_key:randomUUID()}),e=>e.payload?.error==='version_conflict');
    await assert.rejects(call({...actor,slug:'automation'},await request({trust_override:{tier:'Proven',reason:'Demo'}})),e=>e.payload?.error==='AUTHORIZATION_REFUSED');
    await call(actor,await request({deal_evidence:[{...evidence,evidence_kind:'calendar',evidence_ref:'demo-calendar-entry'}]}));assert.equal((await read()).record.relationship.deals_worked,null,'new evidence invalidates coverage');
    await call(actor,await request({trust_override:null}));assert.equal((await read()).record.relationship.override,null);
    for(const populated of [false,true]) await t.test(`merge preserves relationship data with ${populated?'populated':'empty'} survivor`,async()=>{
      await db.query('savepoint merge_fixture');
      try {
        const make=async ref=>(await db.query("insert into public.vendor(party_id,vendor_ref,category,created_by,updated_by) values ($1,$2,'banker',$3,$3) returning id",[vp,ref,actor.id])).rows[0].id;
        const survivor=await make('V-SURV-'+randomUUID()), loser=await make('V-LOSER-'+randomUUID());
        const a={...evidence,deal_id:deal.toUpperCase(),occurred_at:'2022-01-01T00:00:00.000Z'}, b={...evidence,role:'referred',occurred_at:'2023-01-01T00:00:00.000Z'};
        const override={tier:'Trial',reason:'Synthetic loser',recorded_by:'joe',recorded_at:'2026-10-01T12:00:00Z'};
        await db.query('update public.vendor set deal_evidence=$2,deal_history_verified_at=$3,loan_programs=$4,trust_override=$5 where id=$1',[loser,JSON.stringify([a,b]),override.recorded_at,['Loser program'],override]);
        if(populated) await db.query('update public.vendor set deal_evidence=$2,deal_history_verified_at=$3,loan_programs=$4,trust_override=$5 where id=$1',[survivor,JSON.stringify([{...a,deal_id:deal,occurred_at:'2024-01-01T00:00:00.000Z'}]),override.recorded_at,['Survivor program'],{...override,tier:'Established',reason:'Synthetic survivor'}]);
        const result=await TOOLS['merge-vendor-rows'].handler(db,actor,{survivor_vendor:survivor,merged_vendor:loser,idempotency_key:randomUUID()});
        const stored=(await db.query('select deal_evidence,deal_history_verified_at,loan_programs,trust_override from public.vendor where id=$1',[survivor])).rows[0];
        assert.equal(stored.deal_evidence.length,2);assert.equal(stored.deal_evidence[0].deal_id,deal);
        assert.deepEqual(stored.loan_programs,populated?['Survivor program','Loser program']:['Loser program']);
        assert.equal(stored.trust_override.tier,populated?'Established':'Trial');
        if(populated) {
          assert.equal(stored.deal_history_verified_at,null);
          assert.ok(result.conflicts_left_for_human.some(c=>c.field==='trust_override'));
          assert.ok(result.conflicts_left_for_human.some(c=>c.field==='deal_evidence'));
        } else assert.equal(stored.deal_history_verified_at.toISOString(),new Date(override.recorded_at).toISOString());
        assert.equal((await readBusinessRecord({client:db,actor,dataset:'vendors',id:survivor,contract:'vendor-directory.v1',correlationId:'merge'})).record.loan_programs.length,populated?2:1);
      } finally {await db.query('rollback to savepoint merge_fixture');}
    });
    await db.query('set local role carr_reader');
    for(const dataset of ['clients','vendors']) for(const sort of ['name','vertical','deal_type','last_deal_desc','last_deal_asc',...(dataset==='vendors'?['territory']:[])]) {
      const query=parseBusinessQuery(dataset,new URLSearchParams({contract:'vendor-directory.v1',q:'Demo',sort,owner:'joe',...(dataset==='vendors'?{territory:'Demo North'}:{})}),'joe');
      const list=await readBusinessList({client:db,actor,query,correlationId:'demo'});assert.ok(list.rows.some(row=>row.id===(dataset==='vendors'?vendor:client)),sort);assert.equal(list.query.owner,'joe');
    }
    assert.equal((await read()).record.id,vendor,'detail read uses reader grants');
  } finally {await db.query('rollback');await db.end();}
});

test('W5 PostgreSQL: every sort, owner and territory excludes competing rows with stable pages', async t => {
 const dsn=process.env.DATABASE_URL;
 if(!dsn) {assert.notEqual(process.env.CARR_VENDOR_DIRECTORY_DB_REQUIRED,'1');t.skip('disposable PostgreSQL required');return;}
 const db=disposableClient(dsn);await db.connect();await db.query('begin');
 try {
  const actors=(await db.query("select id,slug from public.actor where slug in ('joe','dell')")).rows;
  const actor={...actors.find(a=>a.slug==='joe'),human:true};
  const categories=(await db.query('select slug,label from public.vendor_category order by label limit 2')).rows;
  assert.equal(categories.length,2);
  const prefix='SortFixture-'+randomUUID();
  const fixtures={clients:[],vendors:[]};
  for(let i=0;i<30;i++) for(const dataset of ['clients','vendors']) {
   const owner=i%3===0?'dell':'joe', ownerId=actors.find(a=>a.slug===owner).id;
   const name=prefix+' '+String(Math.floor((29-i)/2)).padStart(2,'0');
   const party=(await db.query("insert into public.party(kind,name,created_by,updated_by) values ('person',$1,$2,$2) returning id",[name,actor.id])).rows[0].id;
   const vertical=i%5===0?null:i%2?'dental':'vet', territory=i%5===0?null:i%2?'South':'North';
   const date=i%5===0?null:`2026-09-${String(1+i%4).padStart(2,'0')}`;
   const category=categories[i%2], type=i%5===0?null:i%2?'Lease':'Purchase';
   const stamp=`2026-09-${String(1+i%7).padStart(2,'0')}T12:00:00Z`;
   let id;
   if(dataset==='clients') {
    id=(await db.query('insert into public.client(party_id,roster_ref,created_by,updated_by,owner_id,vertical,deal_type_label,updated_at) values ($1,$2,$3,$3,$4,$5,$6,$7) returning id',[party,'C-'+randomUUID(),actor.id,ownerId,vertical,type,stamp])).rows[0].id;
    if(date) await db.query("insert into public.deal(client_id,name,deal_type,phase,outcome,closed_on,created_by,updated_by) values ($1,$2,'lease','closed','won',$3,$4,$4)",[id,prefix,date,actor.id]);
   } else {
    const linked=(await db.query('select id from public.deal where client_id=$1',[fixtures.clients[i].id])).rows[0];
    const evidence=linked?[{deal_id:linked.id,role:'worked',occurred_at:new Date(date).toISOString(),evidence_kind:'entry',evidence_ref:'synthetic-sort'}]:[];
    id=(await db.query("insert into public.vendor(party_id,vendor_ref,category,created_by,updated_by,owner_id,verticals,category_slug,territory,deal_evidence,updated_at) values ($1,$2,'synthetic-unrecorded',$3,$3,$4,$5,$6,$7,$8,$9) returning id",[party,'V-'+randomUUID(),actor.id,ownerId,vertical?[vertical]:null,i%5===0?null:category.slug,territory,JSON.stringify(evidence),stamp])).rows[0].id;
   }
   fixtures[dataset].push({id,name,owner,territory,vertical,deal_type:dataset==='clients'?type:i%5===0?null:category.label,last_deal_at:date,updated_at:stamp});
  }
  await db.query('set local role carr_reader');
  const compare=(a,b,key,desc=false)=>{
   const x=a[key]?.toLowerCase(),y=b[key]?.toLowerCase();
   if(x==null&&y!=null)return 1;if(y==null&&x!=null)return -1;
   const primary=x==null?0:x<y?-1:x>y?1:0;
   return (desc?-primary:primary)||(key==='updated_at'?0:a.name.localeCompare(b.name))||a.id.localeCompare(b.id);
  };
  const keys={name:'name',recent:'updated_at',vertical:'vertical',deal_type:'deal_type',last_deal_asc:'last_deal_at',last_deal_desc:'last_deal_at',territory:'territory'};
  const verify=async(client,dataset,sort,owner='all',territory=null)=>{
   const expected=fixtures[dataset].filter(r=>(owner==='all'||r.owner===owner)&&(!territory||r.territory===territory)).sort((a,b)=>compare(a,b,keys[sort],sort==='recent'||sort==='last_deal_desc')).map(r=>r.id);
   const actual=[];
   for(let page=1;page<=Math.max(1,Math.ceil(expected.length/25));page++) {
    const query=parseBusinessQuery(dataset,new URLSearchParams({contract:'vendor-directory.v1',q:prefix,sort,owner,page:String(page),...(territory?{territory}:{})}),'joe');
    const result=await readBusinessList({client,actor,query,correlationId:'sort-fixture'});
    assert.equal(result.total,expected.length);actual.push(...result.rows.map(r=>r.id));
   }
   assert.deepEqual(actual,expected,`${dataset}/${sort}/${owner}/${territory}`);
  };
  for(const dataset of ['clients','vendors']) for(const sort of Object.keys(keys).filter(s=>dataset==='vendors'||s!=='territory')) {
   await verify(db,dataset,sort);
   for(const owner of ['joe','dell']) await verify(db,dataset,sort,owner);
   if(dataset==='vendors') for(const territory of ['North','South','Missing']) for(const owner of ['all','joe','dell']) await verify(db,dataset,sort,owner,territory);
   if(!['name','recent'].includes(sort)) {
    const orders={vertical:'lower(f.vertical) asc nulls last, lower(f.name), f.id',deal_type:'lower(f.deal_type) asc nulls last, lower(f.name), f.id',last_deal_desc:'f.last_deal_at desc nulls last, lower(f.name), f.id',last_deal_asc:'f.last_deal_at asc nulls last, lower(f.name), f.id',territory:'lower(f.territory) asc nulls last, lower(f.name), f.id'};
    const mutant={query:(sql,values)=>db.query(sql.replaceAll(orders[sort],'lower(f.name) asc, f.id asc'),values)};
    // A separate mutation per comparator must fail on ordered IDs.
    await assert.rejects(verify(mutant,dataset,sort),e=>e.code==='ERR_ASSERTION');
   }
  }
 } finally {await db.query('rollback');await db.end();}
});
