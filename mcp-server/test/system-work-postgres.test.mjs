// Schema-shape integration: run against isolated fixtures, never production.
import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import {readSystemWorkCensus,SYSTEM_WORK_LEGS} from '../src/system-work-census.v5.js';
const url=process.env.SYSTEM_WORK_TEST_DATABASE_URL;
async function sliceFixtures(db) {
  await db.query(`create temporary table census_work_fixture
   (id uuid,ref text,title text,state text,organization_tenant_id text,owner_actor text);
   create temporary table census_plan_fixture
   (id uuid,work_request_id uuid,created_at timestamptz,work_request_version bigint,
    plan jsonb,plan_digest text,accepted_plan_id uuid,accepted_plan_hash text);
   create temporary table census_mark_fixture(slice_id text,status text,created_at timestamptz,mark_seq bigint)`);
  return {query:(sql,params)=>{
   if(sql.includes('as cache'))return Promise.resolve({rows:[]});
   if(sql.includes('ops.engineering_passport_facts'))return Promise.resolve({rows:[{facts:null}]});
   return db.query(sql.replaceAll('ops.work_request','pg_temp.census_work_fixture')
    .replaceAll('ops.engineering_slice_plan','pg_temp.census_plan_fixture')
    .replaceAll('ops.slice_completion_mark','pg_temp.census_mark_fixture'),params);
  }};
}
test('bare business subjects stay excluded alongside recursive business identities',{skip:!url},async()=>{
 const db=new pg.Client({connectionString:url});await db.connect();
 try{
  await db.query('begin');
  await db.query(`create temporary table business_loop_fixture as select * from public.loop_item with no data`);
  const subjects=[{},...['deal','lead','client','vendor','party'].flatMap(type=>[
   {subject:type},{nested:{subject:type}},{nested:{[type+'_id']:'synthetic'}},{subject_ref:type+':synthetic'}])];
  for(const [i,extra] of subjects.entries())await db.query(`insert into business_loop_fixture
   (id,kind,title,status,tier,domain,extra_cells,created_at,updated_at,version)
   values(gen_random_uuid(),'idea','Synthetic subject','open','shared','system',$1,now(),now(),1)`,[extra]);
  const client={query:(sql,params)=>db.query(sql.replaceAll('public.loop_item','pg_temp.business_loop_fixture'),params)};
  const result=await readSystemWorkCensus({client,actor:{slug:'joe',human:true},kinds:'loop'});
  assert.equal(result.coverage[0].state,'complete');
  assert.equal(result.items.length,1);
  assert.equal(result.coverage[0].count_total,1);
 }finally{await db.query('rollback');await db.end();}
});
test('actual PostgreSQL legs compile, grants execute and private/business loops are excluded',{skip:!url},async()=>{
 const client=new pg.Client({connectionString:url});await client.connect();
 try{
 await client.query('begin');
 const actors=(await client.query("select id from public.actor where slug in ('joe','dell') order by slug desc")).rows;
 const ids=actors.map(a=>a.id);assert.equal(ids.length,2);
 const block=(await client.query("insert into public.loop_block(rel_path,kind,seq,created_by,updated_by) values('Synthetic fixture','idea',1,$1,$1) returning id",[ids[0]])).rows[0].id;
 for(const [index,tier,personal,domain,subject] of [[1,'shared',null,'system',{}],[2,'personal',ids[0],'system',{}],[3,'personal',ids[1],'system',{}],[4,'shared',null,'deals',{}],[5,'shared',null,'system',{subject_type:'client'}],[6,'shared',null,'system',{subject:{deal_id:'synthetic'}}],
  ...['deal','lead','client','vendor','party'].flatMap((type,i)=>[[10+i*4,'shared',null,'system',{nested:{[type+'_id']:'synthetic'}}],
   [11+i*4,'shared',null,'system',{subject_ref:type+':synthetic'}],
   [12+i*4,'shared',null,'system',{nested:{ref:type+'/synthetic'}}],
   [13+i*4,'shared',null,'system',{subject:{type,ref:'synthetic'}}]])]){
  await client.query(`insert into public.loop_item(kind,number,block_id,render_seq,title,tier,personal_to,domain,extra_cells,created_by,updated_by)
  values('idea',$1::text,$2,$1::integer,'Synthetic loop',$3,$4,$5,$6,$7,$7)`,[String(index),block,tier,personal,domain,subject,ids[0]]);
 }
 await client.query('set local role carr_reader');
 for(const slug of ['joe','dell']){
 const result=await readSystemWorkCensus({client,actor:{slug,human:true},correlationId:'synthetic',kinds:SYSTEM_WORK_LEGS.map(l=>l.kind).join(',')});
 for(const c of result.coverage) assert.notEqual(c.state,'unavailable',`${slug} ${c.kind}: ${c.reason}`);
 const loops=result.items.filter(i=>i.kind==='loop');assert.equal(loops.length,2);assert.ok(loops.every(i=>i.identity.kind==='idea'));
 const only=await readSystemWorkCensus({client,actor:{slug,human:true},correlationId:'synthetic',kinds:'loop',limit:1});
 const second=await readSystemWorkCensus({client,actor:{slug,human:true},correlationId:'synthetic',kinds:'loop',limit:1,cursor:only.next_cursor});
 assert.notEqual(only.items[0].id,second.items[0].id);assert.equal(second.next_cursor,null);
 }
 await client.query('rollback');
 }finally{await client.end();}
});
test('progress census requires explicit system classification',{skip:!url},async()=>{
 const client=new pg.Client({connectionString:url});await client.connect();
 try{await client.query('begin');
 await client.query(`insert into public.board_snapshot(organization_tenant_id,sponsoring_human_slug,board_id,snapshot_json,updated_by_actor_id)
 values('carr-internal','joe','synthetic-classification',$1,(select id from public.actor where slug='joe'))`,[JSON.stringify({tasks:{
  unknown:{repo:'jbookout/carr-system',title:'Synthetic unknown'},
  system:{repo:'jbookout/carr-system',domain:'system',title:'Synthetic system'},
  business:{repo:'jbookout/carr-system',domain:'deals',title:'Synthetic business'}}})]);
 await client.query('set local role carr_reader');
 const result=await readSystemWorkCensus({client,actor:{slug:'joe',human:true},kinds:'progress_task'});
 assert.equal(result.coverage[0].state,'complete');
 assert.deepEqual(result.items.map(i=>i.identity.task_id),['system']);
 }finally{await client.query('rollback');await client.end();}
});
test('canonical slice members drive mixed, complete, reopened and superseded plan status',{skip:!url},async()=>{
 const client=new pg.Client({connectionString:url});await client.connect();
 try{await client.query('begin');
 const census=await sliceFixtures(client);
 const work=(await client.query(`insert into census_work_fixture(id,ref,title,state,organization_tenant_id)
 values(gen_random_uuid(),'WR-999991','Synthetic canonical plan','in_progress','carr-internal') returning id`)).rows[0].id;
 const insertPlan=async(revision,slices,date)=>(await client.query(`insert into census_plan_fixture
 (id,work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,plan_digest,plan,created_at)
 values(gen_random_uuid(),$1,gen_random_uuid(),$2,1,$2,$3,$4) returning id`,[work,'sha256:'+'1'.repeat(64),JSON.stringify({
 schema_version:'engineering-slice-plan.v1',work_request:{id:'wr:'+work,state_version:1,canonical_record_digest:'sha256:'+'1'.repeat(64)},
 accepted_plan_revision:{id:'PLAN-synthetic',revision,digest:'sha256:'+'1'.repeat(64)},plan_digest:'sha256:'+'1'.repeat(64),slices:slices.map(slice_ref=>({slice_ref}))}),date])).rows[0].id;
 const old=await insertPlan(1,['synthetic-old'],'2026-09-01T00:00:00Z');
 const current=await insertPlan(2,['synthetic-a','synthetic-b'],'2026-09-02T00:00:00Z');
 const mark=async(ref,status,seq)=>client.query(`insert into census_mark_fixture(slice_id,status,mark_seq,created_at)
 values($1,$2,$3,'2026-09-10T00:00:00Z'::timestamptz + $3::bigint * interval '1 second')`,[ref,status,seq]);
 await mark('synthetic-a','complete',1);await mark('synthetic-old','complete',2);
 const read=live_library=>readSystemWorkCensus({client:census,actor:{slug:'joe',human:true},kinds:'slice_plan',live_library});
 let unfinished=await read(false);assert.deepEqual(unfinished.items.map(i=>i.id),[current]);assert.equal((await read(true)).items.length,0);
 await mark('synthetic-b','complete',3);
 assert.equal((await read(false)).items.length,0);assert.deepEqual((await read(true)).items.map(i=>i.id),[current]);
 await mark('synthetic-a','in_progress',4);
 unfinished=await read(false);assert.deepEqual(unfinished.items.map(i=>i.id),[current]);assert.equal((await read(true)).items.length,0);
 assert.ok(!unfinished.items.some(i=>i.id===old));
 }finally{await client.query('rollback');await client.end();}
});

