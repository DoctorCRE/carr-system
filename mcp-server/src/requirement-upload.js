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

export function createRequirementUploadHandler({ transaction, bucket }) {
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
    try {
      const tokenHash = await digest(new TextEncoder().encode(token));
      const visitorHash = await digest(new TextEncoder().encode(visitor));
      const claim = await transaction(async c => {
        const g = (await c.query(`select request_id from ops.requirement_upload_grant
          where id=$1 and organization_tenant_id=$2 and deal_id=$3 and row_key=$4
            and action='upload' and token_sha256=$5 and expires_at>now()
            and revoked_at is null and consumed_at is null for update`, [grant, tenant, deal, row, tokenHash])).rows[0];
        if (!g) fail(403, 'upload_denied');
        // Five writes per attempt: visitor reservation, byte reservation, claim, R2 put, receipt.
        // Failed attempts keep reservations, so retries cannot make the ceiling optimistic.
        const budget = await c.query(`insert into ops.requirement_upload_visitor(visitor_hash,window_start,writes)
          values($1,now(),5) on conflict(visitor_hash) do update set
          writes=case when ops.requirement_upload_visitor.window_start<=now()-interval '1 minute' then 5
            else ops.requirement_upload_visitor.writes+5 end,
          window_start=now()
          where ops.requirement_upload_visitor.window_start<=now()-interval '1 minute'
            or ops.requirement_upload_visitor.writes<=15 returning visitor_hash`, [visitorHash]);
        if (!budget.rows.length) fail(429, 'upload_rate_limited');
        const quota = await c.query(`insert into ops.requirement_upload_quota(singleton,reserved_bytes) values(true,$1)
          on conflict(singleton) do update set reserved_bytes=ops.requirement_upload_quota.reserved_bytes+$1
          where ops.requirement_upload_quota.reserved_bytes+$1<=104857600 returning reserved_bytes`, [Number(length)]);
        if (!quota.rows.length) fail(503, 'upload_storage_full');
        await c.query('update ops.requirement_upload_grant set consumed_at=now() where id=$1', [grant]);
        return g;
      });
      let received = 0;
      const bounded = new TransformStream({ transform(chunk, controller) {
        received += chunk.byteLength;
        if (received > Number(length)) throw new Error('upload_size_refused');
        controller.enqueue(chunk);
      } });
      const bytes = new Uint8Array(await new Response(request.body.pipeThrough(bounded)).arrayBuffer());
      if (received !== Number(length)) return json({ error: 'upload_size_refused' }, 413);
      if (!isRequirementPdf(bytes))
        return json({ error: 'unsupported_mime' }, 415);
      const sha256 = await digest(bytes);
      const key = `quarantine/${tenant}/${deal}/${row}/${grant}.pdf`;
      await bucket.put(key, bytes, { httpMetadata: { contentType: 'application/octet-stream' },
        customMetadata: { state: 'quarantined', sha256 } });
      const receipt = await transaction(async c => (await c.query(`insert into ops.requirement_upload_receipt
        (grant_id,request_id,r2_key,sha256,bytes,mime) values($1,$2,$3,$4,$5,'application/pdf')
        returning id, request_id, state`, [grant, claim.request_id, key, sha256, bytes.length])).rows[0]);
      return json(receipt, 201);
    } catch (error) {
      if (error.message === 'upload_size_refused') return json({ error: 'upload_size_refused' }, 413);
      if ([403, 429, 503].includes(error.status)) return json({ error: error.message }, error.status);
      return json({ error: 'upload_unavailable' }, 503);
    }
  };
}
