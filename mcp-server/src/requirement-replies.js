// Email receipt proposals retain stored attachment identities and require partner review.
import { ToolError } from './tool-error.ts';
import { organizationTenantForActor } from './identity.js';
import { sha256 } from './sha256.ts';

const uuid = { type: 'string', format: 'uuid' };
const refuse = error => { throw new ToolError({ error }); };
const normalize = text => (text ?? '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

export async function recordRequirementReply(c, actor, activityId, attachmentId, dealId) {
  const source = (await c.query(`select a.id from activity a join deal d on d.id=$2
    where a.id=$1 and a.kind='email_in' and (a.deal_id=$2 or (a.deal_id is null and a.client_id=d.client_id))`,
  [activityId, dealId])).rows[0];
  if (!source) refuse('requirement_reply_source_invalid');
  await c.query(`insert into ops.requirement_reply_proposal
    (organization_tenant_id,deal_id,activity_id,attachment_id,created_by) values($1,$2,$3,$4,$5)
    on conflict(organization_tenant_id,activity_id,attachment_id) do nothing`,
  [organizationTenantForActor(actor), dealId, activityId, attachmentId, actor.id]);
}

async function preview(c, row) {
  const source = (await c.query(`select a.id,a.version,a.kind,a.email_context,a.detail,a.occurred_at,
    f.filename,f.sha256,f.bytes from activity a join deal d on d.id=$3
    join attachment f on f.id=$2 and f.subject_type='deal' and f.subject_id=d.id and f.deleted_at is null
    where a.id=$1 and a.kind='email_in' and (a.deal_id=d.id or (a.deal_id is null and a.client_id=d.client_id))`,
  [row.activity_id, row.attachment_id, row.deal_id])).rows[0];
  if (!source) return null;
  const sender = source.email_context?.sender?.email?.trim().toLowerCase();
  if (!sender) return null;
  const participant = (await c.query(`select 1 from deal_participant dp join party p on p.id=dp.party_id
    where dp.deal_id=$1 and dp.to_at is null and p.deleted_at is null and p.merged_into is null
    and $2=any(regexp_split_to_array(lower(coalesce(p.email,'')),'[\\s<>,;]+')) limit 1`, [row.deal_id, sender])).rows.length > 0;
  const requests = (await c.query(`select r.id as request_id,r.row_key,r.document_type,r.recipient_email,
    r.thread_activity_id,t.email_context as thread_context,t.version as thread_version,t.occurred_at as thread_time
    from v_requirement_request r left join activity t on t.id=r.thread_activity_id and t.deal_id=r.deal_id
    where r.organization_tenant_id=$1 and r.deal_id=$2 and r.status='open' order by r.id`,
  [row.organization_tenant_id, row.deal_id])).rows;
  const text = ` ${normalize(`${source.email_context?.subject ?? ''} ${source.detail ?? ''}`)} `;
  const candidates = requests.filter(r => {
    if (!participant && sender !== r.recipient_email) return false;
    const thread = source.email_context?.conversation_id;
    return (thread && thread === r.thread_context?.conversation_id && new Date(r.thread_time) < new Date(source.occurred_at)) ||
      (normalize(r.document_type) && text.includes(` ${normalize(r.document_type)} `));
  });
  if (!candidates.length) return null;
  const shown = { id: row.id, version: row.version, status: row.status, deal_id: row.deal_id,
    activity_id: row.activity_id, attachment_id: row.attachment_id, filename: source.filename,
    sender, subject: source.email_context?.subject ?? null, evidence_quote: source.detail,
    evidence_quote_untrusted: true, requires_confirmation: true,
    candidates: candidates.map(r => ({request_id:r.request_id,row_key:r.row_key,document_type:r.document_type})) };
  return { ...shown, proposal_digest: 'sha256:' + sha256(JSON.stringify({shown, source, candidates})) };
}

export function requirementReplyTools({withEnvelope, writeEvent, resolveSubject}) {
  const partner = actor => {
    if (actor.human !== true || !['joe','dell'].includes(actor.slug)) refuse('partner_confirmation_required');
  };
  const resolve = (name, confirm) => ({
    write: true, humanOnly: true, humanCallerOnly: true,
    description: confirm ? 'A partner confirms one candidate from a stored email attachment proposal with its current version and digest. Fulfills the open request with the existing attachment as evidence. Never satisfies a checklist row or sends mail.'
      : 'A partner rejects an email document reply proposal at its displayed version with a reason. Leaves the request open and retains the proposal so intake cannot recreate it.',
    inputSchema: {type:'object',additionalProperties:false,
      required:['idempotency_key','proposal_id','base_version',...(confirm ? ['request_id','proposal_digest'] : ['reason'])],
      properties:{idempotency_key:{type:'string',minLength:1},proposal_id:uuid,base_version:{type:'integer',minimum:1},
        ...(confirm ? {request_id:uuid,proposal_digest:{type:'string',pattern:'^sha256:[a-f0-9]{64}$'}}
          : {reason:{type:'string',minLength:1,maxLength:500}})}},
    handler:(c,actor,args) => withEnvelope(c,actor,name,args,async () => {
      partner(actor);
      const row = (await c.query(`select * from ops.requirement_reply_proposal
        where id=$1 and organization_tenant_id=$2 for update`,[args.proposal_id,organizationTenantForActor(actor)])).rows[0];
      if (!row || row.status !== 'pending') refuse('requirement_reply_not_pending');
      if (row.version !== args.base_version) refuse('version_conflict');
      if (confirm) {
        const request = (await c.query(`select id from ops.requirement_request
          where id=$1 and organization_tenant_id=$2 and deal_id=$3 and fulfilled_at is null for update`,
        [args.request_id,row.organization_tenant_id,row.deal_id])).rows[0];
        if (!request) refuse('requirement_not_open');
        await c.query('select id from activity where id=$1 for share',[row.activity_id]);
        await c.query('select id from attachment where id=$1 for share',[row.attachment_id]);
        const current = await preview(c,row);
        if (!current || current.proposal_digest !== args.proposal_digest ||
            !current.candidates.some(r => r.request_id === args.request_id)) refuse('requirement_reply_stale');
        await c.query(`update ops.requirement_request set fulfilled_at=now(),fulfilled_by=$2,evidence_attachment_id=$3
          where id=$1`,[args.request_id,actor.id,row.attachment_id]);
      } else if (!args.reason?.trim()) refuse('requirement_reply_reason_required');
      const status = confirm ? 'confirmed' : 'rejected';
      await c.query(`update ops.requirement_reply_proposal set status=$2,version=version+1,request_id=$3,
        resolved_by=$4,resolved_at=now(),reason=$5 where id=$1`,
      [row.id,status,args.request_id ?? null,actor.id,args.reason?.trim() ?? null]);
      await writeEvent(c,actor,name,'deal',row.deal_id,{new:{proposal_id:row.id,status,
        request_id:args.request_id ?? null,activity_id:row.activity_id,attachment_id:row.attachment_id,reason:args.reason?.trim() ?? null},
      cause:'human_stated',idempotency_key:args.idempotency_key});
      return {ok:true,proposal_id:row.id,status,version:row.version+1,
        ...(confirm ? {request_id:args.request_id,request_status:'fulfilled',evidence_attachment_id:row.attachment_id} : {})};
    }),
  });
  return {
    'list-requirement-reply-proposals': {
      write:false,
      description:'List pending document reply proposals for a deal with stored attachment metadata, source email, candidate open requests, version and confirmation digest. Multiple candidates require an explicit partner choice. No file bytes or URLs.',
      inputSchema:{type:'object',additionalProperties:false,required:['deal'],properties:{deal:{type:'string'}}},
      handler:async (c,actor,args) => {
        const deal = await resolveSubject(c,args.deal);
        if (deal.type !== 'deal') refuse('not_a_deal');
        const rows = (await c.query(`select * from v_requirement_reply_proposal
          where organization_tenant_id=$1 and deal_id=$2 and status='pending' order by created_at,id`,
        [organizationTenantForActor(actor),deal.id])).rows;
        const proposals = [];
        for (const row of rows) { const shown = await preview(c,row); if (shown) proposals.push(shown); }
        return {ok:true,schema_version:'requirement-reply-proposals.v1',proposals,send:false};
      },
    },
    'confirm-requirement-reply':resolve('confirm-requirement-reply',true),
    'reject-requirement-reply':resolve('reject-requirement-reply',false),
  };
}
