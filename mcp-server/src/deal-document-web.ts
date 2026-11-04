import { ToolError } from './tool-error.ts';
import { UPLOAD_PREFIX, UPLOAD_MAX_BYTES, dealDocumentUploadTools, lockUploadQuota, refundUpload,
  uploadFail, uploadReceipt, uploadRow, verifyUploadBytes } from './deal-document-upload.ts';
import type { UploadActor, UploadRow } from './deal-document-upload.ts';
import type { Db } from './verb-contract.js';

type ObjectHead = { size: number; customMetadata?: Record<string,string> };
type Bucket = { put(key:string,bytes:Uint8Array,options:unknown):Promise<unknown>; head(key:string):Promise<ObjectHead|null>; delete(key:string):Promise<unknown> };
type Transaction = <T>(fn:(c:Db)=>Promise<T>)=>Promise<T>;
const path = new RegExp(`^${UPLOAD_PREFIX}/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/(bytes|cancel))?$`);
const json = (value:unknown,status=200) => new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json','cache-control':'no-store','x-content-type-options':'nosniff'}});
const codes: Record<string,number> = { upload_denied:403, upload_conflict:409, upload_expired:410,
  upload_size_refused:413, upload_format_refused:415, upload_hash_mismatch:422, upload_rate_limited:429, upload_storage_full:503, upload_storage_unavailable:503 };

async function boundedBytes(request:Request,size:number,exact=true) {
  if (!request.body) return uploadFail('upload_size_refused');
  const bytes=new Uint8Array(size);
  const reader=request.body.getReader();
  let offset=0;
  try {
    for (;;) {
      const {done,value}=await reader.read();
      if (done) break;
      if (offset+value.length>size) { void reader.cancel(); uploadFail('upload_size_refused'); }
      bytes.set(value,offset); offset+=value.length;
    }
  } finally { reader.releaseLock(); }
  if(exact && offset!==size) uploadFail('upload_size_refused');
  return bytes.subarray(0,offset);
}

