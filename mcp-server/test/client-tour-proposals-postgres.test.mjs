import test from 'node:test';
import assert from 'node:assert/strict';
import { clientTourProposalTools } from '../src/client-tour-proposals.js';

function client() {
  return {query:async sql=>({rows:sql.includes('select d.owner')||sql.includes('select owner,version')?[{owner:'joe',version:1}]:sql.includes('proposal_source')?[{source:{portal_slug:'synthetic'}}]:sql.includes('pull_client')?[{proposal:{id:'one'}}]:[]})};
}
const partner={slug:'joe',human:true};
const args={deal_id:'deal',tour_id:'tour',idempotency_key:'key'};
const saved={schema_version:'client-tour-selection.v1',portal_slug:'synthetic',revision:1,items:[]};
function toolsFor(fetchFn,now) {
  return clientTourProposalTools({withEnvelope:async (_c,_a,_n,_args,fn)=>fn(),writeEvent:async()=>{},fetchFn,now});
}

for (const value of [
  {...saved,portal_slug:'other'}, {...saved,revision:0}, {...saved,revision:1.5},
  {...saved,financials:1}, {...saved,items:[{name:'Building'}]},
  {...saved,items:[{carr_property_id:'unresolved-name'}]},
  {...saved,items:[{carr_property_id:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',name:'Building'}]},
  {...saved,items:Array.from({length:101},()=>({carr_property_id:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'}))},
  {...saved,items:Array.from({length:2},()=>({carr_property_id:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'}))},
]) test('reject invalid saved list '+JSON.stringify(value).slice(0,100),async ()=>{
  const tools=toolsFor(async()=>Response.json(value));
  await assert.rejects(tools['pull-client-tour-proposal'].handler(client(),partner,args),e=>e.payload?.error==='proposal_source_invalid');
});

test('two source failures open the breaker; a later successful probe recovers',async ()=>{
  let time=0,calls=0,healthy=false;
  const tools=toolsFor(async()=>{calls++;return healthy?Response.json(saved):new Response(null,{status:401});},()=>time);
  const pull=()=>tools['pull-client-tour-proposal'].handler(client(),partner,args);
  await assert.rejects(pull()); await assert.rejects(pull());
  await assert.rejects(pull(),e=>e.payload?.error==='proposal_source_circuit_open');
  assert.equal(calls,2);
  time=60001;healthy=true;await pull();assert.equal(calls,3);
});

test('bounded pull rate, fixed-origin GET, no credentials, redirect refusal and bounded body',async ()=>{
  let calls=0;
  const tools=toolsFor(async(url,init)=>{
    calls++;assert.equal(new URL(url).hostname,'share.doctorcre.com');
    assert.equal(init.redirect,'error');assert.equal(init.credentials,'omit');
    assert.equal(init.method,'GET');assert.equal(init.body,undefined);
    assert(init.signal instanceof AbortSignal);return Response.json(saved);
  },()=>0);
  for(let i=0;i<20;i++) await tools['pull-client-tour-proposal'].handler(client(),partner,args);
  await assert.rejects(tools['pull-client-tour-proposal'].handler(client(),partner,args),e=>e.payload?.error==='proposal_source_rate_limited');
  assert.equal(calls,20);
  const large=toolsFor(async()=>new Response('x'.repeat(98305),{headers:{'content-type':'application/json'}}));
  await assert.rejects(large['pull-client-tour-proposal'].handler(client(),partner,args),e=>e.payload?.error==='proposal_source_too_large');
});

test('portal actors cannot trigger even the source GET',async ()=>{
  let calls=0;
  const tools=toolsFor(async()=>{calls++;return Response.json(saved);});
  for(const actor of [{slug:'joe',human:false},{slug:'portal',human:false},{slug:'dell',human:false}]) {
    await assert.rejects(tools['pull-client-tour-proposal'].handler(client(),actor,args));
    await assert.rejects(tools['dismiss-client-tour-proposal'].handler(client(),actor,{...args,proposal_id:'one'}));
  }
  assert.equal(calls,0);
});

test('proposal_accept_is_the_only_path_that_changes_the_shortlist', async () => {
  const writes = [];
  const c = {query:async (sql,args)=>{
    if (sql.includes('select d.owner')||sql.includes('select owner,version')) return {rows:[{owner:'joe',version:1}]};
    if (sql.includes('proposal_source')) return {rows:[{source:{portal_slug:'synthetic'}}]};
    writes.push({sql,args});
    return {rows:[{proposal:{id:'synthetic'},receipt:{state:'accepted'}}]};
  }};
  const tools = clientTourProposalTools({withEnvelope:async (_c,_a,_n,_args,fn)=>fn(),writeEvent:async()=>{},fetchFn:async ()=>Response.json({schema_version:'client-tour-selection.v1',portal_slug:'synthetic',revision:1,items:[]})});
  const who = {slug:'joe',human:true};
  await tools['pull-client-tour-proposal'].handler(c,who,{deal_id:'deal',tour_id:'tour',idempotency_key:'pull'});
  assert(writes.some(w=>w.sql.includes('pull_client_tour_proposal')));
  assert(!writes.some(w=>w.sql.includes('accept_client_tour_proposal')));
  writes.length=0;
  await tools['accept-client-tour-proposal'].handler(c,who,{deal_id:'deal',proposal_id:'proposal',expected_selection_version_id:null,mode:'use_theirs',idempotency_key:'accept'});
  assert(writes.some(w=>w.sql.includes('accept_client_tour_proposal')));
});
