import { organizationTenantForActor } from './identity.js';
import { evaluateActorAuthority } from './global-boundaries.v5.js';
import { ToolError } from './tool-error.ts';
import type { Actor, Db, VerbInputs } from './verb-contract.js';

export const UPLOAD_PREFIX = '/api/v1/deal-documents/uploads';
export const UPLOAD_MAX_BYTES = 25 * 1024 * 1024;
const uuid = { type: 'string', format: 'uuid' };
const object = (properties: Record<string, unknown>) => ({ type: 'object', additionalProperties: false, properties, required: Object.keys(properties) });
export const uploadMetadata = object({ idempotency_key: uuid, action_id: uuid, deal_id: uuid,
  filename: { type: 'string', minLength: 1, maxLength: 255 },
  declared_mime: { enum: ['application/pdf','image/jpeg','image/png','image/heic','image/heif'] },
  bytes: { type: 'integer', minimum: 1, maximum: UPLOAD_MAX_BYTES },
  sha256: { type: 'string', pattern: '^[0-9a-f]{64}$' } });
export type UploadInput = VerbInputs['begin-deal-document-upload'];
export type UploadActor = Actor & { documentUploadSession?: string };
export type UploadRow = UploadInput & { id: string; actor_slug: string; session_digest: string; organization_tenant_id: string;
  object_key: string; state: 'reserved'|'receiving'|'staged'|'rejected'|'retryable_unknown'|'cancelled'; expires_at: string; charged: boolean; cancel_key: string|null };
export const uploadFail = (error: string): never => { throw new ToolError({ error }); };
export const uploadDigest = async (bytes: Uint8Array) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2,'0')).join('');
export const uploadSessionDigest = (key: string) => uploadDigest(new TextEncoder().encode(key));

const extensions: Record<string, readonly string[]> = { 'application/pdf': ['pdf'], 'image/jpeg': ['jpg','jpeg'], 'image/png': ['png'], 'image/heic': ['heic'], 'image/heif': ['heif'] };
export function validateUploadName(row: Pick<UploadInput,'filename'|'declared_mime'>) {
  if (!row.filename.trim() || /[\x00-\x1f\x7f<>\\/]/.test(row.filename) || row.filename.length > 255 ||
      !extensions[row.declared_mime]?.includes(row.filename.split('.').at(-1)?.toLowerCase() ?? '')) uploadFail('upload_format_refused');
}
export async function verifyUploadBytes(bytes: Uint8Array, row: Pick<UploadInput,'filename'|'declared_mime'|'bytes'|'sha256'>) {
  validateUploadName(row);
  if (bytes.length !== Number(row.bytes) || bytes.length < 1 || bytes.length > UPLOAD_MAX_BYTES) uploadFail('upload_size_refused');
  const text = (start: number,end: number) => new TextDecoder().decode(bytes.subarray(start,end));
  const starts = (values: number[]) => values.every((b,i) => bytes[i] === b);
  let mime = '';
  if (text(0,5) === '%PDF-' && text(Math.max(0,bytes.length-1024),bytes.length).includes('%%EOF')) mime = 'application/pdf';
  if (starts([255,216,255]) && bytes.at(-2) === 255 && bytes.at(-1) === 217) mime = 'image/jpeg';
  if (starts([137,80,78,71,13,10,26,10]) && text(12,16) === 'IHDR' && text(bytes.length-8,bytes.length-4) === 'IEND') mime = 'image/png';
  if (bytes.length >= 24 && text(4,8) === 'ftyp') {
    const size = new DataView(bytes.buffer, bytes.byteOffset,bytes.byteLength).getUint32(0);
    if (size >= 16 && size <= Math.min(bytes.length,128)) {
      const brands = [text(8,12)];
      for (let i=16;i+4<=size;i+=4) brands.push(text(i,i+4));
      if (brands.some(b => ['heic','heix','hevc','hevx'].includes(b))) mime='image/heic';
      else if (brands.some(b => ['mif1','msf1'].includes(b))) mime='image/heif';
    }
  }
  if (mime !== row.declared_mime) uploadFail('upload_format_refused');
  if (await uploadDigest(bytes) !== row.sha256) uploadFail('upload_hash_mismatch');
}
export async function authorizeUpload(c: Db, actor: UploadActor, deal: string) {
  if (!actor.documentUploadSession || !/^[0-9a-f]{64}$/.test(actor.documentUploadSession) ||
      actor.human !== true || (typeof actor.slug!=='string' || !['joe','dell'].includes(actor.slug)) || organizationTenantForActor(actor) !== 'carr-internal') uploadFail('upload_denied');
  const d = (await c.query<{owner: string}>(`select d.owner from public.deal d join public.client c on c.id=d.client_id
    join public.party p on p.id=c.party_id where d.id=$1 and c.merged_into is null and p.merged_into is null and p.deleted_at is null
    and exists(select 1 from public.actor where slug=$2 and active)`,[deal,actor.slug])).rows[0];
  if (!d || evaluateActorAuthority({ actor, action:'business.read_deal',tenant: organizationTenantForActor(actor),
    now:new Date().toISOString(),controls:{deal_owner_slug:d.owner,policy_scope:['business.read_deal'],capabilities:['deal.read']} }).decision !== 'allow') uploadFail('upload_denied');
}
export async function lockUploadQuota(c: Db) {
  return (await c.query<{reserved_bytes: string; failures: number; blocked_until: string|null}>(
    'select * from ops.deal_document_upload_quota where singleton for update')).rows[0];
}
export async function uploadRow(c: Db, actor: UploadActor, id: string,lock=true) {
  const row = (await c.query<UploadRow>(`select * from ops.deal_document_upload where id=$1 ${lock?'for update':''}`,[id])).rows[0];
  if (!row || row.actor_slug !== actor.slug || row.session_digest !== actor.documentUploadSession ||
      row.organization_tenant_id !== organizationTenantForActor(actor)) uploadFail('upload_denied');
  await authorizeUpload(c,actor,row.deal_id);
  return row;
}
export const uploadReceipt = (row: UploadRow) => ({ ok:true, schema_version:'deal-document-upload.v1', upload_id:row.id,
  deal_id:row.deal_id, filename:row.filename, declared_mime:row.declared_mime, bytes:Number(row.bytes), sha256:row.sha256,
  state:row.state !== 'cancelled' && Date.parse(row.expires_at) <= Date.now() ? 'expired' : row.state, expires_at:row.expires_at, filed:false });
