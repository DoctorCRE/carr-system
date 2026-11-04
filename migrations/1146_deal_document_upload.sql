-- Temporary partner intake only. No canonical attachment mutation.
-- rollback: forward-only successor; preserve charged temporary objects.
-- lock-review: new tables and indexes only.
create table ops.deal_document_upload_quota (
 singleton boolean primary key default true check(singleton),
 reserved_bytes bigint not null default 0 check(reserved_bytes between 0 and 104857600),
 failures integer not null default 0,
 blocked_until timestamptz
);
insert into ops.deal_document_upload_quota(singleton) values(true);
create table ops.deal_document_upload (
 id uuid primary key default gen_random_uuid(),
 organization_tenant_id text not null check(organization_tenant_id='carr-internal'),
 actor_slug text not null references public.actor(slug),
 session_digest text not null check(session_digest ~ '^[0-9a-f]{64}$'),
 deal_id uuid not null references public.deal(id),
 action_id uuid not null,
 idempotency_key uuid not null,
 filename text not null,
 declared_mime text not null,
 bytes integer not null check(bytes between 1 and 26214400),
 sha256 text not null check(sha256 ~ '^[0-9a-f]{64}$'),
 object_key text not null unique,
 state text not null default 'reserved' check(state in ('reserved','receiving','staged','rejected','retryable_unknown','cancelled')),
 cancel_key uuid,
 charged boolean not null default true,
 created_at timestamptz not null default now(),
 expires_at timestamptz not null default now()+interval '1 day',
 unique(organization_tenant_id,actor_slug,idempotency_key),
 unique(organization_tenant_id,actor_slug,cancel_key)
);
create index deal_document_upload_action on ops.deal_document_upload(actor_slug,action_id);
create index deal_document_upload_rate on ops.deal_document_upload(actor_slug,created_at);
revoke all on ops.deal_document_upload,ops.deal_document_upload_quota from public,carr_reader,carr_jobs;
grant select,insert,update on ops.deal_document_upload,ops.deal_document_upload_quota to carr_writer;

grant select on ops.deal_document_upload to carr_reader;

do $upload_grants$
declare contract jsonb := ops.scac_current_grant_contract(); relation_name text; privilege_name text;
begin
  foreach relation_name in array array['ops.deal_document_upload','ops.deal_document_upload_quota'] loop
    foreach privilege_name in array array['insert','update'] loop
      contract:=jsonb_set(contract,'{relation_dml}',coalesce(contract->'relation_dml','[]'::jsonb)||jsonb_build_array(jsonb_build_object(
        'ingress_key','db-relation-acl:'||relation_name||':carr_writer:'||privilege_name,
        'ingress_kind','db_relation_acl','relation',relation_name,'relation_kind','r',
        'grantee','carr_writer','privilege',privilege_name,'grantable',false)));
    end loop;
  end loop;
  execute format('create or replace function ops.scac_current_grant_contract() returns jsonb language sql immutable set search_path=pg_catalog as %L','select '||quote_literal(contract::text)||'::jsonb');
end $upload_grants$;
