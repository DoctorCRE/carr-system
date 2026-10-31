-- rollback: remove consumers before dropping these additive, empty-by-default tables.
-- expand-contract: requests and quarantined receipts never overwrite milestone facts.
-- lock-review: new tables only; foreign keys reference indexed deal and actor identities.
create table ops.requirement_request (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null check (organization_tenant_id = 'carr-internal'),
  deal_id uuid not null references public.deal(id),
  row_key text not null check (row_key ~ '^[a-z][a-z0-9_]{0,79}$'),
  owner text not null check (length(btrim(owner)) between 1 and 100),
  due_on date not null,
  document_type text not null,
  source jsonb not null check (jsonb_typeof(source) = 'object'),
  created_by uuid not null references public.actor(id),
  created_at timestamptz not null default now(),
  unique (organization_tenant_id, deal_id, row_key),
  unique (id, organization_tenant_id, deal_id, row_key)
);
create table ops.requirement_upload_grant (
  id uuid primary key default gen_random_uuid(),
  request_id uuid not null,
  organization_tenant_id text not null,
  deal_id uuid not null,
  row_key text not null,
  unique (id,request_id),
  action text not null default 'upload' check (action = 'upload'),
  token_sha256 text not null unique check (token_sha256 ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz not null check (expires_at > created_at and expires_at <= created_at + interval '1 day'),
  revoked_at timestamptz,
  consumed_at timestamptz,
  created_at timestamptz not null default now(),
  foreign key (request_id, organization_tenant_id, deal_id, row_key)
    references ops.requirement_request(id, organization_tenant_id, deal_id, row_key)
);
create table ops.requirement_upload_receipt (
  id uuid primary key default gen_random_uuid(),
  grant_id uuid not null unique,
  request_id uuid not null references ops.requirement_request(id),
  r2_key text not null unique,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  bytes integer not null check (bytes between 1 and 1048576),
  mime text not null check (mime = 'application/pdf'),
  state text not null default 'quarantined' check (state = 'quarantined'),
  foreign key (grant_id,request_id) references ops.requirement_upload_grant(id,request_id),
  received_at timestamptz not null default now()
);
create index requirement_upload_receipt_request on ops.requirement_upload_receipt(request_id);
create table ops.requirement_upload_visitor (
  visitor_hash text primary key check (visitor_hash ~ '^[0-9a-f]{64}$'),
  window_start timestamptz not null,
  writes integer not null check (writes between 5 and 20)
);
create table ops.requirement_upload_quota (
  singleton boolean primary key default true check (singleton),
  reserved_bytes bigint not null default 0 check (reserved_bytes between 0 and 104857600)
);
revoke all on ops.requirement_upload_quota from public, carr_reader;
grant select, insert, update on ops.requirement_upload_quota to carr_writer;
revoke all on ops.requirement_request, ops.requirement_upload_grant,
  ops.requirement_upload_receipt, ops.requirement_upload_visitor from public, carr_reader;
grant select, insert on ops.requirement_request, ops.requirement_upload_receipt to carr_writer;
grant select, insert, update on ops.requirement_upload_grant, ops.requirement_upload_visitor to carr_writer;
grant select on ops.requirement_request to carr_authority;
grant select,insert,update on ops.requirement_upload_grant to carr_authority;
-- Read only the public request projection; upload secrets and visitor identities stay private.
create view public.v_requirement_request as
select r.id, r.organization_tenant_id, r.deal_id, r.row_key, r.owner, r.due_on,
  r.document_type, r.source, false as satisfied,
  coalesce((select jsonb_agg(jsonb_build_object('id',u.id,'state',u.state,'received_at',u.received_at,
    'sha256',u.sha256,'bytes',u.bytes,'mime',u.mime) order by u.received_at,u.id)
    from ops.requirement_upload_receipt u where u.request_id=r.id),'[]'::jsonb) as receipts
from ops.requirement_request r;
grant select on public.v_requirement_request to carr_reader, carr_writer;
do $grants$
declare contract jsonb := ops.scac_current_grant_contract(); entry record;
begin
  for entry in select * from (values
    ('ops.requirement_upload_quota','update','carr_writer'),
    ('ops.requirement_request','insert','carr_writer'),('ops.requirement_upload_receipt','insert','carr_writer'),
    ('ops.requirement_upload_grant','insert','carr_writer'),('ops.requirement_upload_grant','update','carr_writer'),
    ('ops.requirement_upload_visitor','insert','carr_writer'),('ops.requirement_upload_visitor','update','carr_writer'),
    ('ops.requirement_upload_grant','insert','carr_authority'),('ops.requirement_upload_grant','update','carr_authority'),
    ('ops.requirement_upload_quota','insert','carr_writer')) as changes(relation,privilege,grantee)
  loop
    contract := jsonb_set(contract,'{relation_dml}',coalesce(contract->'relation_dml','[]'::jsonb) || jsonb_build_array(jsonb_build_object(
      'ingress_key','db-relation-acl:'||entry.relation||':'||entry.grantee||':'||entry.privilege,
      'ingress_kind','db_relation_acl','relation',entry.relation,'relation_kind','r',
      'grantee',entry.grantee,'privilege',entry.privilege,'grantable',false)));
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb '
    'language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $grants$;