export async function refundUpload(c: Db,row: UploadRow) {
  if (row.charged) {
    await c.query('update ops.deal_document_upload_quota set reserved_bytes=reserved_bytes-$1 where singleton',[row.bytes]);
    await c.query('update ops.deal_document_upload set charged=false where id=$1',[row.id]);
  }
}
export function dealDocumentUploadTools() {
  return {
    'begin-deal-document-upload': { write:true, humanOnly:true,
      description:'Reserve temporary partner document staging for the current browser session. Three files and 25 MiB per action, ten reservations per actor/hour and 100 MiB total retained staging. Never files attachments. Office files are refused.',
      inputSchema: uploadMetadata,
      handler:async (c: Db,actor: UploadActor,args: UploadInput) => {
        validateUploadName(args);
        await authorizeUpload(c,actor,args.deal_id);
        const quota=await lockUploadQuota(c);
        const prior=(await c.query<UploadRow>('select * from ops.deal_document_upload where organization_tenant_id=$1 and actor_slug=$2 and idempotency_key=$3',
          [organizationTenantForActor(actor),actor.slug,args.idempotency_key])).rows[0];
        if (prior) {
          if (prior.session_digest !== actor.documentUploadSession || (Object.keys(args) as (keyof UploadInput)[]).some(k=>String(prior[k])!==String(args[k]))) uploadFail('upload_conflict');
          return uploadReceipt(prior);
        }
        if (quota.blocked_until && Date.parse(quota.blocked_until)>Date.now()) uploadFail('upload_storage_unavailable');
        const counts=(await c.query<{count:string; bytes:string; hourly:string}>(`select count(*) filter(where action_id=$2) count,
          coalesce(sum(bytes) filter(where action_id=$2),0) bytes,
          count(*) filter(where created_at>now()-interval '1 hour') hourly from ops.deal_document_upload where actor_slug=$1 and (action_id=$2 or created_at>now()-interval '1 hour')`,[actor.slug,args.action_id])).rows[0];
        if (Number(counts.count)>=3 || Number(counts.bytes)+args.bytes>UPLOAD_MAX_BYTES || Number(counts.hourly)>=10) uploadFail('upload_rate_limited');
        if (Number(quota.reserved_bytes)+args.bytes>104857600) uploadFail('upload_storage_full');
        const id=crypto.randomUUID();
        const row=(await c.query<UploadRow>(`insert into ops.deal_document_upload
          (id,organization_tenant_id,actor_slug,session_digest,deal_id,action_id,idempotency_key,filename,declared_mime,bytes,sha256,object_key)
          values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) returning *`,[id,organizationTenantForActor(actor),actor.slug,actor.documentUploadSession,
          args.deal_id,args.action_id,args.idempotency_key,args.filename,args.declared_mime,args.bytes,args.sha256,`partner-staging/${id}`])).rows[0];
        await c.query('update ops.deal_document_upload_quota set reserved_bytes=reserved_bytes+$1 where singleton',[args.bytes]);
        return uploadReceipt(row);
      } },
    'read-deal-document-upload': { write:false,humanOnly:true,
      description:'Read the retained temporary upload receipt for its initiating actor and browser session. Unknown outcomes reconcile through the authenticated byte endpoint. Never returns object keys or files.',
      inputSchema:object({upload_id:uuid}), handler:async(c: Db,actor: UploadActor,args:{upload_id:string}) => uploadReceipt(await uploadRow(c,actor,args.upload_id,false)) },
    'cancel-deal-document-upload': { write:true,humanOnly:true,
      description:'Cancel a reservation or rejected upload and refund reserved staging bytes. Stored or uncertain bytes require the session-authenticated cancellation endpoint so deletion is confirmed before refund. Never removes canonical files.',
      inputSchema:object({upload_id:uuid,idempotency_key:uuid}),
      handler:async(c: Db,actor: UploadActor,args:{upload_id:string;idempotency_key:string}) => {
        await lockUploadQuota(c);
        const row=await uploadRow(c,actor,args.upload_id);
        if (row.state==='cancelled') { if(row.cancel_key!==args.idempotency_key) uploadFail('upload_conflict'); return uploadReceipt(row); }
        if (!['reserved','rejected'].includes(row.state)) uploadFail('upload_conflict');
        await refundUpload(c,row);
        await c.query("update ops.deal_document_upload set state='cancelled',cancel_key=$2 where id=$1",[row.id,args.idempotency_key]);
        return uploadReceipt({...row,state:'cancelled'});
      } },
  };
}
