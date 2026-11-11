-- Explicit client needs stay owned by their deal, separate from vendor scores.
create table public.deal_client_need (
 id uuid primary key default gen_random_uuid(),
 deal_id uuid not null references public.deal(id),
 vendor_id uuid not null references public.vendor(id),
 description text not null check (length(btrim(description)) between 1 and 2000),
 applicability text not null check (length(btrim(applicability)) between 1 and 2000),
 source_ref text not null check (length(btrim(source_ref)) between 1 and 2000),
 source_revision text not null check (length(btrim(source_revision)) between 1 and 200),
 observed_at timestamptz not null,
 fresh_until timestamptz not null check (fresh_until > observed_at),
 restriction text not null check (restriction in ('allowed','restricted')),
 withdrawn boolean not null default false,
 version integer not null default 1 check (version > 0),
 approved_revision integer,
 approved_client_id uuid references public.client(id),
 approved_by uuid references public.actor(id),
 approved_at timestamptz,
 created_by uuid not null references public.actor(id),
 updated_by uuid not null references public.actor(id),
 updated_at timestamptz not null default clock_timestamp(),
 check ((approved_revision is null and approved_client_id is null and approved_by is null and approved_at is null)
     or (approved_revision is not null and approved_revision=version and approved_client_id is not null and approved_by is not null and approved_at is not null))
);
create index deal_client_need_vendor on public.deal_client_need(vendor_id,id);
create index deal_client_need_deal on public.deal_client_need(deal_id,id);
create function public.revise_deal_client_need() returns trigger language plpgsql as $$
begin
 if row(new.deal_id,new.vendor_id,new.description,new.applicability,new.source_ref,new.source_revision,
        new.observed_at,new.fresh_until,new.restriction,new.withdrawn)
    is distinct from row(old.deal_id,old.vendor_id,old.description,old.applicability,old.source_ref,old.source_revision,
        old.observed_at,old.fresh_until,old.restriction,old.withdrawn) then
  new.version := old.version+1;
  new.approved_revision := null; new.approved_client_id := null;
  new.approved_by := null; new.approved_at := null;
 end if;
 new.updated_at := clock_timestamp();
 return new;
end $$;
create trigger deal_client_need_revision before update on public.deal_client_need
 for each row execute function public.revise_deal_client_need();
grant select on public.deal_client_need to carr_reader;
grant select,insert,update on public.deal_client_need to carr_writer;
