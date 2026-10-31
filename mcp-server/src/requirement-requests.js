// Own row requests and their upload grants; receipt is never satisfaction.
import { ToolError } from './tool-error.ts';
import { readDealChecklist } from './deal-checklist.ts';
import { organizationTenantForActor } from './identity.js';
import { isCalendarDate } from './calendar-date.ts';

const uuid = { type: 'string', pattern: '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' };
const refuse = error => { throw new ToolError({ error }); };
export function requirementRequestTools({ withEnvelope, writeEvent, resolveSubject }) {
  const write = (name, properties, required, description, fn) => ({
    write: true, authorityOnly: name !== 'request-checklist-document', description,
    inputSchema: { type: 'object', additionalProperties: false,
      properties: { idempotency_key: { type: 'string' }, ...properties }, required: ['idempotency_key', ...required] },
    handler: (c, actor, args) => withEnvelope(c, actor, name, args, async () => {
      const tenant = organizationTenantForActor(actor);
      const result = await fn(c, actor, args, tenant);
      await writeEvent(c, actor, name, 'deal', result.deal_id,
        { field: 'requirement_request', new: { request_id: result.request_id || result.request?.id,
          ...(result.grant_id ? { grant_id: result.grant_id } : {}) }, idempotency_key: args.idempotency_key });
      return { ok: true, ...result };
    }),
  });
  return {
    'request-checklist-document': write('request-checklist-document', {
      deal: { type: 'string' }, row_key: { type: 'string', maxLength: 80 },
      recipient_email: { type: 'string', format: 'email', maxLength: 254 }, thread_activity_id: uuid,
      owner: { type: 'string', minLength: 1, maxLength: 100 }, due_on: { type: 'string', format: 'date' },
    }, ['deal', 'row_key', 'owner', 'due_on'],
    'Request a document for one current doctrine checklist row. Copies its document type and doctrine evidence, with an explicit owner and due date. Existing critical dates, lease and invoice milestones retain their own authority. Repeating a row with different details refuses. Receiving a document never satisfies a requirement.',
    async (c, actor, args, tenant) => {
      if (!isCalendarDate(args.due_on) || !args.owner?.trim()) refuse('invalid_requirement');
      const subject = await resolveSubject(c, args.deal);
      if (subject.type !== 'deal') refuse('not_a_deal');
      const record = (await c.query('select id, deal_type as type, owner from deal where id=$1', [subject.id])).rows[0];
      if (!record) refuse('deal_not_found');
      if (args.thread_activity_id && !(await c.query(`select id from activity where id=$1 and deal_id=$2
        and kind in ('email_in','email_out')`, [args.thread_activity_id, record.id])).rows.length)
        refuse('requirement_thread_invalid');
      const recipient = args.recipient_email?.trim().toLowerCase() ?? null;
      const checklist = await readDealChecklist(c, record);
      const row = checklist.phases.flatMap(p => p.items).find(item => item.id === args.row_key);
      if (checklist.template_state !== 'current' || !row?.document_type) refuse('row_not_requestable');
      const request = (await c.query(`insert into ops.requirement_request
        (organization_tenant_id,deal_id,row_key,owner,due_on,document_type,source,created_by,recipient_email,thread_activity_id)
        values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) on conflict(organization_tenant_id,deal_id,row_key) do nothing returning *, to_char(due_on,'YYYY-MM-DD') as due_on`,
      [tenant, record.id, row.id, args.owner.trim(), args.due_on, row.document_type, JSON.stringify(row.source), actor.id, recipient, args.thread_activity_id ?? null])).rows[0]
        || (await c.query("select *,to_char(due_on,'YYYY-MM-DD') as due_on from ops.requirement_request where organization_tenant_id=$1 and deal_id=$2 and row_key=$3",
          [tenant, record.id, row.id])).rows[0];
      if (request.owner !== args.owner.trim() || request.due_on !== args.due_on ||
          request.recipient_email !== recipient || request.thread_activity_id !== (args.thread_activity_id ?? null))
        refuse('requirement_already_requested');
      return { deal_id: record.id, request };
    }),
    'issue-requirement-upload': write('issue-requirement-upload', {
      request_id: uuid, token_sha256: { type: 'string', pattern: '^[0-9a-f]{64}$' },
      expires_in_seconds: { type: 'integer', minimum: 60, maximum: 86400 },
    }, ['request_id', 'token_sha256', 'expires_in_seconds'],
    'Issue a single-use PDF upload capability for a requested row. Caller generates a cryptographically random 32-byte token and supplies only its SHA-256. Keep the bearer in the guest link fragment; never persist or log it. Upload path binds tenant, deal, row, grant and upload action. Limits are 1 MiB and at most one day. No file download or satisfaction authority.',
    async (c, _actor, args, tenant) => {
      const row = (await c.query('select * from ops.requirement_request where id=$1 and organization_tenant_id=$2', [args.request_id, tenant])).rows[0];
      if (!row) refuse('requirement_not_found');
      const g = (await c.query(`insert into ops.requirement_upload_grant
        (request_id,organization_tenant_id,deal_id,row_key,token_sha256,expires_at)
        values($1,$2,$3,$4,$5,now()+$6*interval '1 second') returning id, expires_at`,
      [row.id, tenant, row.deal_id, row.row_key, args.token_sha256, args.expires_in_seconds])).rows[0];
      return { request_id: row.id, deal_id: row.deal_id, grant_id: g.id, expires_at: g.expires_at,
        upload_path: `/requirement-upload/${tenant}/${row.deal_id}/${row.row_key}/${g.id}`,
        method: 'POST', mime: 'application/pdf', max_bytes: 1048576 };
    }),
    'revoke-requirement-upload': write('revoke-requirement-upload', { grant_id: uuid }, ['grant_id'],
      'Revoke one row-bound upload grant. Already revoked grants remain revoked. Revocation does not erase received documents.',
      async (c, _actor, args, tenant) => {
        const g = (await c.query(`update ops.requirement_upload_grant set revoked_at=coalesce(revoked_at,now())
          where id=$1 and organization_tenant_id=$2 returning request_id,deal_id,id as grant_id`, [args.grant_id, tenant])).rows[0];
        if (!g) refuse('upload_grant_not_found');
        return g;
      }),
    'list-requirement-requests': {
      write: false,
      description: 'Read row-bound document requests and quarantined receipt metadata for a deal. Returns no upload token or object URL. Receipt is distinct from satisfaction; this first slice cannot satisfy rows.',
      inputSchema: { type: 'object', additionalProperties: false, properties: { deal: { type: 'string' } }, required: ['deal'] },
      handler: async (c, actor, args) => {
        const subject = await resolveSubject(c, args.deal);
        if (subject.type !== 'deal') refuse('not_a_deal');
        return { ok: true, requests: (await c.query(`select * from v_requirement_request
          where organization_tenant_id=$1 and deal_id=$2 order by due_on,row_key`, [organizationTenantForActor(actor), subject.id])).rows };
      },
    },
  };
}
