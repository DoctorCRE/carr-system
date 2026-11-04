import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPortalWebHandler, safePortalProjection } from '../src/portal-web.js';

const origin = 'https://share.doctorcre.com';
const ttl = 604800000;
function fixture(overrides = {}) {
  let time = Date.parse('2026-01-01T00:00:00Z');
  const sessions = new Map(), calls = [], signals = [];
  const portal = slug => ({slug,roadmap:{current_phase:'search',phases:[]},materials:[{kind:'note',title:'<script>x</script>',content:'retained'}]});
  const handler = createPortalWebHandler({
    now: () => time, signal: x => signals.push(x), safeProjection: safePortalProjection,
    exchange: async args => {
      calls.push(['exchange',args]);
      if(args.password==='lock') return {status:'locked',retry_at:new Date(time+900000).toISOString()};
      if(args.password!=='existing') return {status:'denied'};
      sessions.set(args.digest,{slug:args.slug,expires:time+ttl});
      return {status:'success',expires_at:new Date(time+ttl).toISOString()};
    },
    read: async args => {
      calls.push(['read',args]); const s=sessions.get(args.digest);
      return s && s.slug===args.slug && !s.revoked && s.expires>time
        ? {status:'success',schema_version:'client-portal.v1',portal:portal(args.slug)} : {status:'denied'};
    },
    revoke: async args => {
      calls.push(['revoke',args]); const s=sessions.get(args.digest);
      if(s?.slug===args.slug) s.revoked=true;
      return {status:s?.slug===args.slug?'success':'denied'};
    }, ...overrides
  });
  const request = (path,method='GET',cookie='',body,headers={}) => handler.fetch(new Request(origin+path,{
    method, headers:{...(cookie?{cookie}:{}),...(method==='POST'?{origin,'content-type':'application/x-www-form-urlencoded'}:{}),...headers},
    ...(body!==undefined?{body}: {})
  }));
  const login = async (slug='alpha') => {
    const r=await request(`/${slug}/login`,'POST','','password=existing');
    assert.equal(r.status,303); return r.headers.get('set-cookie').split(';')[0];
  };
  return {request,login,handler,calls,signals,sessions,advance:ms=>time+=ms};
}