export function createDealDocumentHandler({transaction,bucket,callTool,backoff = () => new Promise(resolve=>setTimeout(resolve,100))}:
  {transaction:Transaction;bucket:Bucket;callTool:(c:Db,actor:UploadActor,name:string,args:Record<string,unknown>)=>Promise<unknown>;backoff?:()=>Promise<unknown>}) {
  const storage = async <T>(operation:()=>Promise<T>,recordFailure=true):Promise<T> => {
    for(let attempt=0;attempt<2;attempt++) {
      try { return await operation(); } catch { if(attempt===0) await backoff(); }
    }
    if(recordFailure) await transaction(async c=> {
      await lockUploadQuota(c);
      await c.query(`update ops.deal_document_upload_quota set failures=failures+1,
        blocked_until=case when failures>=2 then now()+interval '5 minutes' else blocked_until end where singleton`);
    });
    console.warn(JSON.stringify({event:'partner_upload_storage_failure',attempts:2}));
    return uploadFail('upload_storage_unavailable');
  };
  return async (request:Request,actor:UploadActor,sessionActive:()=>Promise<boolean>) => {
    const match=path.exec(new URL(request.url).pathname);
    const root=new URL(request.url).pathname===UPLOAD_PREFIX;
    if(!match && !root) return json({error:'not_found'},404);
    const [,id,action]=match??[];
    const active=async()=> { if(!await sessionActive()) uploadFail('upload_denied'); };
    try {
      await active();
      if(root && request.method==='POST') {
        if(request.headers.get('content-type')!=='application/json') uploadFail('upload_format_refused');
        if(Number(request.headers.get('content-length'))>4096) uploadFail('upload_size_refused');
        const raw=await boundedBytes(request,4096,false);
        const args:unknown=JSON.parse(new TextDecoder().decode(raw));
        if(!args || typeof args!=='object' || Array.isArray(args)) return uploadFail('upload_conflict');
        return json(await transaction(async c=> {
          const receipt=await callTool(c,actor,'begin-deal-document-upload',Object.fromEntries(Object.entries(args)));
          await active();return receipt;
        }));
      }
      if(!bucket) uploadFail('upload_storage_unavailable');
      if (!action && request.method==='GET') {
        const row=await transaction(c=>uploadRow(c,actor,id));
        if(row.state==='retryable_unknown' && Date.parse(row.expires_at)>Date.now()) {
          const head=await storage(()=>bucket.head(row.object_key));
          if(head?.size===Number(row.bytes) && head.customMetadata?.sha256===row.sha256 && head.customMetadata?.upload_id===id) {
            return json(await transaction(async c=> {
              const current=await uploadRow(c,actor,id);
              await active();
              if(['receiving','retryable_unknown'].includes(current.state)) {
                await c.query("update ops.deal_document_upload set state='staged' where id=$1",[id]);
                return uploadReceipt({...current,state:'staged'});
              }
              return uploadReceipt(current);
            }));
          }
        }
        await active();
        return json(uploadReceipt(row));
      }
      if(action==='cancel' && request.method==='POST') {
        const key=request.headers.get('idempotency-key');
        if(!key || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(key)) return uploadFail('upload_conflict');
        return json(await transaction(async c=> {
          await lockUploadQuota(c);
          const row=await uploadRow(c,actor,id);
          if(['receiving','retryable_unknown'].includes(row.state)) uploadFail('upload_conflict');
          if(row.state==='staged') {
            await storage(()=>bucket.delete(row.object_key),false);
            await active();
            await refundUpload(c,row);
            await c.query("update ops.deal_document_upload set state='cancelled',cancel_key=$2 where id=$1",[id,key]);
            return uploadReceipt({...row,state:'cancelled'});
          }
          return dealDocumentUploadTools()['cancel-deal-document-upload'].handler(c,actor,{upload_id:id,idempotency_key:key});
        }));
      }
      if(action!=='bytes' || request.method!=='POST') return json({error:'method_not_allowed'},405);
      if(request.headers.get('content-type')!=='application/octet-stream' || request.headers.has('content-encoding')) uploadFail('upload_format_refused');
      const length=request.headers.get('content-length');
      if(length && (!/^[1-9][0-9]{0,8}$/.test(length) || Number(length)>UPLOAD_MAX_BYTES)) uploadFail('upload_size_refused');
      const row=await transaction(async c=> {
        const quota=await lockUploadQuota(c);
        const current=await uploadRow(c,actor,id);
        if(Date.parse(current.expires_at)<=Date.now()) uploadFail('upload_expired');
        if(current.state==='staged') return current;
        if(current.state!=='reserved') uploadFail('upload_conflict');
        if(quota.blocked_until && Date.parse(quota.blocked_until)>Date.now()) uploadFail('upload_storage_unavailable');
        const count=(await c.query<{n:string}>("select count(*) n from ops.deal_document_upload where actor_slug=$1 and state='receiving'",[actor.slug])).rows[0];
        if(Number(count.n)>=2) uploadFail('upload_rate_limited');
        await c.query("update ops.deal_document_upload set state='receiving' where id=$1",[id]);
        return current;
      });
      if(row.state==='staged') return json(uploadReceipt(row));
      let attempted=false;
      try {
        if(length && Number(length)!==Number(row.bytes)) uploadFail('upload_size_refused');
        const bytes=await boundedBytes(request,Number(row.bytes));
        await verifyUploadBytes(bytes,row);
        await active();
        await transaction(async c=> { await uploadRow(c,actor,id); });
        attempted=true;
        const stored=await storage(()=>bucket.put(row.object_key,bytes,{httpMetadata:{contentType:'application/octet-stream'},
          customMetadata:{sha256:row.sha256,upload_id:id}}));
        if(!stored) uploadFail('upload_storage_unavailable');
        await active();
        const receipt=await transaction(async c=> {
          await lockUploadQuota(c);
          const current=await uploadRow(c,actor,id);
          await active();
          if(!['receiving','staged'].includes(current.state)) uploadFail('upload_conflict');
          if(Date.parse(current.expires_at)<=Date.now()) uploadFail('upload_expired');
          await c.query("update ops.deal_document_upload set state='staged' where id=$1",[id]);
          await c.query('update ops.deal_document_upload_quota set failures=0,blocked_until=null where singleton');
          return uploadReceipt({...current,state:'staged'});
        });
        return json(receipt,201);
      } catch(error) {
        await transaction(async c=> {
          await lockUploadQuota(c);
          const current=(await c.query<UploadRow>('select * from ops.deal_document_upload where id=$1 for update',[id])).rows[0];
          if(current.state==='receiving') {
            if(!attempted) await refundUpload(c,current);
            await c.query('update ops.deal_document_upload set state=$2 where id=$1',[id,attempted?'retryable_unknown':'rejected']);
          }
        });
        throw error;
      }
    } catch(error) {
      const code=error instanceof ToolError ? error.payload.error : 'upload_storage_unavailable';
      return json({error:code},codes[code]??503);
    }
  };
}