test('slice-plan census binds PostgreSQL parameters before Passport completion', {skip:!url}, async()=>{
 const db=new pg.Client({connectionString:url});await db.connect();
 try{
  await db.query('begin');
  const client=await sliceFixtures(db);
  const read=live_library=>readSystemWorkCensus({client,actor:{slug:'joe',human:true},kinds:'slice_plan',live_library});
  const empty=await read(false);
  assert.equal(empty.coverage[0].state,'complete');assert.equal(empty.census_complete,true);
  assert.equal(empty.coverage[0].count_total,0);
  await db.query(`insert into census_work_fixture values
   ('00000000-0000-4000-8000-000000000001','WR-999991','Synthetic plan','in_progress','carr-internal','joe');
   insert into census_plan_fixture values
   ('00000000-0000-4000-8000-000000000002','00000000-0000-4000-8000-000000000001',now(),1,
    '{"slices":[{"slice_ref":"synthetic-pg-slice"}],"accepted_plan_revision":{"revision":1}}',
    'sha256:synthetic','00000000-0000-4000-8000-000000000003','sha256:synthetic')`);
  const unfinished=await read(false);
  assert.equal(unfinished.census_complete,true);assert.equal(unfinished.coverage[0].count_total,1);
  assert.equal(unfinished.items[0].id,'00000000-0000-4000-8000-000000000002');
  await db.query(`insert into census_mark_fixture values('synthetic-pg-slice','complete',now(),1)`);
  assert.equal((await read(false)).items.length,0);
  const live=await read(true);assert.equal(live.census_complete,true);
  assert.equal(live.coverage[0].count_total,1);assert.equal(live.items[0].state,'complete');
 }finally{await db.query('rollback');await db.end();}
});
