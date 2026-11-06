import test from 'node:test';
import assert from 'node:assert/strict';
import { ALLOW_LIST } from '../src/identity.js';
import { createDealroomHandler } from '../src/dealroom-web.js';
import { readActivityDealAttribution, ACTIVITY_ATTRIBUTION_PATH } from '../src/activity-deal-attribution.ts';
const HOST='dealroom.doctorcre.com';
class Kv {
  constructor() { this.values = new Map(); }
  async put(key, value) { this.values.set(key, value); }
  async get(key, options) { const value = this.values.get(key); return value == null ? null : options?.type === "json" ? JSON.parse(value) : value; }
  async delete(key) { this.values.delete(key); }
}
class Assets {
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/workspace.html") return new Response("<main>CARR Command Center</main>", { headers: { "content-type": "text/html" } });
    return new Response("missing", { status: 404 });
  }
}
const makeEnvironment = () => ({
  VERB_REQUEST_BUDGET_HUMAN: {limit:async()=>({success:true})},
  VERB_REQUEST_BUDGET: {idFromName:x=>x,get:()=>({fetch:async()=>Response.json({allowed:true})})},
  DEALROOM_HOST: HOST, GOOGLE_CLIENT_ID: "client", GOOGLE_CLIENT_SECRET: "secret",
  CORRELATION_ID: "corr-web", OAUTH_KV: new Kv(), ASSETS: new Assets(),
  WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true",
});
const cookie = (response) => (typeof response.headers.getSetCookie === "function" ? response.headers.getSetCookie() : [response.headers.get("set-cookie")]).find((value) => value?.startsWith("__Host-dealroom_session=")).split(";", 1)[0];

async function signIn(handler, environment) {
  const start = await handler.fetch(new Request(`https://${HOST}/auth/login`), environment, {});
  const google = new URL(start.headers.get("location"));
  const pending = typeof start.headers.getSetCookie === "function" ? start.headers.getSetCookie()[0] : start.headers.get("set-cookie");
  const callback = await handler.fetch(new Request(`https://${HOST}/auth/callback?state=${google.searchParams.get("state")}&code=stub`, { headers: { cookie: pending } }), environment, {});
  return cookie(callback);
}


const id='11111111-1111-4111-8111-111111111111';
for(const [email,slug] of Object.entries(ALLOW_LIST))test('authenticated route shares contract for '+slug,async()=>{
 const environment=makeEnvironment();let calls=0;
 const handler=createDealroomHandler({
 exchangeGoogleCodeFn:async()=>({id_token:'stub'}),
 verifyGoogleIdTokenFn:async()=>({email,email_verified:true,sub:'synthetic:'+slug}),
 activityAttributionCaller:async(_env,actor,verb,args)=>{
 calls++;assert.equal(actor.slug,slug);assert.equal(verb,'read-activity-deal-attribution');
 return readActivityDealAttribution({query:async()=>({rows:[]})},actor,args);
 }});
 const req=(suffix='',method='GET',session='')=>handler.fetch(new Request('https://'+HOST+ACTIVITY_ATTRIBUTION_PATH+suffix,{method,headers:{cookie:session}}),environment,{});
 assert.equal((await req('?activity_id='+id)).status,401);
 const session=await signIn(handler,environment);
 for(const query of ['', '?activity_id=bad','?activity_id='+id+'&actor=joe','?activity_id='+id+'&activity_id='+id])assert.equal((await req(query,'GET',session)).status,400);
 assert.equal(calls,0);
 assert.equal((await req('?activity_id='+id,'POST',session)).status,405);
 const r=await req('?activity_id='+id,'GET',session);assert.equal(r.status,200);assert.equal((await r.json()).state,'empty');assert.equal(calls,1);
});
test('source failure is sanitized by the fixed route',async()=>{
 const environment=makeEnvironment();const handler=createDealroomHandler({exchangeGoogleCodeFn:async()=>({id_token:'stub'}),verifyGoogleIdTokenFn:async()=>({email:Object.entries(ALLOW_LIST).find(([,slug])=>slug==='joe')[0],email_verified:true,sub:'synthetic'}),activityAttributionCaller:async()=>{throw Error('private');}});
 const session=await signIn(handler,environment);
 const r=await handler.fetch(new Request('https://'+HOST+ACTIVITY_ATTRIBUTION_PATH+'?activity_id='+id,{headers:{cookie:session}}),environment,{});
 assert.equal(r.status,503);assert.deepEqual(await r.json(),{error:'DEPENDENCY_UNAVAILABLE'});
});
