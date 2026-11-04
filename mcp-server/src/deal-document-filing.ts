import { fileCanonicalDealAttachment } from './deal-files.js';
import { authorizeUpload, lockUploadQuota, refundUpload, uploadFail, uploadReceipt, uploadRow } from './deal-document-upload.ts';
import type { UploadActor, UploadRow } from './deal-document-upload.ts';
import type { Db, WriteEvent } from './verb-contract.js';

const backoff=()=>new Promise(resolve=>setTimeout(resolve,100));
async function boundedStorage<T>(operation:()=>Promise<T>):Promise<T> {
  let timer:ReturnType<typeof setTimeout>|undefined;
  try {
    return await Promise.race([operation(),new Promise<never>((_resolve,reject)=>{timer=setTimeout(()=>reject(new Error('storage_timeout')),5000);})]);
  } finally {if(timer!==undefined) clearTimeout(timer);}
}
export function dealDocumentFilingTools({writeEvent}:{writeEvent:WriteEvent}) {
  return {'commit-deal-document-upload':{
    write:true,humanOnly:true,
    description:'File verified staged bytes once as a canonical attachment under the reserved deal. Current initiating partner session required. Returns attachment ID, deal and SHA256. Replays converge to the same attachment; sends and extracts nothing.',
    inputSchema:{type:'object',additionalProperties:false,properties:{upload_id:{type:'string',format:'uuid'},idempotency_key:{type:'string',format:'uuid'}},required:['upload_id','idempotency_key']},
    handler:async(c:Db,actor:UploadActor,args:{upload_id:string;idempotency_key:string})=>{
      await lockUploadQuota(c);
      const row=await uploadRow(c,actor,args.upload_id);
      const sessionActive=actor.documentUploadSessionActive;
      if(!sessionActive) return uploadFail('upload_denied');
      if(!await sessionActive()) uploadFail('upload_denied');
      const prior=(await c.query<{id:string}>(`select id from ops.deal_document_upload where organization_tenant_id=$1 and actor_slug=$2 and $3::uuid=commit_key`,
        [row.organization_tenant_id,actor.slug,args.idempotency_key])).rows[0];
      if(prior && prior.id!==row.id) uploadFail('upload_conflict');
      if(row.state==='filed') {
        if(row.commit_key!==args.idempotency_key) uploadFail('upload_conflict');
        return uploadReceipt(row);
      }
      if(Date.parse(row.expires_at)<=Date.now()) uploadFail('upload_expired');
      if(row.state!=='staged') uploadFail('upload_conflict');
      const bucket=actor.documentUploadBucket;
      if(!bucket) return uploadFail('upload_denied');
      let head;
      for(let attempt=0;attempt<2;attempt++) {
        try{head=await boundedStorage(()=>bucket.head(row.object_key));break;}
        catch{if(attempt===0) await backoff();else console.warn(JSON.stringify({event:'partner_filing_storage_failure',attempts:2}));}
      }
      if(!head || head.size!==Number(row.bytes) || head.customMetadata?.sha256!==row.sha256 || head.customMetadata?.upload_id!==row.id) uploadFail('upload_storage_unavailable');
      await authorizeUpload(c,actor,row.deal_id);
      if(!await sessionActive()) uploadFail('upload_denied');
      const saved=await fileCanonicalDealAttachment(c,actor,{deal_id:row.deal_id,r2_key:row.object_key,filename:row.filename,mime:row.declared_mime,sha256:row.sha256,bytes:row.bytes});
      await writeEvent(c,actor,'commit-deal-document-upload','deal',row.deal_id,{field:'attachment',new:{attachment_id:saved.attachment_id,sha256:row.sha256,bytes:row.bytes},cause:'human_stated',idempotency_key:args.idempotency_key});
      await c.query("update ops.deal_document_upload set state='filed',attachment_id=$2,commit_key=$3 where id=$1",[row.id,saved.attachment_id,args.idempotency_key]);
      if(!saved.existing) await refundUpload(c,row);
      return uploadReceipt({...row,state:'filed',attachment_id:saved.attachment_id});
    }
  }};
}

type CleanupBucket={delete(key:string):Promise<unknown>};
type Transaction=<T>(fn:(c:Db)=>Promise<T>)=>Promise<T>;
export async function cleanupDealDocumentUploads({transaction,bucket,backoff:pause=backoff}:{transaction:Transaction;bucket:CleanupBucket;backoff?:()=>Promise<unknown>}) {
  const deadline=Date.now()+20000;
  const ids=await transaction(async c=>(await c.query<{id:string}>(`select u.id from ops.deal_document_upload u where expires_at<=now() and cleaned_at is null and cleanup_attempts<2
    and state<>'receiving' and not exists(select 1 from public.attachment a where a.r2_key=u.object_key)
    order by expires_at,id limit 20`)).rows);
  for(const {id} of ids) {
    for(let attempt=0;attempt<2;attempt++) {
      if(Date.now()>=deadline) return {examined:ids.length};
      const row=await transaction(async c=>{
        await lockUploadQuota(c);
        const current=(await c.query<UploadRow & {cleanup_attempts:number;cleaned_at:string|null}>(`select * from ops.deal_document_upload where id=$1 for update`,[id])).rows[0];
        if(!current || current.cleaned_at || current.cleanup_attempts>=2 || current.state==='receiving' || Date.parse(current.expires_at)>Date.now()) return null;
        if((await c.query('select id from public.attachment where r2_key=$1 limit 1',[current.object_key])).rows.length) return null;
        await c.query("update ops.deal_document_upload set cleanup_attempts=cleanup_attempts+1,cleanup_error='delete_outcome_unknown',state=case when state='filed' then state else 'expired' end where id=$1",[id]);
        return current;
      });
      if(!row) break;
      let deleted=false;
      try{await boundedStorage(()=>bucket.delete(row.object_key));deleted=true;}catch{console.warn(JSON.stringify({event:'partner_upload_cleanup_failure',upload_id:id}));}
      await transaction(async c=>{
        await lockUploadQuota(c);
        const current=(await c.query<UploadRow>('select * from ops.deal_document_upload where id=$1 for update',[id])).rows[0];
        if(deleted) {
          await refundUpload(c,current);
          await c.query('update ops.deal_document_upload set cleaned_at=now(),cleanup_error=null where id=$1',[id]);
        } else await c.query("update ops.deal_document_upload set cleanup_error='storage_delete_failed' where id=$1 and cleaned_at is null",[id]);
      });
      if(deleted) break;
      if(attempt===0) await pause();
    }
  }
  return {examined:ids.length};
}