test('redirect, form, denied login and security headers', async () => {
  const f=fixture(), r=await f.request('/alpha'); assert.equal(r.status,308); assert.equal(r.headers.get('location'),'/alpha/');
  const form=await f.request('/alpha/'); assert.match(await form.text(),/type="password"/);
  const denied=await f.request('/alpha/login','POST','','password=wrong'); assert.equal(denied.status,401);
  for(const response of [r,form,denied]) {
    assert.equal(response.headers.get('cache-control'),'private, no-store');
    assert.match(response.headers.get('content-security-policy'),/frame-ancestors 'none'/);
    assert.equal(response.headers.get('access-control-allow-origin'),null);
  }
});
test('two portals in one cookie jar, digest-only reads and cross-slug refusal', async () => {
  const f=fixture(), a=await f.login(), b=await f.login('beta'), jar=`${a}; ${b}`;
  for(const slug of ['alpha','beta']) {
    const r=await f.request(`/${slug}/`,'GET',jar); assert.equal(r.status,200);
    const html=await r.text(); assert.match(html,/retained/); assert.doesNotMatch(html,/<script>/);
    assert.equal((await f.request(`/${slug}/status`,'GET',jar)).status,200);
  }
  assert.equal((await f.request('/beta/status','GET',a.replace('portal-alpha','portal-beta'))).status,401);
  assert.notEqual(f.calls[0][1].digest,f.calls[1][1].digest);
  for(const [op,args] of f.calls) {
    assert.match(args.digest,/^[a-f0-9]{64}$/);
    assert.deepEqual(Object.keys(args).sort(),(op==='exchange'?['slug','password','digest','signal']:['slug','digest','signal']).sort());
  }
});
test('seven-day host cookie, database expiry and clearing expired sessions', async () => {
  const f=fixture(), r=await f.request('/alpha/login','POST','','password=existing'), c=r.headers.get('set-cookie');
  for(const part of ['__Host-portal-alpha=','Secure','HttpOnly','SameSite=Lax','Path=/','Max-Age=604800']) assert.ok(c.includes(part));
  assert.ok(!c.includes('Domain=')); f.advance(ttl);
  const expired=await f.request('/alpha/status','GET',c.split(';')[0]); assert.equal(expired.status,401); assert.match(expired.headers.get('set-cookie'),/Max-Age=0/);
});
test('logout converges on repeats, revoked reads refuse and missing cookie logout succeeds', async () => {
  const f=fixture(), c=await f.login();
  for(let i=0;i<2;i++) assert.equal((await f.request('/alpha/logout','POST',c)).status,303);
  assert.equal((await f.request('/alpha/status','GET',c)).status,401);
  assert.equal((await f.request('/alpha/logout','POST')).status,303);
});
test('lockout explains retry time; later exchange can succeed', async () => {
  const f=fixture(); for(let i=0;i<2;i++) {
    const r=await f.request('/alpha/login','POST','','password=lock'); assert.equal(r.status,429);
    assert.equal(r.headers.get('retry-after'),'900'); assert.match(await r.text(),/2026-01-01T00:15:00/);
  }
  f.advance(900000); await f.login();
});
test('every mutation checks origin; forbidden routes and unsafe selectors do not call dependencies', async () => {
  const f=fixture();
  for(const path of ['login','logout','packages','files','selections','publish','upload'])
    for(const value of ['', 'https://evil.test']) assert.equal((await f.request('/alpha/'+path,'POST','','password=existing',{origin:value})).status,403);
  for(const path of ['/alpha%2fbeta/','/alpha/%2e%2e/status','/alpha/../status','/alpha/upload','/alpha/publish','/partner/upload','/alpha/?client=beta'])
    assert.notEqual((await f.request(path)).status,200,path);
  assert.equal((await f.request('/alpha/login')).status,405); assert.equal(f.calls.length,0);
});
test('deferred endpoints explicitly return not available', async () => {
  const f=fixture(); for(const path of ['packages','files','selections']) for(const method of ['GET','POST'])
    assert.equal((await f.request('/alpha/'+path,method)).status,501);
  assert.equal(f.calls.length,0);
});
test('body bounds, media type and exact password shape', async () => {
  const f=fixture(); for(const body of ['password='+ 'x'.repeat(9000),'password=a&client=beta','password=a&password=b','password=','password='+ 'x'.repeat(1025)])
    assert.ok((await f.request('/alpha/login','POST','',body)).status>=400);
  assert.equal((await f.request('/alpha/login','POST','','{}',{'content-type':'application/json'})).status,415); assert.equal(f.calls.length,0);
});
for(const name of ['exchange','read','revoke','safeProjection']) test(`${name} failure closes access and emits a redacted signal`,async()=>{
  let fail=false;
  const good={exchange:{status:'success',expires_at:'2026-01-08T00:00:00Z'},read:{status:'success',schema_version:'client-portal.v1',portal:{slug:'alpha',roadmap:{phases:[]},materials:[]}},revoke:{status:'success'},safeProjection:{slug:'alpha',roadmap:{phases:[]},materials:[]}};
  const f=fixture({[name]:async()=>{if(fail)throw Error('private sentinel');return good[name];}}),c=await f.login(); fail=true;
  const mutation=name==='exchange'||name==='revoke';
  const r=await f.request(name==='exchange'?'/alpha/login':name==='revoke'?'/alpha/logout':'/alpha/',mutation?'POST':'GET',c,mutation?'password=existing':undefined);
  assert.equal(r.status,503); assert.doesNotMatch(await r.text(),/private sentinel|retained/); assert.ok(f.signals.length);
});
test('no cached content after authorization changes',async()=>{
  const f=fixture(),c=await f.login(); assert.equal((await f.request('/alpha/','GET',c)).status,200); f.sessions.clear(); assert.equal((await f.request('/alpha/status','GET',c)).status,401);
});
test('dependency deadline retains its slot without retrying',async()=>{
  let resolve,count=0; const f=fixture({maxConcurrent:1,deadlineMs:15,exchange:()=>{count++;return new Promise(r=>resolve=r);}});
  assert.equal((await f.request('/alpha/login','POST','','password=existing')).status,503);
  assert.equal((await f.request('/beta/login','POST','','password=existing')).status,503); assert.equal(count,1); resolve({status:'denied'});
});
test('breaker opens after three failures and recovers on a live half-open read',async()=>{
  let fail=true,count=0; const f=fixture({read:async()=>{count++;if(fail)throw Error();return {status:'success',schema_version:'client-portal.v1',portal:{slug:'alpha',roadmap:{phases:[]},materials:[]}};}});
  const c='__Host-portal-alpha='+'a'.repeat(64);
  for(let i=0;i<4;i++) assert.equal((await f.request('/alpha/status','GET',c)).status,503); assert.equal(count,3);
  f.advance(30000); fail=false; assert.equal((await f.request('/alpha/status','GET',c)).status,200); assert.equal(count,4);
});
test('bounded login/action rates, bucket saturation and eviction',async()=>{
  const f=fixture(); for(let i=0;i<10;i++) await f.request('/alpha/login','POST','','password=wrong');
  assert.equal((await f.request('/alpha/login','POST','','password=wrong')).status,429); f.advance(60000);
  assert.equal((await f.request('/alpha/login','POST','','password=wrong')).status,401);
  const g=fixture({maxRateBuckets:1}); await g.request('/alpha/'); assert.equal((await g.request('/beta/')).status,503); g.advance(60000); assert.equal((await g.request('/beta/')).status,200);
  const h=fixture({actionLimit:1}); await h.request('/alpha/'); assert.equal((await h.request('/alpha/')).status,429);
});
test('malformed dependency results or projections fail closed',async()=>{
  for(const result of [null,{status:'success'},{status:'success',schema_version:'client-portal.v1',portal:{slug:'beta'}}]) {
    const f=fixture({read:async()=>result}),c=await f.login(); assert.equal((await f.request('/alpha/','GET',c)).status,503);
  }
});
test('safe projection drops non-contract fields and refuses invalid shapes',()=>{
  const p=safePortalProjection({slug:'alpha',roadmap:{current_phase:'x',phases:[]},materials:[],internal_notes:'secret'},'alpha');
  assert.equal('internal_notes' in p,false); assert.throws(()=>safePortalProjection({slug:'beta'},'alpha'));
});
test('request admission and half-open probe concurrency stay bounded',async()=>{
  let release, entered;
  const started=new Promise(resolve=>entered=resolve);
  const f=fixture({maxRequests:1,exchange:()=>{entered();return new Promise(resolve=>release=resolve);}});
  const pending=f.request('/alpha/login','POST','','password=existing');
  await started;
  assert.equal((await f.request('/beta/')).status,503);
  release({status:'denied'}); assert.equal((await pending).status,401);
  let attempts=0, probe, probeEntered;
  const probeStarted=new Promise(resolve=>probeEntered=resolve);
  const g=fixture({read:()=>{attempts++; if(attempts<=3) throw Error(); probeEntered(); return new Promise(resolve=>probe=resolve);}});
  const c='__Host-portal-alpha='+'a'.repeat(64);
  for(let i=0;i<3;i++) await g.request('/alpha/status','GET',c);
  g.advance(30000); const first=g.request('/alpha/status','GET',c);
  await probeStarted;
  assert.equal((await g.request('/alpha/status','GET',c)).status,503); assert.equal(attempts,4);
  probe({status:'denied'}); assert.equal((await first).status,401);
});
test('streamed oversized and stalled bodies refuse without database work',async()=>{
  const f=fixture({deadlineMs:15});
  for(const large of [true,false]) {
    const body=new ReadableStream({start(controller){if(large)controller.enqueue(new Uint8Array(8193));}});
    const r=await f.handler.fetch(new Request(origin+'/alpha/login',{method:'POST',headers:{origin,'content-type':'application/x-www-form-urlencoded'},body,duplex:'half'}));
    assert.equal(r.status,large?413:408);
  }
  assert.equal(f.calls.length,0);
});
test('cookie duplicates, malformed tokens and missing adapters cannot grant access',async()=>{
  const f=fixture(),c=await f.login();
  for(const value of [c+'; '+c,'__Host-portal-alpha=bad']) assert.equal((await f.request('/alpha/status','GET',value)).status,401);
  const g=fixture({exchange:undefined}); assert.equal((await g.request('/alpha/login','POST','','password=existing')).status,503);
});
test('short SQL expiry clips cookie age and database failure never serves earlier content',async()=>{
  const f=fixture({exchange:async()=>({status:'success',expires_at:'2026-01-01T00:00:30Z'})});
  const r=await f.request('/alpha/login','POST','','password=existing'); assert.match(r.headers.get('set-cookie'),/Max-Age=30/);
  let fail=false; const g=fixture({read:async()=>{
    if(fail)throw Error(); return {status:'success',schema_version:'client-portal.v1',portal:{slug:'alpha',roadmap:{phases:[]},materials:[]}};
  }}), c=await g.login(); assert.equal((await g.request('/alpha/','GET',c)).status,200); fail=true;
  assert.equal((await g.request('/alpha/','GET',c)).status,503);
});
test('successful authorization cannot reset the failing projection breaker',async()=>{
  let calls=0;
  const f=fixture({safeProjection:()=>{calls++;throw Error();}}),c=await f.login();
  for(let i=0;i<4;i++) assert.equal((await f.request('/alpha/','GET',c)).status,503);
  assert.equal(calls,3);
});
