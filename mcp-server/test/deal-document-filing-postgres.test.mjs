import test, { before, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { dealDocumentUploadTools, uploadDigest } from '../src/deal-document-upload.ts';
import { dealDocumentFilingTools, cleanupDealDocumentUploads } from '../src/deal-document-filing.ts';
import { dealFileTools } from '../src/deal-files.js';
import { executeRegisteredTool } from '../src/tools.js';

let fixture,pool,actor,deal,stored,deletes;
const bucket={head:async key=>stored.get(key)??null,delete:async key=>{deletes++;stored.delete(key);}};
const filing=dealDocumentFilingTools({writeEvent:async(c,a,v,t,id,fields)=>c.query('insert into audit(cause) values($1)',[fields.cause])});
const tx=async fn=>{const c=await pool.connect();try{await c.query('begin');await c.query('set local role carr_writer');const r=await fn(c);await c.query('commit');return r;}catch(e){await c.query('rollback');throw e;}finally{c.release();}};
const commit=(id,key=randomUUID(),who=actor)=>tx(c=>filing['commit-deal-document-upload'].handler(c,who,{upload_id:id,idempotency_key:key}));
const read=id=>tx(c=>dealDocumentUploadTools()['read-deal-document-upload'].handler(c,actor,{upload_id:id}));
const count=async()=>Number((await pool.query('select count(*) n from attachment')).rows[0].n);
const stage=async(extra={})=>{
 const bytes=new Uint8Array([255,216,255,10,255,217]);
 const args={idempotency_key:randomUUID(),action_id:randomUUID(),deal_id:deal,filename:'phone.jpg',declared_mime:'image/jpeg',bytes:bytes.length,sha256:await uploadDigest(bytes),...extra};
 const r=await tx(c=>dealDocumentUploadTools()['begin-deal-document-upload'].handler(c,actor,args));
 await pool.query("update ops.deal_document_upload set state='staged' where id=$1",[r.upload_id]);
 stored.set(`partner-staging/${r.upload_id}`,{size:args.bytes,customMetadata:{sha256:args.sha256,upload_id:r.upload_id}});return r;
};
before(async()=>{
 fixture=await postgresDatabase('partner-filing-');const c=fixture.client;
 await c.query(`create role carr_writer;create role carr_reader;create role carr_jobs;create schema ops;
 grant usage on schema ops to carr_writer;
 create table actor(id uuid primary key,slug text unique,active boolean);
 create table party(id uuid primary key,merged_into uuid,deleted_at timestamptz);
 create table client(id uuid primary key,party_id uuid,merged_into uuid);
 create table deal(id uuid primary key,client_id uuid,owner text);
 create table attachment(id uuid primary key default gen_random_uuid(),subject_type text,subject_id uuid,r2_key text,filename text,mime text,sha256 text,bytes bigint,created_by uuid,created_at timestamptz default now(),deleted_at timestamptz);
 create table audit(cause text);
 create view v_ref_index as select 'deal'::text subject_type,id subject_id from deal;
 create function ops.scac_current_grant_contract() returns jsonb language sql immutable as 'select jsonb_build_object()';
 grant select on actor,party,client,deal to carr_writer;grant select,insert on attachment,audit to carr_writer;`);
 for(const file of ['1086_deal_documents.sql','1146_deal_document_upload.sql','1147_deal_document_filing.sql']) await c.query(await readFile(new URL('../../migrations/'+file,import.meta.url),'utf8'));
 await c.query('grant select on v_deal_documents to carr_writer');
 actor={id:randomUUID(),slug:'joe',human:true,documentUploadSession:'a'.repeat(64),documentUploadBucket:bucket,documentUploadSessionActive:async()=>true};
 await c.query("insert into actor values($1,'joe',true),($2,'dell',true)",[actor.id,randomUUID()]);
 deal=randomUUID();const party=randomUUID(),client=randomUUID();await c.query('insert into party(id) values($1)',[party]);await c.query('insert into client(id,party_id) values($1,$2)',[client,party]);await c.query("insert into deal values($1,$2,'joe')",[deal,client]);
 pool=new pg.Pool({host:c.host,port:c.port,user:c.user,database:c.database});
});
beforeEach(async()=>{await pool.query('truncate ops.deal_document_upload,attachment,audit');await pool.query('update ops.deal_document_upload_quota set reserved_bytes=0,failures=0,blocked_until=null');stored=new Map();deletes=0;});
after(async()=>{await pool?.end();await fixture?.close();});
test('commit_verified_photo_once_and_list_under_selected_deal',async()=>{
 const row=await stage();const key=randomUUID();const filed=await commit(row.upload_id,key);
 assert.equal(filed.state,'filed');assert.equal(filed.filed,true);assert.equal(filed.deal_id,deal);
 assert.equal((await commit(row.upload_id,key)).attachment_id,filed.attachment_id);
 assert.equal((await commit(row.upload_id,key,{...actor,documentUploadBucket:undefined})).attachment_id,filed.attachment_id);
 assert.equal((await read(row.upload_id)).attachment_id,filed.attachment_id);
 const list=await tx(c=>dealFileTools({})['list-deal-documents'].handler(c,actor,{deal_id:deal}));
 assert.equal(list.documents[0].id,filed.attachment_id);assert.equal(list.documents[0].content_hash,row.sha256);
 assert.equal(await count(),1);assert.deepEqual((await pool.query('select cause from audit')).rows,[{cause:'human_stated'}]);
});
test('concurrent saves converge and key reuse across deals refuses',async()=>{
 const row=await stage(),key=randomUUID();const results=await Promise.all([commit(row.upload_id,key),commit(row.upload_id,key)]);
 assert.equal(results[0].attachment_id,results[1].attachment_id);assert.equal(await count(),1);
 const otherDeal=randomUUID();await pool.query('insert into deal select $1,client_id,owner from deal limit 1',[otherDeal]);
 const other=await stage({deal_id:otherDeal});await assert.rejects(commit(other.upload_id,key),/upload_conflict/);
 await assert.rejects(commit(row.upload_id),/upload_conflict/);
});
test('missing or mismatched R2, revoked visibility, session and expiry never file',async()=>{
 const row=await stage(),key=`partner-staging/${row.upload_id}`,good=stored.get(key);
 for(const head of [null,{...good,size:99},{...good,customMetadata:{sha256:'0'.repeat(64),upload_id:row.upload_id}}]){stored.set(key,head);await assert.rejects(commit(row.upload_id),/upload_storage_unavailable/);assert.equal(await count(),0);}
 stored.set(key,good);let attempts=0;
 await assert.rejects(commit(row.upload_id,randomUUID(),{...actor,documentUploadBucket:{head:async()=>{attempts++;throw Error('offline');}}}),/upload_storage_unavailable/);assert.equal(attempts,2);
 await assert.rejects(commit(row.upload_id,randomUUID(),{...actor,documentUploadSession:'b'.repeat(64)}),/upload_denied/);
 await pool.query("update actor set active=false where slug='joe'");await assert.rejects(commit(row.upload_id),/upload_denied/);await pool.query("update actor set active=true where slug='joe'");
 await pool.query("update ops.deal_document_upload set expires_at=now()-interval '1 second'");await assert.rejects(commit(row.upload_id),/upload_expired/);
});
test('reuse email canonical attachment, rollback and filed read after expiry',async()=>{
 const row=await stage(),existing=randomUUID();await pool.query("insert into attachment(id,subject_type,subject_id,r2_key,sha256) values($1,'deal',$2,'documents/email/hash/file.pdf',$3)",[existing,deal,row.sha256]);
 assert.equal((await commit(row.upload_id)).attachment_id,existing);assert.equal(await count(),1);
 await pool.query("update ops.deal_document_upload set expires_at=now()-interval '1 second'");await cleanupDealDocumentUploads({transaction:tx,bucket,backoff:async()=>{}});
 assert.equal(stored.has(`partner-staging/${row.upload_id}`),false);assert.equal((await read(row.upload_id)).state,'filed');
 const next=await stage({sha256:'1'.repeat(64)});
 await assert.rejects(tx(async c=>{await filing['commit-deal-document-upload'].handler(c,actor,{upload_id:next.upload_id,idempotency_key:randomUUID()});throw Error('rollback');}),/rollback/);
 assert.equal(await count(),1);assert.equal((await read(next.upload_id)).state,'staged');
});
test('cleanup preserves canonical bytes and durably stops at two failed deletes',async()=>{
 const row=await stage(),filed=await commit(row.upload_id);await pool.query("update ops.deal_document_upload set expires_at=now()-interval '1 second'");
 await cleanupDealDocumentUploads({transaction:tx,bucket,backoff:async()=>{}});assert.equal(stored.has(`partner-staging/${row.upload_id}`),true);assert.equal((await read(row.upload_id)).attachment_id,filed.attachment_id);
 const orphan=await stage();await pool.query("update ops.deal_document_upload set expires_at=now()-interval '1 second' where id=$1",[orphan.upload_id]);
 let n=0;const failing={...bucket,delete:async()=>{n++;throw Error('offline');}};
 await cleanupDealDocumentUploads({transaction:tx,bucket:failing,backoff:async()=>{}});await cleanupDealDocumentUploads({transaction:tx,bucket:failing,backoff:async()=>{}});
 assert.equal(n,2);assert.equal(Number((await pool.query('select reserved_bytes from ops.deal_document_upload_quota')).rows[0].reserved_bytes),6);
});
test('cancel and expiry race with commit without deleting filed bytes',async()=>{
 const row=await stage(),key=randomUUID();let release,started;
 const ready=new Promise(r=>{started=r;}),gate=new Promise(r=>{release=r;});
 const slow={...actor,documentUploadBucket:{head:async k=>{started();await gate;return bucket.head(k);}}};
 const running=commit(row.upload_id,key,slow);await ready;
 const cancel=tx(c=>dealDocumentUploadTools()['cancel-deal-document-upload'].handler(c,actor,{upload_id:row.upload_id,idempotency_key:randomUUID()}));
 const cancelled=assert.rejects(cancel,/upload_conflict/);release();await running;await cancelled;
 assert.equal(deletes,0);assert.equal(await count(),1);
});
test('cleanup handles at most twenty orphans and receiving stays charged',async()=>{
 for(let i=0;i<21;i++){
   const row=await stage();await pool.query("update ops.deal_document_upload set created_at=now()-interval '2 hours',expires_at=now()-interval '1 second' where id=$1",[row.upload_id]);
 }
 const result=await cleanupDealDocumentUploads({transaction:tx,bucket,backoff:async()=>{}});
 assert.equal(result.examined,20);assert.equal(deletes,20);assert.equal(stored.size,1);
 await pool.query("update ops.deal_document_upload set state='receiving' where cleaned_at is null");
 assert.equal((await cleanupDealDocumentUploads({transaction:tx,bucket,backoff:async()=>{}})).examined,0);
 assert.equal(Number((await pool.query('select reserved_bytes from ops.deal_document_upload_quota')).rows[0].reserved_bytes),6);
});
test('registered commit schema rejects inferred deal or caller storage',async()=>{
 const row=await stage();
 await assert.rejects(tx(c=>executeRegisteredTool(c,actor,'commit-deal-document-upload',{upload_id:row.upload_id,idempotency_key:randomUUID(),deal_id:deal})),/./);
 assert.equal(await count(),0);
});
test('revocation during verification and crash after cleanup claim fail closed',async()=>{
 const row=await stage();let active=true;
 const who={...actor,documentUploadSessionActive:async()=>active,documentUploadBucket:{head:async k=>{active=false;return bucket.head(k);}}};
 await assert.rejects(commit(row.upload_id,randomUUID(),who),/upload_denied/);assert.equal(await count(),0);
 await pool.query("update ops.deal_document_upload set expires_at=now()-interval '1 second'");let n=0;
 await assert.rejects(cleanupDealDocumentUploads({bucket,backoff:async()=>{},transaction:async fn=>{const result=await tx(fn);if(++n===2)throw Error('worker died');return result;}}),/worker died/);
 assert.equal(deletes,0);
 assert.equal((await pool.query('select cleanup_attempts from ops.deal_document_upload')).rows[0].cleanup_attempts,1);
 await cleanupDealDocumentUploads({transaction:tx,bucket,backoff:async()=>{}});assert.equal(deletes,1);
 assert.equal((await pool.query('select cleanup_attempts from ops.deal_document_upload')).rows[0].cleanup_attempts,2);
});
