-- rollback: remove checklist filename consumers before restoring the 1116 request view.
-- lock-review: appends a nullable field to the existing request view; no table rewrite.
-- expand-contract: readers receive file names through the existing request projection.
create or replace view public.v_requirement_request as
select r.id,r.organization_tenant_id,r.deal_id,r.row_key,r.owner,r.due_on,r.document_type,r.source,false as satisfied,
  coalesce((select jsonb_agg(jsonb_build_object('id',u.id,'state',u.state,'received_at',u.received_at,
    'sha256',u.sha256,'bytes',u.bytes,'mime',u.mime) order by u.received_at,u.id)
    from ops.requirement_upload_receipt u where u.request_id=r.id),'[]'::jsonb) as receipts,
  case when r.fulfilled_at is null then 'open' else 'fulfilled' end as status,
  r.recipient_email,r.thread_activity_id,r.fulfilled_at,r.fulfilled_by,r.evidence_attachment_id,
  (select f.filename from public.attachment f where f.id=r.evidence_attachment_id
    and f.subject_type='deal' and f.subject_id=r.deal_id and f.deleted_at is null) as evidence_filename
from ops.requirement_request r;
