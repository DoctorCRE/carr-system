import test from 'node:test';
import assert from 'node:assert/strict';
import { readSystemWorkCensus } from '../src/system-work-census.v5.js';

const actor = { slug: 'joe', human: true };
const now = () => new Date('2026-10-01T12:00:00Z');
test('a census row whose linkage was not read never claims it is unlinked', async () => {
  const row = { id: 'demo-shape', title: 'Demo bounded work', state: 'ready', version: '1',
    opened_at: now().toISOString(), last_activity_at: now().toISOString(), completed: false, cancelled: false,
    identity: { work_request: 'WR-900' } };
  const client = { query: async sql => ({ rows: sql.includes('as cache') ? []
    : sql.includes('count(*) as count') ? [{ count: 1 }] : [row] }) };
  const read = await readSystemWorkCensus({ client, actor, now, kinds: 'work_shape' });
  assert.equal(read.items.length, 1);
  assert.notEqual(read.items[0].unlinked, true, 'a discarded relationship is not an absent relationship');
  assert.equal(read.items[0].linkage?.schema, 'work-linkage.v1');
  assert.equal(read.items[0].linkage.state, 'unavailable');
});
import { boardAnswerTools } from '../src/board-answers.js';

const stored = tasks => ({board_id:'demo-project',version:7,snapshot_json:{schema:'carr-progress-board.v2',tasks}});
async function boardRead(tasks) {
 const client={query:async sql=>({rows:sql.includes('from board_snapshot')?[stored(tasks)]:[]})};
 return (await boardAnswerTools({})['read-progress-board'].handler(client,actor,{board_id:'demo-project'})).snapshot;
}
test('a fully read task with no recorded references has a source-bound empty observation',async()=>{
 const snapshot=await boardRead({empty:{title:'Standalone work',task_id:'domain:opaque',linkage_metadata_version:1}});
 assert.deepEqual(snapshot.snapshot_json.tasks.empty.linkage,{schema:'work-linkage.v1',state:'complete',
 subject:{board_id:'demo-project',task_id:'empty',version:7},references:[],reason:null});
});
test('blocker keys resolve exact recorded targets while dangling and self references remain unresolved',async()=>{
 const snapshot=await boardRead({dependent:{title:'Dependent',blocked_by_task_ids:['blocker','missing','dependent','blocker'],parent_task_id:'blocker'},
 blocker:{title:'Recorded blocker',task_id:'different-domain-reference'}});
 const refs=snapshot.snapshot_json.tasks.dependent.linkage.references;
 assert.deepEqual(refs.map(r=>[r.relation,r.ref,r.state]),[
 ['blocked_by','blocker','resolved'],['blocked_by','missing','unresolved'],['blocked_by','dependent','unresolved'],['parent','blocker','resolved']]);
 assert.deepEqual(refs[0].target,{board_id:'demo-project',task_id:'blocker'});
 assert.equal(refs[0].title,'Recorded blocker');
 assert.equal(refs[2].target,null);
});
test('malformed reference structures cannot certify an empty or complete linkage read',async()=>{
 for(const task of [{blocked_by_task_ids:'blocker'},{blocked_by_task_ids:[42]},{parent_task_id:{}},{related:[{what:'unknown'}]},{plan_ref:42}]) {
  const snapshot=await boardRead({broken:task});
  assert.equal(snapshot.snapshot_json.tasks.broken.linkage.state,'unavailable');
 }
});
test('recorded opaque ancestor references remain readable without invented navigation',async()=>{
 const snapshot=await boardRead({a:{plan_ref:'plan:one',slice_ref:'slice:two',job_ref:'job:three',related:[{kind:'parent',id:'legacy:four'}]}});
 assert.deepEqual(snapshot.snapshot_json.tasks.a.linkage.references.map(r=>[r.relation,r.ref,r.state,r.target]),[
 ['plan','plan:one','unresolved',null],['slice','slice:two','unresolved',null],['job','job:three','unresolved',null],['parent','legacy:four','unresolved',null]]);
});
test('recorded WR and GitHub PR associations count without treating unreadable WRs as absent',async()=>{
 const calls=[];
 const client={query:async(sql,args)=>{
  calls.push([sql,args]);
  return {rows:sql.includes('from board_snapshot')?[stored({a:{work_request:'WR-900',pr:22,repo:'jbookout/carr-system'},b:{work_request_ref:'WR-901'}})]
  :sql.includes('from ops.work_request')?[{ref:'WR-900',title:'Readable WR'}]:[]};
 }};
 const read=await boardAnswerTools({})['read-progress-board'].handler(client,actor,{board_id:'demo-project'});
 const refs=read.snapshot.snapshot_json.tasks.a.linkage.references;
 assert.deepEqual(refs.map(r=>[r.relation,r.ref,r.state]),[['work_request','WR-900','resolved'],['pull_request','https://github.com/jbookout/carr-system/pull/22','resolved']]);
 assert.deepEqual(refs[0].target,{work_request:'WR-900'});
 const hidden=read.snapshot.snapshot_json.tasks.b.linkage.references[0];
 assert.equal(hidden.state,'unavailable');assert.equal(hidden.target,null);
 assert.equal(calls.filter(([sql])=>sql.includes('from ops.work_request')).length,1);
 const [sql,args]=calls.find(([sql])=>sql.includes('from ops.work_request'));
 assert.match(sql,/organization_tenant_id=\$1/);assert.match(sql,/readable|visibility|owner_actor/);
 assert.deepEqual(args.slice(0,2),['carr-internal','joe']);
});
test('census batches full source boards and requires each exact observed version',async()=>{
 const rows=['a','b'].map(id=>({id:'demo-project:'+id,title:id,state:'running',version:'7',opened_at:now().toISOString(),last_activity_at:now().toISOString(),completed:false,cancelled:false,identity:{board_id:'demo-project',task_id:id}}));
 for(const source of [stored({a:{blocked_by_task_ids:['b']},b:{title:'Actual source target',linkage_metadata_version:1}}),{...stored({a:{},b:{}}),version:8},null]) {
  const calls=[];
  const client={query:async(sql,args)=>{calls.push([sql,args]);return {rows:sql.includes('as cache')?[]:sql.includes('board_id=any')?(source?[source]:[]):sql.includes('count(*) as count')?[{count:2}]:rows};}};
  const read=await readSystemWorkCensus({client,actor,now,kinds:'progress_task'});
  assert.equal(calls.filter(([sql])=>sql.includes('board_id=any')).length,1);
  const sourceQuery=calls.find(([sql])=>sql.includes('board_id=any'));
  assert.deepEqual(sourceQuery[1],['carr-internal','joe',['demo-project']]);
  if(source?.version===7) {
   assert.equal(read.items[0].linkage.state,'complete');assert.equal(read.items[0].linkage.references[0].title,'Actual source target');
   assert.equal(read.items[1].unlinked,true);
  } else for(const item of read.items) {assert.equal(item.linkage.state,'unavailable');assert.notEqual(item.unlinked,true);}
 }
});
test('publisher preserves exact blocker and parent keys in the compact snapshot',async()=>{
 const {execFileSync}=await import('node:child_process');
 const output=execFileSync('python3',['-c',`import importlib.util,json
spec=importlib.util.spec_from_file_location("board","tools/progress_board.py")
board=importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)
snapshot=board.board_snapshot({"project":"demo","tasks":{"a":{"title":"Dependent","status":"blocked","blocked_by_task_ids":["b"],"parent_task_id":"c"}}},costs={})
assert snapshot["tasks"]["a"]["blocked_by_task_ids"]==["b"]
assert snapshot["tasks"]["a"]["parent_task_id"]=="c" 
print("metadata retained")
`],{cwd:new URL('../..',import.meta.url),encoding:'utf8'});
 assert.match(output,/metadata retained/);
});
test('malformed task entries and unsupported recorded associations never become empty complete tasks',async()=>{
 const snapshot=await boardRead({scalar:'invalid',a:{depends_on:['other']},b:{held:{blocker:{task_id:'other'}}}});
 assert.equal(snapshot.snapshot_json.tasks.scalar,'invalid');
 for(const id of ['a','b'])assert.equal(snapshot.snapshot_json.tasks[id].linkage.state,'unavailable');
});
test('legacy cached unlinked flags never survive an unavailable relationship read',async()=>{
 const row={id:'demo',title:'Legacy',state:'open',version:'1',opened_at:now().toISOString(),last_activity_at:now().toISOString(),completed:false,cancelled:false,identity:{},unlinked:true};
 const client={query:async sql=>({rows:sql.includes('as cache')?[]:sql.includes('count(*) as count')?[{count:1}]:[row]})};
 const read=await readSystemWorkCensus({client,actor,now,kinds:'work_shape'});
 assert.notEqual(read.items[0].unlinked,true);
});
test('legacy WR associations use the same authorized read and PR URL references are preserved',async()=>{
 const snapshot=await boardRead({a:{related:[{kind:'work_request',id:'WR-999'}],pr:'https://github.com/jbookout/doctorcre-app/pull/33'}});
 const refs=snapshot.snapshot_json.tasks.a.linkage.references;
 assert.equal(snapshot.snapshot_json.tasks.a.linkage.state,'complete');
 assert.deepEqual(refs.map(ref=>[ref.relation,ref.state]),[['work_request','unavailable'],['pull_request','resolved']]);
});
test('compact publication retains ancestry aliases and discloses omitted relationship metadata',async()=>{
 const {execFileSync}=await import('node:child_process');
 const output=execFileSync('python3',['-c',`import importlib.util,json
spec=importlib.util.spec_from_file_location("board","tools/progress_board.py")
board=importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)
tasks={"a":{"title":"Ancestry","status":"running","parent_ref":"legacy:parent","work_request_id":"WR-900","canonical_work_ref":"WR-901"},
"b":{"title":"Unsupported","status":"running","depends_on":["a"]},
"c":{"title":"Held","status":"running","held":{"blocker":{"task_id":"a"},"private_diagnostic":"Do not publish"}}}
print(json.dumps(board.board_snapshot({"project":"demo","tasks":tasks},costs={})))
`],{cwd:new URL('../..',import.meta.url),encoding:'utf8'});
 const publication=JSON.parse(output),tasks=publication.tasks;
 assert.equal(tasks.a.linkage_metadata_version,1);
 assert.equal(tasks.a.parent_ref,'legacy:parent');assert.equal(tasks.a.work_request_id,'WR-900');assert.equal(tasks.a.canonical_work_ref,'WR-901');
 assert.equal(tasks.b.linkage_metadata_unavailable,true);assert.equal(tasks.c.linkage_metadata_unavailable,true);
 assert.equal(tasks.c.held,undefined);
 const snapshot=await boardRead(tasks);
 assert.equal(snapshot.snapshot_json.tasks.a.linkage.references.length,3);
 for(const id of ['b','c'])assert.equal(snapshot.snapshot_json.tasks[id].linkage.state,'unavailable');
});

test('empty legacy publications cannot certify absence of metadata omitted by an older publisher',async()=>{
 const snapshot=await boardRead({legacy:{title:'Previously compacted work'}});
 assert.equal(snapshot.snapshot_json.tasks.legacy.linkage.state,'unavailable');
 assert.equal(snapshot.snapshot_json.tasks.legacy.linkage.reason,'source_linkage_not_observed');
});
