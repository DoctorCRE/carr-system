import test, {beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import {cachedSemanticAsk,clearSemanticCache} from '../src/jev-semantic.js';
beforeEach(clearSemanticCache);
const request = () => ({model:'jev-1.13.0',state:{text:'bounded'},questions:{q:{type:'choice',instructions:'Which meaning?',criteria:{z:'Z',a:'A'}}}});
test('complete input, pin and question version cache; stable options and concurrent single flight',async()=>{
  let calls=0, seen;
  const ask=async r=>{calls++;seen=r;return {model:r.model,answers:{q:{type:'choice',choice:'a'}}};};
  const answers=await Promise.all([cachedSemanticAsk(ask,request(),'v1'),cachedSemanticAsk(ask,request(),'v1')]);
  assert.equal(calls,1);assert.equal(answers[0].advisory_only,true);
  assert.deepEqual(Object.keys(seen.questions.q.criteria),['a','z']);
  const repeat=await cachedSemanticAsk(ask,request(),'v1');assert.equal(repeat.cache_hit,true);
  await cachedSemanticAsk(ask,request(),'v2');
  await cachedSemanticAsk(ask,{...request(),state:{text:'changed'}},'v2');
  await cachedSemanticAsk(ask,{...request(),questions:{q:{...request().questions.q,instructions:'Changed'}}},'v2');
  assert.equal(calls,4);
  await assert.rejects(()=>cachedSemanticAsk(ask,{...request(),model:'jev-latest'},'v1'),/pin/);
  await assert.rejects(()=>cachedSemanticAsk(ask,{...request(),state:{text:'x'.repeat(100001)}},'v1'),/narrow/);
});
test('invalid, partial or wrong-model responses never cache',async()=>{
  for(const answer of [{answers:{}},{model:'jev-latest',answers:{q:{choice:'a'}}},{answers:{q:{choice:'unoffered'}}}]){
    clearSemanticCache();let calls=0;
    const ask=async()=>{calls++;return answer;};
    for(let n=0;n<2;n++) await assert.rejects(()=>cachedSemanticAsk(ask,request(),'v1'));
    assert.equal(calls,2);
  }
});
test('the hashed payload and submitted evidence are one immutable snapshot', async()=>{
  const input=request(); input.state.text='before'; let seen;
  const ask=async r=>{seen=structuredClone(r);return {answers:{q:{choice:'a'}}};};
  const pending=cachedSemanticAsk(ask,input,'snapshot-v1');
  input.state.text='after'; input.questions.q.criteria.a='Changed';
  await pending;
  assert.equal(seen.state.text,'before');
  assert.equal(seen.questions.q.criteria.a,'A');
  const hit=await cachedSemanticAsk(ask,{...request(),state:{text:'before'}},'snapshot-v1');
  assert.equal(hit.cache_hit,true);
});
