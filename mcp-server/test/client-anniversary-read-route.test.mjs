import test from 'node:test';
import assert from 'node:assert/strict';
import {ALLOW_LIST} from '../src/identity.js';
import {createDealroomHandler,isDealroomRequest} from '../src/dealroom-web.js';
import {readClientAnniversaries,CLIENT_ANNIVERSARY_PATH as PATH} from '../src/client-anniversary-read.ts';
class Kv {values=new Map();async put(k,v){this.values.set(k,v);}async get(k,o){const v=this.values.get(k);return v==null?null:o?.type==='json'?JSON.parse(v):v;}async delete(k){this.values.delete(k);}}
const host='dealroom.doctorcre.com';
async function fixture(email,fail=false){
 const env={DEALROOM_HOST:host,GOOGLE_CLIENT_ID:'client',GOOGLE_CLIENT_SECRET:'synthetic',OAUTH_KV:new Kv(),VERB_REQUEST_BUDGET_HUMAN:{limit:async()=>({success:true})},VERB_REQUEST_BUDGET:{idFromName:k=>k,get:()=>({fetch:async()=>Response.json({allowed:true})})}};
 const handler=createDealroomHandler({exchangeGoogleCodeFn:async()=>({id_token:'stub'}),verifyGoogleIdTokenFn:async()=>({email,email_verified:true,sub:email}),
 clientAnniversaryCaller:async(_env,actor,name,args)=>{assert.equal(name,'read-client-anniversaries');if(fail)throw new Error('synthetic source failure');return readClientAnniversaries({client:{query:async()=>({rows:[]})},actor,args});}});
 const start=await handler.fetch(new Request(`https://${host}/auth/login`),env,{});
 const google=new URL(start.headers.get('location'));
 const callback=await handler.fetch(new Request(`https://${host}/auth/callback?state=${google.searchParams.get('state')}&code=stub`,{headers:{cookie:start.headers.getSetCookie()[0]}}),env,{});
 const cookie=callback.headers.getSetCookie().find(v=>v.startsWith('__Host-dealroom_session='))?.split(';')[0];
 const get=(suffix='',method='GET')=>handler.fetch(new Request(`https://${host}${PATH}${suffix}`,{method,headers:cookie?{cookie}:{}}),env,{});
 return {handler,env,get};
}
test('verified Joe and Dell sessions reach the identical read contract',async()=>{
 for(const email of Object.keys(ALLOW_LIST)){
 const {get}=await fixture(email);const response=await get('?limit=1');assert.equal(response.status,200,email);
 assert.equal((await response.json()).schema_version,'client-anniversary-read.v1');
 assert.equal((await get('','POST')).status,405);
 for(const suffix of ['?actor=joe','?client=unknown','?limit=0','?limit=1&limit=2','?limit=1.5']) assert.equal((await get(suffix)).status,400,suffix);
 }
});
test('anonymous access refuses and fixed route has no generic proxy',async()=>{
 const {handler,env}=await fixture(Object.keys(ALLOW_LIST)[0]);
 assert.equal(isDealroomRequest(new Request(`https://${host}${PATH}`),env),true);
 assert.equal((await handler.fetch(new Request(`https://${host}${PATH}`),env,{})).status,401);
 assert.equal((await handler.fetch(new Request(`https://${host}${PATH}/anything`),env,{})).status,404);
});

test('source failure is sanitized unavailable with no cached fallback',async()=>{
 const {get}=await fixture(Object.keys(ALLOW_LIST)[0],true);
 const r=await get();assert.equal(r.status,503);assert.deepEqual(await r.json(),{error:'DEPENDENCY_UNAVAILABLE',state:'unavailable'});
});
