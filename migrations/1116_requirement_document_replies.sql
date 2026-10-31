-- rollback: remove reply consumers before dropping additive proposal and fulfillment fields.
-- expand-contract: fulfillment records receipt evidence, never checklist satisfaction.
-- lock-review: additive nullable fields; new table foreign keys reference indexed identities.
alter table ops.requirement_request
  add column recipient_email text,
  add column thread_activity_id uuid references public.activity(id),
  add column fulfilled_at timestamptz,
  add column fulfilled_by uuid references public.actor(id),
  add column evidence_attachment_id uuid references public.attachment(id),
  add constraint requirement_fulfillment_complete check
    ((fulfilled_at is null and fulfilled_by is null and evidence_attachment_id is null) or
     (fulfilled_at is not null and fulfilled_by is not null and evidence_attachment_id is not null));
create table ops.requirement_reply_proposal (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null check (organization_tenant_id = 'carr-internal'),
  deal_id uuid not null references public.deal(id),
  activity_id uuid not null references public.activity(id),
  attachment_id uuid not null references public.attachment(id),
  created_by uuid not null references public.actor(id),
  status text not null default 'pending' check(status in ('pending','confirmed','rejected')),
  version integer not null default 1 check(version > 0),
  request_id uuid references ops.requirement_request(id),
  resolved_by uuid references public.actor(id),
  resolved_at timestamptz,
  reason text,
  created_at timestamptz not null default now(),
  unique(organization_tenant_id,activity_id,attachment_id),
  check ((status='pending' and resolved_by is null and resolved_at is null and request_id is null) or
    (status='rejected' and resolved_by is not null and resolved_at is not null and request_id is null) or
    (status='confirmed' and resolved_by is not null and resolved_at is not null and request_id is not null))
);
create index requirement_reply_pending on ops.requirement_reply_proposal(organization_tenant_id,deal_id,created_at,id) where status='pending';
revoke all on ops.requirement_reply_proposal from public,carr_reader;
grant select,insert,update on ops.requirement_reply_proposal to carr_writer;
grant update on ops.requirement_request to carr_writer;
create or replace view public.v_requirement_request as
select r.id,r.organization_tenant_id,r.deal_id,r.row_key,r.owner,r.due_on,r.document_type,r.source,false as satisfied,
  coalesce((select jsonb_agg(jsonb_build_object('id',u.id,'state',u.state,'received_at',u.received_at,
    'sha256',u.sha256,'bytes',u.bytes,'mime',u.mime) order by u.received_at,u.id)
    from ops.requirement_upload_receipt u where u.request_id=r.id),'[]'::jsonb) as receipts,
  case when r.fulfilled_at is null then 'open' else 'fulfilled' end as status,
  r.recipient_email,r.thread_activity_id,r.fulfilled_at,r.fulfilled_by,r.evidence_attachment_id
from ops.requirement_request r;
create view public.v_requirement_reply_proposal as select * from ops.requirement_reply_proposal;
grant select on public.v_requirement_reply_proposal to carr_reader,carr_writer;
do $contract$
declare contract jsonb;
begin
  contract := ops.scac_current_grant_contract();
  contract := jsonb_set(contract,'{relation_dml}',(contract->'relation_dml') ||
    $reply_grants$[{"ingress_key":"db-relation-acl:ops.requirement_request:carr_writer:update","ingress_kind":"db_relation_acl","relation":"ops.requirement_request","relation_kind":"r","grantee":"carr_writer","privilege":"update","grantable":false},{"ingress_key":"db-relation-acl:ops.requirement_reply_proposal:carr_writer:insert","ingress_kind":"db_relation_acl","relation":"ops.requirement_reply_proposal","relation_kind":"r","grantee":"carr_writer","privilege":"insert","grantable":false},{"ingress_key":"db-relation-acl:ops.requirement_reply_proposal:carr_writer:update","ingress_kind":"db_relation_acl","relation":"ops.requirement_reply_proposal","relation_kind":"r","grantee":"carr_writer","privilege":"update","grantable":false}]$reply_grants$::jsonb);
  execute format(
    'CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS %L',
    'SELECT ' || quote_literal(contract::text) || '::jsonb');
end $contract$;
