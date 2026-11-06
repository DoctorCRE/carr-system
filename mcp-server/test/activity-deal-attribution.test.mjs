import test from 'node:test';
import assert from 'node:assert/strict';
import { readActivityDealAttribution, parseActivityDealAttribution } from '../src/activity-deal-attribution.ts';
const id='11111111-1111-4111-8111-111111111111';
const deal='22222222-2222-4222-8222-222222222222';
const actor={id,slug:'joe',human:true};
const activity={id,version:1,source_ref:'activity:'+id,source_version:null};
const candidate={deal_id:deal,label:'Synthetic practice lease',deal_version:1,evidence:[{activity_id:id,activity_version:1}]};
const db=snapshot=>({query:async()=>({rows:[{snapshot}]})});
test('direct attribution is ready and receives exactly one credit',async()=>{
 const r=await readActivityDealAttribution(db({activity,direct:candidate,candidates:[],overflow:false,source_missing:false}),actor,{activity_id:id});
 assert.equal(r.state,'ready'); assert.equal(r.attribution,'direct'); assert.equal(r.credited_activity_count,1);
 assert.deepEqual(parseActivityDealAttribution(r),r);
});
test('malformed and extra arguments refuse before reading',async()=>{
 for(const args of [{},{activity_id:'bad'},{activity_id:id,actor:'dell'},{activity_id:id,tenant:'x'},{activity_id:id,deal_id:deal}])
  await assert.rejects(readActivityDealAttribution({query:()=>assert.fail('read')},actor,args),e=>e.payload.error==='QUERY_INVALID');
});
test('unauthorized callers refuse before reading',async()=>{
 await assert.rejects(readActivityDealAttribution({query:()=>assert.fail('read')},{id,slug:'stranger',human:true},{activity_id:id}),e=>e.payload.error==='AUTHORIZATION_REFUSED');
});
test('valid UUID uses exactly one bounded read for Joe and Dell',async()=>{
 for(const slug of ['joe','dell']){
 let count=0;
 const r=await readActivityDealAttribution({query:async(sql,args)=>{count++;assert.deepEqual(args,[id,slug]);return {rows:[]};}},{...actor,slug},{activity_id:id});
 assert.equal(count,1); assert.equal(r.state,'empty'); assert.equal(r.coverage,'unknown');
 }
});
test('source-linked and ambiguous associations receive no inferred credit',async()=>{
 for(const candidates of [[candidate],[candidate,{...candidate,deal_id:'33333333-3333-4333-8333-333333333333'}]]){
 const r=await readActivityDealAttribution(db({activity,direct:null,candidates,source_missing:false}),actor,{activity_id:id});
 assert.equal(r.state,'needs_input');assert.equal(r.attribution,candidates.length===1?'source_linked':'ambiguous');assert.equal(r.credited_activity_count,0);
 }
});
test('unresolved and missing source distinguish complete and unknown evidence',async()=>{
 for(const source_missing of [false,true]){
 const r=await readActivityDealAttribution(db({activity,direct:null,candidates:[],source_missing}),actor,{activity_id:id});
 assert.equal(r.attribution,'unresolved');assert.equal(r.coverage,source_missing?'unknown':'complete');
 }
});
test('source failure and overflow refuse evidence',async()=>{
 for(const c of [{query:async()=>{throw Error('private connection detail');}},db({overflow:true})]){
 const r=await readActivityDealAttribution(c,actor,{activity_id:id});assert.equal(r.state,'unavailable');assert.equal(r.activity,null);assert.equal(JSON.stringify(r).includes('private'),false);
 }
});
test('response parser rejects credit, duplicate evidence and contradictory states',async()=>{
 const r=await readActivityDealAttribution(db({activity,direct:candidate,candidates:[],source_missing:false}),actor,{activity_id:id});
 for(const bad of [{...r,credited_activity_count:0},{...r,state:'empty'},{...r,direct:{...candidate,evidence:[...candidate.evidence,...candidate.evidence]}},{...r,direct:{...candidate,label:''}}]) assert.throws(()=>parseActivityDealAttribution(bad));
});
