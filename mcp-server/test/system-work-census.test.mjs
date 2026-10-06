import test from 'node:test';
import assert from 'node:assert/strict';
import {readSystemWorkCensus,SYSTEM_WORK_LEGS,SYSTEM_WORK_KINDS,systemWorkActions,systemWorkTools} from '../src/system-work-census.v5.js';
const actor={slug:'joe',human:true,id:'00000000-0000-4000-8000-000000000001'};
const now=()=>new Date('2026-10-01T12:00:00Z');
const row=(kind,index=0)=>({id:`${kind}-${index}`,title:`Synthetic ${kind}`,state:'open',opened_at:'2026-08-01T12:00:00Z',last_activity_at:'2026-09-01T12:00:00Z',version:'3',completed:false,cancelled:false,identity:{}});
function fixture({down, live=false, many=false, cacheComplete=true}={}){
 const cache={schema:'system-work-external.v1',observed_at:now().toISOString(),complete:cacheComplete,items:['pull_request','remote_branch','builder_brief_file'].map(k=>({...row(k),kind:k,completed:live}))};
 const seen=[];
 return {seen,query:async(sql,params)=>{
  seen.push({sql,params});if(sql.includes("as cache"))return {rows:[{cache}]};
  if(sql.includes('ops.engineering_passport_facts'))return {rows:[{facts:null}]};
  const leg=SYSTEM_WORK_LEGS.find(l=>sql===l.sql||sql.includes(`(${l.sql})`));assert.ok(leg,'known source SQL');
  if(down===leg.kind)throw Object.assign(new Error('offline'),{code:'42501'});
  if(sql.includes('count(*) as count'))return {rows:[{count:many?4:1}]};
  let rows=Array.from({length:many?4:1},(_,i)=>({...row(leg.kind,i),completed:live}));
  if(params[5])rows=rows.filter(r=>r.id===params[5]);
  if(params.length===11)rows=rows.filter(r=>leg.kind>params[8]||(leg.kind===params[8]&&r.id>params[9]));
  return {rows:sql===leg.sql?rows:rows.slice(0,params.at(-1))};
 }};
}
const read=(client,args={})=>readSystemWorkCensus({client,actor,correlationId:'synthetic',now,...args});
test('all 19 source kinds appear with fixture counts and honest defect coverage',async()=>{
 const client=fixture();const result=await read(client);
 assert.deepEqual(result.items.map(r=>r.kind).sort(),[...SYSTEM_WORK_KINDS].sort());
 for(const c of result.coverage){assert.equal(c.count_total,1,c.kind);assert.equal(c.count_returned,1,c.kind);}
 assert.equal(result.census_complete,false);assert.equal(result.coverage.find(c=>c.kind==='defect').reason,'fix_status_not_recorded');
 assert.equal(client.seen.some(q=>/\b(insert|update|delete)\b/i.test(q.sql)),false);
});
test('equal timestamp keyset pages exhaust every leg without duplicates',async()=>{
 let cursor=null,ids=[];
 do{const p=await read(fixture({many:true}),{limit:3,cursor});ids.push(...p.items.map(r=>r.kind+':'+r.id));cursor=p.next_cursor;}while(cursor);
 assert.equal(ids.length,16*4+3);assert.equal(new Set(ids).size,ids.length);
});
test('cursor binds search, source and library filters',async()=>{
 const p=await read(fixture({many:true}),{limit:1});
 await assert.rejects(()=>read(fixture(),{cursor:p.next_cursor,text:'different'}),e=>e.code==='AUTHORIZATION_REFUSED');
 await assert.rejects(()=>read(fixture(),{kinds:'deal'}),e=>e.code==='AUTHORIZATION_REFUSED');
 const client=fixture();await read(client,{kinds:'loop',text:'_%',age:8});
 const query=client.seen.find(q=>q.sql.includes('census where'));
 assert.equal(query.params[4],'%\\_\\%%');assert.equal(query.params[3],'2026-09-23T12:00:00.000Z');
 assert.match(query.sql,/opened_at asc/);assert.match(query.sql,/id=\$6/);
});
test('authority is server-derived and personal loop/admission queries bind sponsor',async()=>{
 await assert.rejects(()=>readSystemWorkCensus({client:fixture(),actor:{slug:'unknown'},now}),e=>e.code==='AUTHORIZATION_REFUSED');
 for(const slug of ['joe','dell']){
  const client=fixture();await readSystemWorkCensus({client,actor:{slug,human:true},now,kinds:'loop,governance_item'});
  for(const q of client.seen){assert.equal(q.params[1],slug);}
 }
 const loop=SYSTEM_WORK_LEGS.find(l=>l.kind==='loop').sql;
 assert.match(loop,/domain='system'/);assert.match(loop,/l\.personal_to=/);assert.match(loop,/subject_type/);assert.match(loop,/'deal','lead','client','vendor','party'/);
 assert.match(SYSTEM_WORK_LEGS.find(l=>l.kind==='governance_item').sql,/r\.personal_to=/);
});
test('outage is incomplete and never empties working sources',async()=>{
 const r=await read(fixture({down:'investigation',cacheComplete:false}));
 assert.equal(r.census_complete,false);assert.ok(r.items.length>0);
 assert.equal(r.coverage.find(c=>c.kind==='investigation').state,'unavailable');
 assert.equal(r.coverage.find(c=>c.kind==='remote_branch').state,'unavailable');
});
test('Live query is independent of unfinished pages and sorts by latest activity',async()=>{
 const r=await read(fixture({live:true}),{live_library:true});assert.equal(r.items.length,19);
 assert.ok(r.items.every(i=>i.completed&&i.available_triage_actions.length===0));
});
test('Live GitHub coverage requires authenticated completed history in the cache',async()=>{
 const client=fixture({live:true}),query=client.query;
 client.query=async (...args)=>{const result=await query(...args);if(args[0].includes('as cache'))result.rows[0].cache.completed_pr_history=true;return result;};
 const result=await read(client,{live_library:true,kinds:'pull_request'});
 assert.equal(result.items.length,1);assert.equal(result.census_complete,true);
 const old=await read(fixture({live:true}),{live_library:true,kinds:'pull_request'});
 assert.equal(old.census_complete,false);assert.equal(old.coverage[0].reason,'cache_contains_open_github_work_only');
});
test('actions name source verbs, preserve typed identity and version semantics',()=>{
 const item={kind:'loop',identity:{loop_id:'synthetic-id',kind:'idea'},state:'open'};
 const actions=systemWorkActions(item);assert.deepEqual(actions.map(a=>a.verb),['close-loop','update-loop','update-loop']);
 assert.equal(actions[0].args.resolution,'dropped');assert.ok(actions.every(a=>a.versioned));
 assert.deepEqual(systemWorkActions({kind:'defect'}),[]);
 assert.equal(systemWorkActions({kind:'retrieval_proposal',id:'synthetic'} )[0].verb,'approve-retrieval-proposals');
 assert.equal(systemWorkActions({kind:'investigation',identity:{run_id:'synthetic'}})[0].verb,'close-investigation');
 assert.equal(systemWorkTools()['unfinished-work'].write,false);
});
function externalFixture(items, extra={}) {
 const client=fixture(),query=client.query;
 client.query=async (...args)=>args[0].includes('as cache')?{rows:[{cache:{schema:'system-work-external.v1',observed_at:now().toISOString(),complete:true,completed_pr_history:true,items,...extra}}]}:query(...args);
 return client;
}
test('malformed external arrays retain healthy database legs and explicit unavailable coverage',async()=>{
 for(const items of [{},null,[null]]){
  const result=await read(externalFixture(items),{kinds:'loop,pull_request'});
  assert.equal(result.items.filter(i=>i.kind==='loop').length,1);
  assert.equal(result.coverage.find(c=>c.kind==='pull_request').state,'unavailable');
  assert.equal(result.census_complete,false);
 }
});
test('rejected external dates and identities make coverage partial while valid rows survive',async()=>{
 for(const bad of [{opened_at:'invalid'},{last_activity_at:null},{id:null},{id:''}]){
  const rows=[{...row('pull_request'),kind:'pull_request'}, {...row('pull_request',1),kind:'pull_request',...bad}];
  const result=await read(externalFixture(rows),{kinds:'pull_request'});
  assert.equal(result.items.length,1);
  assert.equal(result.coverage[0].state,'partial');
  assert.equal(result.coverage[0].count_rejected,1);
  assert.equal(result.coverage[0].count_total,2);
  assert.equal(result.census_complete,false);
  assert.equal(result.source.freshness,'unknown');
 }
});
test('cursor binds exact identity and decoded values always refuse with typed error',async()=>{
 const rows=[{...row('pull_request'),kind:'pull_request'},{...row('pull_request',1),kind:'pull_request'}];
 const first=await read(externalFixture(rows),{kinds:'pull_request',limit:1});
 await assert.rejects(()=>read(externalFixture(rows),{kinds:'pull_request',cursor:first.next_cursor,id:'pull_request-1'}),e=>e.code==='AUTHORIZATION_REFUSED');
 for(const bad of ['null','[]','1','"text"','{}'])
  await assert.rejects(()=>read(externalFixture(rows),{cursor:Buffer.from(bad).toString('base64url')}),e=>e.code==='AUTHORIZATION_REFUSED');
});
test('paged external history is searchable beyond the first snapshot and gaps stay partial',async()=>{
 const pages=[{board_id:'carr-v5-external-synthetic-0',version:1,count:1},{board_id:'carr-v5-external-synthetic-1',version:1,count:1}];
 const client=externalFixture([], {schema:'system-work-external.v2',pages,item_count:2});
 const query=client.query;
 client.query=async(sql,params)=>sql.includes('board_id=any')?{rows:pages.map((p,i)=>({board_id:p.board_id,version:1,snapshot_json:{items:[{...row('pull_request',i),title:i?'Ancient merged feature':'Recent merged feature',kind:'pull_request',completed:true}]}}))}:query(sql,params);
 const result=await read(client,{live_library:true,kinds:'pull_request',text:'Ancient'});
 assert.equal(result.items.length,1);assert.equal(result.items[0].id,'pull_request-1');assert.equal(result.census_complete,true);
 const completeQuery=client.query;
 client.query=async(sql,params)=>{const r=await completeQuery(sql,params);return sql.includes('board_id=any')?{rows:r.rows.slice(0,1)}:r;};
 const gap=await read(client,{live_library:true,kinds:'pull_request'});
 assert.equal(gap.census_complete,false);assert.equal(gap.items.length,1);assert.equal(gap.coverage[0].state,'partial');
});
test('actions obey owning workflow states rather than advertising illegal writes',()=>{
 for(const state of ['triaged','ready','in_progress','blocked','declined','superseded','confirmed_closed'])
  assert.deepEqual(systemWorkActions({kind:'work_request',state,identity:{human_ref:'WR-000001'}}),[]);
 assert.deepEqual(systemWorkActions({kind:'work_request',state:'captured',identity:{human_ref:'WR-000001'}}).map(a=>a.verb),['decline-work-request','review-and-triage']);
 for(const state of ['triaged','investigating','recovering','monitoring','resolved','reviewed'])
  assert.deepEqual(systemWorkActions({kind:'incident',state,identity:{ref:'INC-synthetic'}}),[]);
 assert.equal(systemWorkActions({kind:'incident',state:'detected',identity:{ref:'INC-synthetic'}}).length,2);
 for(const kind of ['loop','investigation','retrieval_proposal','ready_plan_amendment','cutover_plan','slice_proposal','capability_session'])
  assert.deepEqual(systemWorkActions({kind,state:'cancelled',cancelled:true,identity:{sequence:1,stage:'cutover'}}),[]);
});
test('source navigation selects supported owning WRs or explicitly reports unavailable',async()=>{
 const client=fixture(),query=client.query;
 client.query=async(sql,params)=>{const result=await query(sql,params);if(!sql.includes('count(*) as count')&&!sql.includes('as cache'))
  for(const r of result.rows){r.identity={human_ref:'WR-000001',work_request:'WR-000001',board_id:'synthetic-board',task_id:'synthetic-task'};}
  return result;};
 const result=await read(client,{kinds:SYSTEM_WORK_LEGS.map(l=>l.kind).join(',')});
 for(const item of result.items){
  if(['work_request','work_shape','slice_plan','builder_brief','ready_plan_amendment'].includes(item.kind)){
   const target=new URL(item.link,'https://example.invalid');
   assert.equal(target.pathname,'/system-work.html');assert.equal(target.searchParams.get('work_request'),'WR-000001');
   assert.equal(item.navigation.state,'available');
  }else if(item.kind==='progress_task'){
   const target=new URL(item.link,'https://example.invalid');assert.equal(target.searchParams.get('board'),'synthetic-board');assert.equal(target.pathname,'/progress-board.html');
  }else{assert.equal(item.link,null,item.kind);assert.equal(item.navigation.state,'unavailable',item.kind);assert.ok(item.navigation.reason);}
 }
});
test('external row shape validation rejects typed garbage without losing valid rows',async()=>{
 const valid={...row('pull_request'),kind:'pull_request'};
 for(const bad of [{...valid,id:{}},{...valid,opened_at:[]},{...valid,title:{}},{...valid,completed:'false'},{...valid,identity:[]}]){
  const result=await read(externalFixture([valid,bad]),{kinds:'pull_request'});
  assert.equal(result.items.length,1);assert.equal(result.coverage[0].count_rejected,1);assert.equal(result.census_complete,false);
 }
});
test('non-actionable workflow states expose their canonical reader route',async()=>{
 const client=fixture(),query=client.query;
 client.query=async(sql,params)=>{const result=await query(sql,params);if(!sql.includes('count(*) as count')&&!sql.includes('as cache'))
  for(const r of result.rows){r.state='triaged';r.identity=sql.includes('ops.incident')?{ref:'INC-synthetic'}:{human_ref:'WR-000001'};}
  return result;};
 const result=await read(client,{kinds:'work_request,incident'});
 assert.deepEqual(result.items.map(i=>i.source_workflow.read.verb).sort(),['get-incident','work-request-card']);
 assert.deepEqual(result.items.find(i=>i.kind==='work_request').source_workflow.read.args,{work_request:'WR-000001'});
 for(const item of result.items)assert.deepEqual(item.available_triage_actions,[]);
});
