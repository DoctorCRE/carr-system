import test from 'node:test';
import assert from 'node:assert/strict';
import { drainWebPush, providerOrigin } from '../src/web-push-delivery.ts';

const config = { WEB_PUSH_ENABLED:'true', WEB_PUSH_VAPID_PUBLIC_KEY:'B'+'A'.repeat(86), WEB_PUSH_VAPID_PRIVATE_KEY:'A'.repeat(43), WEB_PUSH_VAPID_SUBJECT:'mailto:test@example.test', WEB_PUSH_KEY_GENERATION:'test' };
const row = i => ({event_id:`00000000-0000-4000-8000-${String(i).padStart(12,'0')}`,subscription_id:`10000000-0000-4000-8000-${String(i).padStart(12,'0')}`,claim_token:`20000000-0000-4000-8000-${String(i).padStart(12,'0')}`,card_item_id:`email_proposal:${i}`,endpoint:'https://fcm.googleapis.com/fcm/send/synthetic',p256dh:'synthetic',auth:'synthetic',ttl:3600});

test('quiet_or_resolved_card_is_never_sent_even_when_outbox_row_is_pending',async () => {
  let sends=0;
  const store={enqueue:async()=>{},claim:async()=>[row(1),row(2)],authorize:async()=>false,finish:async()=>{throw Error('must not finish unsent');}};
  const result=await drainWebPush({config,store,send:async()=>{sends++;return new Response(null,{status:201});}});
  assert.equal(sends,0); assert.equal(result.attempted,0);
});

test('60_endpoints_obey_twenty_attempt_batch_and_max_concurrency_two',async () => {
  let active=0,peak=0,calls=0; const payloads=[];
  const store={enqueue:async()=>{},claim:async()=>Array.from({length:60},(_,i)=>row(i)),authorize:async()=>true,finish:async()=>true};
  const result=await drainWebPush({config,store,send:async(c,p)=>{
    active++;peak=Math.max(peak,active);calls++;payloads.push(p);
    await new Promise(resolve=>setImmediate(resolve));active--;return new Response(null,{status:201});
  }});
  assert.equal(peak,2);assert.equal(calls,20);assert.equal(result.accepted,20);
  assert.deepEqual(Object.keys(payloads[0]).sort(),['card_item_id','event_id','path','schema_version']);
  assert.equal(payloads[0].path,'/inbox/card/email_proposal%3A0');
});

test('spend_safe_disabled_drain_and_missing_vapid_configuration',async () => {
  const store=new Proxy({},{get:()=>()=>{throw Error('disabled drain touched database');}});
  for(const key of Object.keys(config)) {
    assert.equal((await drainWebPush({config:{...config,[key]:undefined},store})).state,'disabled');
  }
  assert.equal((await drainWebPush({config:{...config,WEB_PUSH_ENABLED:'false'},store})).attempted,0);
});

test('database_unavailable_sends_nothing',async()=>{
  let sends=0;
  await assert.rejects(drainWebPush({config,store:{enqueue:async()=>{throw Error('database unavailable');}},send:async()=>{sends++;}}));
  assert.equal(sends,0);
});

test('malicious_endpoint_never_reaches_provider',async()=>{
  const credentialed=new URL('https://fcm.googleapis.com/a');credentialed.username='synthetic';
  const endpoints=['http://fcm.googleapis.com/a','https://127.0.0.1/a','https://[::1]/a','https://10.1.2.3/a',
    'https://fcm.googleapis.com.evil.test/a','https://web.push.apple.com:8443/a',credentialed.href,
    'https://evil.test/a','https://FCM.GOOGLEAPIS.COM/a','https://%66cm.googleapis.com/a','https://fcm.googleapis.com:443/a','https://fcm.googleapis.com/a#secret','https://fcm.googleapis.com\\@evil.test/a'];
  for(const endpoint of endpoints) {
    assert.equal(providerOrigin(endpoint),null);
    let sends=0,status;
    await drainWebPush({config,store:{enqueue:async()=>{},claim:async()=>[{...row(1),endpoint}],authorize:async()=>true,
      finish:async(c,s)=>{status=s;return true;}},send:async()=>{sends++;}});
    assert.equal(sends,0);assert.equal(status,400);
  }
  for(const endpoint of ['https://fcm.googleapis.com/a','https://web.push.apple.com/a','https://updates.push.services.mozilla.com/a','https://wns2-bn1p.notify.windows.com/a'])assert.ok(providerOrigin(endpoint));
});

test('provider_404_410_429_500_and_timeout_keep_same_event_identity',async()=>{
  for(const code of [201,404,410,429,500,0]) {
    const receipts=[];const payloads=[];
    const store={enqueue:async()=>{},claim:async()=>[row(1)],authorize:async()=>true,finish:async(c,s,r)=>{receipts.push([s,r]);return true;}};
    for(let i=0;i<2;i++) await drainWebPush({config,store,send:async(c,p)=>{
      payloads.push(p);if(!code)throw Error('synthetic timeout after acceptance');
      return new Response(null,{status:code,headers:{'Retry-After':'120'}});
    }});
    assert.deepEqual(payloads[0],payloads[1]);assert.equal(receipts[0][0],code);assert.equal(receipts[0][1],code?120:0);
  }
});

test('rfc8291_sender_encrypts_synthetic_payload_and_refuses_redirects_without_sensitive_logs',async t=>{
  const {sendWebPush}=await import('../src/web-push-delivery.ts');
  const {runWebPush}=await import('../src/web-push-worker.ts');
  const pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
  const jwk=await crypto.subtle.exportKey('jwk',pair.privateKey);
  const publicKey=Buffer.from(await crypto.subtle.exportKey('raw',pair.publicKey)).toString('base64url');
  const subscription=await crypto.subtle.generateKey({name:'ECDH',namedCurve:'P-256'},true,['deriveBits']);
  const p256dh=Buffer.from(await crypto.subtle.exportKey('raw',subscription.publicKey)).toString('base64url');
  const auth=Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString('base64url');
  const original=globalThis.fetch;const logs=[];const originalLog=console.log;
  t.after(()=>{globalThis.fetch=original;console.log=originalLog;});console.log=(...args)=>logs.push(args);
  let requests=0,captured;
  globalThis.fetch=async(url,request)=>{
    requests++;captured={url,request};
    throw new TypeError('synthetic redirect rejected');
  };
  await assert.rejects(sendWebPush({...row(1),p256dh,auth},{event_id:row(1).event_id},{subject:'mailto:test@example.test',publicKey,privateKey:jwk.d,generation:'test'}));
  assert.equal(requests,1);assert.deepEqual(logs,[]);
  assert.equal(captured.url,row(1).endpoint);assert.equal(captured.request.redirect,'error');assert.ok(captured.request.signal);
  assert.equal(captured.request.headers['content-encoding'],'aes128gcm');assert.ok(captured.request.headers.authorization.startsWith('vapid '));
  assert.equal(Buffer.from(captured.request.body).includes(Buffer.from(row(1).event_id)),false);
  assert.equal((await runWebPush({WEB_PUSH_ENABLED:'false',DATABASE_URL_JOBS:'invalid-synthetic-url'})).state,'disabled');
});

test('retry_after_date_seconds_and_invalid_values',async()=>{
  const {retryAfterSeconds}=await import('../src/web-push-delivery.ts');
  assert.equal(retryAfterSeconds('120'),120);assert.equal(retryAfterSeconds('bad'),0);
  assert.equal(retryAfterSeconds('Thu, 01 Jan 1970 00:01:00 GMT',0),60);
  assert.equal(retryAfterSeconds('99999999999999999999'),86400);
});
