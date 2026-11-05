import test from 'node:test';
import assert from 'node:assert/strict';
import { subjectWatchTools } from '../src/subject-watches.js';

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload=payload; } }
const tools=subjectWatchTools({ ToolError, withEnvelope: async (_c,_a,_v,_args,fn)=>fn() });

test('watch refuses inaccessible targets before entering the write envelope',async()=>{
  let envelopes=0;
  const verbs=subjectWatchTools({ ToolError, withEnvelope: async()=>{ envelopes++; } });
  await assert.rejects(verbs['watch-subject-v1'].handler({query:async()=>({rows:[{result:{ok:false,error:'watch_target_not_found'}}]})},{},{subject_type:'deal',subject_id:'00000000-0000-4000-8000-000000000001'}),e=>e.payload.error==='watch_target_not_found');
  assert.equal(envelopes,0);
});

test('list returns a bounded page and cursor without mutating notification policy',async()=>{
  const calls=[];
  const c={query:async(sql,args)=>{ calls.push({sql,args}); return {rows:[{result:{ok:true,watches:[{id:'first'},{id:'second'}]}}]}; }};
  assert.deepEqual(await tools['list-subject-watches-v1'].handler(c,{}, {limit:1}),{
    ok:true,schema_version:'subject-watch.v1',watches:[{id:'first'}],has_more:true,next_cursor:'first',
  });
  assert.deepEqual(calls,[{sql:'select ops.list_subject_watches($1::uuid,$2) as result',args:[null,1]}]);
});

test('database refusal fails closed rather than producing an empty successful feed',async()=>{
  await assert.rejects(tools['list-subject-watches-v1'].handler({query:async()=>({rows:[]})},{},{}),e=>e.payload.error==='subject_watch_unavailable');
});

test('only write verbs require keys and every input excludes a recipient override',()=>{
  for(const [name,verb] of Object.entries(tools)) {
    assert.equal(verb.inputSchema.additionalProperties,false);
    assert.ok(!Object.hasOwn(verb.inputSchema.properties,'actor_id'));
    assert.equal(verb.inputSchema.required?.includes('idempotency_key') ?? false,!!verb.write);
    assert.equal(verb.writerConnection ?? false,!verb.write);
    assert.match(name,/-v1$/);
  }
});
