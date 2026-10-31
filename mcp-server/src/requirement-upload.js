// Service-only ingress for the existing share surface. No download or release route.
import { isRequirementPdf, REQUIREMENT_UPLOAD_MAX_BYTES } from './deal-files.js';
export const MAX_UPLOAD_BYTES = REQUIREMENT_UPLOAD_MAX_BYTES;
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const PATH = new RegExp(`^/requirement-upload/(carr-internal)/(${UUID})/([a-z][a-z0-9_]{0,79})/(${UUID})$`);
const json = (body, status) => new Response(JSON.stringify(body), { status,
  headers: { 'content-type': 'application/json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' } });
const hex = bytes => Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
const digest = async bytes => hex(await crypto.subtle.digest('SHA-256', bytes));
const fail = (status, error) => { throw Object.assign(new Error(error), { status }); };

export function createRequirementUploadHandler({ transaction, bucket, visitorSecret }) {
  return async request => {
    const path = PATH.exec(new URL(request.url).pathname);
    if (!path) return json({ error: 'upload_denied' }, 403);
    if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405);
    const length = request.headers.get('content-length');
    if (!length || !/^[1-9][0-9]{0,6}$/.test(length) || Number(length) > MAX_UPLOAD_BYTES)
      return json({ error: 'upload_size_refused' }, 413);
    if (request.headers.get('content-type') !== 'application/pdf' || request.headers.has('content-encoding'))
      return json({ error: 'unsupported_mime' }, 415);
    const token = /^Bearer ([0-9a-f]{64})$/.exec(request.headers.get('authorization') || '')?.[1];
    // The share Worker must forward the platform's visitor IP, never a form field.
    const visitor = request.headers.get('cf-connecting-ip');
    if (!token || !visitor || visitor.length > 64 || !request.body) return json({ error: 'upload_denied' }, 403);
    const [, tenant, deal, row, grant] = path;
    let reserved = false, objectKey;
    try {
      if (!visitorSecret) fail(503, 'upload_unavailable');
      const tokenHash = await digest(new TextEncoder().encode(token));
      const hmacKey = await crypto.subtle.importKey('raw', new TextEncoder().encode(visitorSecret),
        { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
      const visitorHash = hex(await crypto.subtle.sign('HMAC', hmacKey, new TextEncoder().encode(visitor)));
      const claim = await transaction(async c => {
        const g = (await c.query(`select request_id from ops.requirement_upload_grant
          where id=$1 and organization_tenant_id=$2 and deal_id=$3 and row_key=$4
            and action='upload' and token_sha256=$5 and expires_at>now()
            and revoked_at is null and consumed_at is null`, [grant, tenant, deal, row, tokenHash])).rows[0];
        if (!g) fail(403, 'upload_denied');
        return g;
      });
      await transaction(async c => {
        // Reserve ten writes, including bounded pruning, object cleanup and quota refund.
        const budget = await c.query(`insert into ops.requirement_upload_visitor(visitor_hash,window_start,writes)
          values($1,now(),10) on conflict(visitor_hash) do update set
          writes=case when ops.requirement_upload_visitor.window_start<=now()-interval '1 minute' then 10
            else ops.requirement_upload_visitor.writes+10 end,
          window_start=now()
          where ops.requirement_upload_visitor.window_start<=now()-interval '1 minute'
            or ops.requirement_upload_visitor.writes<=10 returning visitor_hash`, [visitorHash]);
        if (!budget.rows.length) fail(429, 'upload_rate_limited');
        await c.query(`delete from ops.requirement_upload_visitor where visitor_hash in
          (select visitor_hash from ops.requirement_upload_visitor
           where window_start<now()-interval '24 hours' order by window_start,visitor_hash limit 1)`);
      });
      await transaction(async c => {
        const consumed = await c.query(`update ops.requirement_upload_grant set consumed_at=now()
          where id=$1 and consumed_at is null and revoked_at is null and expires_at>now() returning id`, [grant]);
        if (!consumed.rows.length) fail(403, 'upload_denied');
        const quota = await c.query(`insert into ops.requirement_upload_quota(singleton,reserved_bytes) values(true,$1)
          on conflict(singleton) do update set reserved_bytes=ops.requirement_upload_quota.reserved_bytes+$1
          where ops.requirement_upload_quota.reserved_bytes+$1<=104857600 returning reserved_bytes`, [Number(length)]);
        if (!quota.rows.length) fail(503, 'upload_storage_full');
      });
      reserved = true;
      let received = 0;
      const bounded = new TransformStream({ transform(chunk, controller) {
        received += chunk.byteLength;
        if (received > Number(length)) throw new Error('upload_size_refused');
        controller.enqueue(chunk);
      } });
      const bytes = new Uint8Array(await new Response(request.body.pipeThrough(bounded)).arrayBuffer());
      if (received !== Number(length)) fail(413, 'upload_size_refused');
      if (!isRequirementPdf(bytes))
        fail(415, 'unsupported_mime');
      const sha256 = await digest(bytes);
      const key = `quarantine/${tenant}/${deal}/${row}/${grant}.pdf`;
      objectKey = key;
      await bucket.put(key, bytes, { httpMetadata: { contentType: 'application/octet-stream' },
        customMetadata: { state: 'quarantined', sha256 } });
      const receipt = await transaction(async c => {
        const active = await c.query(`select id from ops.requirement_upload_grant
          where id=$1 and revoked_at is null and expires_at>now() for update`, [grant]);
        if (!active.rows.length) fail(403, 'upload_denied');
        return (await c.query(`insert into ops.requirement_upload_receipt
        (grant_id,request_id,r2_key,sha256,bytes,mime) values($1,$2,$3,$4,$5,'application/pdf')
        returning id, request_id, state`, [grant, claim.request_id, key, sha256, bytes.length])).rows[0];
      });
      reserved = false;
      return json(receipt, 201);
    } catch (error) {
      if (reserved) {
        try {
          await transaction(async c => {
            const receipt = await c.query('select id from ops.requirement_upload_receipt where grant_id=$1', [grant]);
            if (receipt.rows.length) return;
            if (objectKey) await bucket.delete(objectKey);
            await c.query(`update ops.requirement_upload_quota
              set reserved_bytes=reserved_bytes-$1 where singleton`, [Number(length)]);
          });
        } catch { return json({ error: 'upload_unavailable' }, 503); }
      }
      if (error.message === 'upload_size_refused') return json({ error: 'upload_size_refused' }, 413);
      if ([403, 415, 429, 503].includes(error.status)) return json({ error: error.message }, error.status);
      return json({ error: 'upload_unavailable' }, 503);
    }
  };
}
