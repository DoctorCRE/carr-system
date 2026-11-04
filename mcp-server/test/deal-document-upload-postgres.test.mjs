import test, { before, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { dealDocumentUploadTools, uploadDigest, UPLOAD_PREFIX } from '../src/deal-document-upload.ts';
import { createDealDocumentHandler } from '../src/deal-document-web.ts';
import { executeRegisteredTool } from '../src/tools.js';
let fixture,pool,actor,deal,handler,stored,puts,deletes;
const tools=dealDocumentUploadTools();
const bytes=new TextEncoder().encode('%PDF-1.7\nsynthetic\n%%EOF');
const url='https://app.example.test'+UPLOAD_PREFIX;
const tx=async fn=> {
  const c=await pool.connect();
  try { await c.query('begin'); await c.query('set local role carr_writer'); const out=await fn(c); await c.query('commit'); return out; }
  catch(e) { await c.query('rollback'); throw e; } finally { c.release(); }
};
const bucket={
  async put(key,value,options) { puts++; stored.set(key,{size:value.length,customMetadata:options.customMetadata}); return {key}; },
  async head(key) { return stored.get(key)??null; },
  async delete(key) { deletes++; stored.delete(key); },
};
const makeHandler=(storage=bucket)=>createDealDocumentHandler({transaction:tx,bucket:storage,callTool:executeRegisteredTool,backoff:async()=>{}});
const input=async(extra={})=>({idempotency_key:randomUUID(),action_id:randomUUID(),deal_id:deal,filename:'LOI.pdf',declared_mime:'application/pdf',bytes:bytes.length,sha256:await uploadDigest(bytes),...extra});
const begin=async args=>tx(c=>tools['begin-deal-document-upload'].handler(c,actor,args));
const read=async(id,who=actor)=>tx(c=>tools['read-deal-document-upload'].handler(c,who,{upload_id:id}));
const post=(id,body=bytes)=>new Request(`${url}/${id}/bytes`,{method:'POST',headers:{'content-type':'application/octet-stream'},body});
const upload=async(id,body=bytes,who=actor,active=async()=>true)=>handler(post(id,body),who,active);
const cancel=(id,key=randomUUID())=>handler(new Request(`${url}/${id}/cancel`,{method:'POST',headers:{'idempotency-key':key}}),actor,async()=>true);
const quota=async()=>Number((await pool.query('select reserved_bytes from ops.deal_document_upload_quota')).rows[0].reserved_bytes);
before(async()=> {
 fixture=await postgresDatabase('partner-upload-');
 const c=fixture.client;
 await c.query(`create role carr_writer; create role carr_reader; create role carr_jobs; create schema ops;
 grant usage on schema ops to carr_writer;
 create table actor(id uuid primary key,slug text unique,active boolean);
 create table party(id uuid primary key,merged_into uuid,deleted_at timestamptz);
 create table client(id uuid primary key,party_id uuid,merged_into uuid);
 create table deal(id uuid primary key,client_id uuid,owner text);
 create function ops.scac_current_grant_contract() returns jsonb language sql immutable as 'select jsonb_build_object()';
 grant select on actor,party,client,deal to carr_writer;`);
 await c.query(await readFile(new URL('../../migrations/1146_deal_document_upload.sql',import.meta.url),'utf8'));
 actor={id:randomUUID(),slug:'joe',human:true,documentUploadSession:'a'.repeat(64)};
 await c.query("insert into actor values($1,'joe',true),($2,'dell',true)",[actor.id,randomUUID()]);
 deal=randomUUID();const party=randomUUID(),client=randomUUID();
 await c.query('insert into party(id) values($1)',[party]);
 await c.query('insert into client(id,party_id) values($1,$2)',[client,party]);
 await c.query("insert into deal values($1,$2,'joe')",[deal,client]);
 pool=new pg.Pool({host:c.host,port:c.port,user:c.user,database:c.database});
});
beforeEach(async()=> { await pool.query('truncate ops.deal_document_upload'); await pool.query('update ops.deal_document_upload_quota set reserved_bytes=0,failures=0,blocked_until=null'); stored=new Map();puts=0;deletes=0;handler=makeHandler(); });
after(async()=> {await pool?.end();await fixture?.close();});
test('partner_pdf_bytes_stage_only_for_reserved_actor_deal_and_hash',async()=> {
 const args=await input();const row=await begin(args);
 assert.equal((await begin(args)).upload_id,row.upload_id);
 await assert.rejects(begin({...args,sha256:'0'.repeat(64)}),/upload_conflict/);
 assert.equal((await upload(row.upload_id,bytes,{...actor,slug:'dell'})).status,403);
 assert.equal((await upload(row.upload_id,bytes,{...actor,documentUploadSession:'b'.repeat(64)})).status,403);
 assert.equal(puts,0);
 const response=await upload(row.upload_id);assert.equal(response.status,201,await response.clone().text());
 const receipt=await response.json();assert.equal(receipt.state,'staged');assert.equal(receipt.filed,false);assert.equal(receipt.deal_id,deal);
 assert.equal(response.headers.get('cache-control'),'no-store');assert.equal('object_key' in receipt,false);
 assert.equal((await upload(row.upload_id)).status,200);assert.equal(puts,1);
 assert.equal((await read(row.upload_id)).sha256,args.sha256);assert.equal(await quota(),bytes.length);
});
test('mismatch, interrupted, oversize and spoofed bodies refund without R2',async()=> {
 for(const body of [bytes.subarray(0,10),new Uint8Array(bytes.length+1),new TextEncoder().encode('x'.repeat(bytes.length)),null]) {
   const row=await begin(await input());const response=await upload(row.upload_id,body);
   assert.ok([413,415,422].includes(response.status),await response.text());
   assert.equal((await read(row.upload_id)).state,'rejected');assert.equal(await quota(),0);
 }
 const row=await begin(await input({sha256:'0'.repeat(64)}));assert.equal((await upload(row.upload_id)).status,422);
 assert.equal(await quota(),0);assert.equal(puts,0);
});
test('cancel refunds once, changed key refuses, expired bytes refuse',async()=> {
 const row=await begin(await input());const key=randomUUID();
 assert.equal((await cancel(row.upload_id,key)).status,200);assert.equal((await cancel(row.upload_id,key)).status,200);
 assert.equal((await cancel(row.upload_id)).status,409);assert.equal(await quota(),0);
 assert.equal((await upload(row.upload_id)).status,409);
 const expired=await begin(await input());await pool.query("update ops.deal_document_upload set expires_at=now()-interval '1 second' where id=$1",[expired.upload_id]);
 assert.equal((await upload(expired.upload_id)).status,410);assert.equal((await read(expired.upload_id)).state,'expired');
 assert.equal(await quota(),bytes.length);
});
test('staged cancellation deletes only its opaque temporary key before refund',async()=> {
 const row=await begin(await input());await upload(row.upload_id);
 assert.equal((await cancel(row.upload_id)).status,200);assert.equal(deletes,1);assert.equal(stored.size,0);assert.equal(await quota(),0);
});
test('revoked session refuses before storage and after storage preserves unknown receipt',async()=> {
 const row=await begin(await input());assert.equal((await upload(row.upload_id,bytes,actor,async()=>false)).status,403);assert.equal(puts,0);
 let active=true;handler=makeHandler({...bucket,put:async(...args)=>{const result=await bucket.put(...args);active=false;return result;}});
 assert.equal((await upload(row.upload_id,bytes,actor,async()=>active)).status,403);
 assert.equal((await read(row.upload_id)).state,'retryable_unknown');assert.equal(await quota(),bytes.length);
 handler=makeHandler();const out=await handler(new Request(`${url}/${row.upload_id}`),actor,async()=>true);
 assert.equal((await out.json()).state,'staged');
});
test('R2 failures retain quota, cap retries, open breaker, keep readback available',async()=> {
 let attempts=0;handler=makeHandler({...bucket,put:async()=>{attempts++;throw new Error('synthetic unavailable');}});
 for(let i=0;i<3;i++) {
 const row=await begin(await input());assert.equal((await upload(row.upload_id)).status,503);
 assert.equal((await read(row.upload_id)).state,'retryable_unknown');assert.equal((await cancel(row.upload_id)).status,409);
 }
 assert.equal(attempts,6);await assert.rejects(begin(await input()),/upload_storage_unavailable/);
 assert.equal(await quota(),3*bytes.length);
});
test('quota concurrency, batch count, batch bytes and hourly reservations are bounded',async()=> {
 await pool.query('update ops.deal_document_upload_quota set reserved_bytes=104857600-25');
 const result=await Promise.allSettled([begin(await input()),begin(await input())]);
 assert.equal(result.filter(r=>r.status==='fulfilled').length,1);assert.ok(await quota()<=104857600);
 await pool.query('truncate ops.deal_document_upload');await pool.query('update ops.deal_document_upload_quota set reserved_bytes=0');
 const action_id=randomUUID();for(let i=0;i<3;i++) await begin(await input({action_id}));
 await assert.rejects(begin(await input({action_id})),/upload_rate_limited/);
 const a=randomUUID();await begin(await input({action_id:a,bytes:26214400}));await assert.rejects(begin(await input({action_id:a})),/upload_rate_limited/);
 for(let i=4;i<10;i++) await begin(await input());
 await assert.rejects(begin(await input()),/upload_rate_limited/);
});
test('two receiving claims limit a third, duplicate bytes never race a claim',async()=> {
 const rows=[];for(let i=0;i<3;i++) rows.push(await begin(await input()));
 let release;const waiting=new Promise(r=>{release=r});let start;const started=new Promise(r=>{start=r});let n=0;
 handler=makeHandler({...bucket,put:async(...args)=>{if(++n===2) start();await waiting;return bucket.put(...args);}});
 const running=[upload(rows[0].upload_id),upload(rows[1].upload_id)];await started;
 assert.equal((await upload(rows[0].upload_id)).status,409);assert.equal((await upload(rows[2].upload_id)).status,429);
 assert.equal((await cancel(rows[0].upload_id)).status,409);release();await Promise.all(running);
});
test('registered begin endpoint enforces schema and rejects chosen keys',async()=> {
 const request=body=>new Request(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
 const response=await handler(request(await input()),actor,async()=>true);
 assert.equal(response.status,200,await response.clone().text());
 assert.equal((await handler(request({...await input(),object_key:'canonical/unsafe'}),actor,async()=>true)).status>=400,true);
});
test('receipt failure before or after commit resolves through stable readback',async()=> {
 for(const committed of [false,true]) {
   const row=await begin(await input());let n=0;
   handler=createDealDocumentHandler({bucket,callTool:executeRegisteredTool,backoff:async()=>{},transaction:async fn=> {
     n++;
     if(n===3 && !committed) throw new Error('synthetic lost database');
     const result=await tx(fn);
     if(n===3 && committed) throw new Error('synthetic lost response');
     return result;
   }});
   assert.equal((await upload(row.upload_id)).status,503);
   handler=makeHandler();
   const result=await handler(new Request(`${url}/${row.upload_id}`),actor,async()=>true);
   assert.equal((await result.json()).state,'staged');
 }
});
test('interrupted stream refunds before put and the largest file stays bounded',async()=> {
 const row=await begin(await input());
 const stream=new ReadableStream({start(c){c.enqueue(bytes.subarray(0,5));c.error(new Error('synthetic interrupted'));}});
 const request=new Request(`${url}/${row.upload_id}/bytes`,{method:'POST',duplex:'half',headers:{'content-type':'application/octet-stream'},body:stream});
 assert.equal((await handler(request,actor,async()=>true)).status,503);
 assert.equal(await quota(),0);assert.equal(puts,0);
 const large=new Uint8Array(25*1024*1024);large.set(new TextEncoder().encode('%PDF-1.7\n'));large.set(new TextEncoder().encode('%%EOF'),large.length-5);
 const big=await begin(await input({bytes:large.length,sha256:await uploadDigest(large)}));
 assert.equal((await upload(big.upload_id,large)).status,201);assert.equal(await quota(),large.length);
});
test('failed temporary deletion retains charge and never touches canonical bytes',async()=> {
 stored.set('documents/canonical',{size:12});
 const row=await begin(await input());await upload(row.upload_id);
 let tries=0;handler=makeHandler({...bucket,delete:async()=>{tries++;throw new Error('synthetic denied');}});
 assert.equal((await cancel(row.upload_id)).status,503);assert.equal(tries,2);assert.equal(await quota(),bytes.length);
 assert.equal((await read(row.upload_id)).state,'staged');assert.equal(stored.has('documents/canonical'),true);
});
test('cancellation key reused for another upload refuses before deleting bytes',async()=> {
 const first=await begin(await input());const second=await begin(await input());
 await upload(first.upload_id);await upload(second.upload_id);
 const key=randomUUID();assert.equal((await cancel(first.upload_id,key)).status,200);
 const before=deletes;
 assert.equal((await cancel(second.upload_id,key)).status,409);
 assert.equal(deletes,before);assert.equal((await read(second.upload_id)).state,'staged');
});
