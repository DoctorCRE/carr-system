-- rollback: forward-only; retain deal files and remove callers before retiring storage.
-- lock-review: creates an empty table and index, no existing rows rewritten.
create table ops.deal_deliverable (
 id uuid primary key,
 organization_tenant_id text not null check(organization_tenant_id='carr-internal'),
 deal_id uuid not null references public.deal(id),
 filename text not null,
 mime text not null,
 bytes bigint not null check(bytes>0),
 sha256 text not null check(sha256 ~ '^[0-9a-f]{64}$'),
 object_key text not null unique,
 created_by text not null,
 created_at timestamptz not null default now(),
 expires_at timestamptz not null default now()+interval '24 hours',
 finalized_at timestamptz,
 state text not null default 'started' check(state in ('started','uploading','verified','finalized','expired')),
 visibility text not null default 'private' check(visibility in ('private','shared')),
 version integer not null default 0,
 check((state='finalized' and finalized_at is not null and version>=1)
   or (state<>'finalized' and finalized_at is null and version=0 and visibility='private'))
);
create index deal_deliverable_deal on ops.deal_deliverable(organization_tenant_id,deal_id,created_at,id);
revoke all on ops.deal_deliverable from public;
grant select,insert,update on ops.deal_deliverable to carr_writer;
grant select on ops.deal_deliverable to carr_reader;
