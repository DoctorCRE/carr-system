-- rollback: forward-only; drop these empty tables only before any reviewed registry is loaded.
-- expand-contract: expand; new reviewed registry, no legacy vendor updates or inferred memberships.
-- lock-review: new empty tables and indexes only; foreign keys reference existing indexed identities.
create table public.vendor_market (
  slug text primary key check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' and length(slug)<=100),
  name text not null check (length(btrim(name)) between 1 and 200),
  kind text not null check (kind in ('market','regional')),
  boundary text not null check (length(btrim(boundary)) between 1 and 4000),
  source text not null check (length(btrim(source)) between 1 and 2000),
  reviewed_by uuid not null references public.actor(id),
  reviewed_at timestamptz not null,
  unique(slug,kind)
);
create table public.vendor_market_key (
  key text primary key check (key=lower(btrim(key)) and length(key) between 1 and 200),
  market_slug text not null references public.vendor_market(slug)
);
create index vendor_market_key_market on public.vendor_market_key(market_slug);
create table public.vendor_market_membership (
  market_slug text not null references public.vendor_market(slug),
  vendor_id uuid not null references public.vendor(id),
  office_presence boolean not null,
  service_coverage text check (length(btrim(service_coverage)) between 1 and 4000),
  source text not null check (length(btrim(source)) between 1 and 2000),
  reviewed_by uuid not null references public.actor(id),
  reviewed_at timestamptz not null,
  primary key (market_slug,vendor_id),
  check (office_presence or service_coverage is not null)
);
create index vendor_market_membership_vendor on public.vendor_market_membership(vendor_id);
create table public.vendor_market_regional_reference (
  market_slug text not null references public.vendor_market(slug),
  regional_slug text not null references public.vendor_market(slug),
  note text not null check (length(btrim(note)) between 1 and 4000),
  source text not null check (length(btrim(source)) between 1 and 2000),
  reviewed_by uuid not null references public.actor(id),
  reviewed_at timestamptz not null,
  market_kind text not null default 'market' check (market_kind='market'),
  regional_kind text not null default 'regional' check (regional_kind='regional'),
  foreign key(market_slug,market_kind) references public.vendor_market(slug,kind),
  foreign key(regional_slug,regional_kind) references public.vendor_market(slug,kind),
  primary key(market_slug,regional_slug),
  check (market_slug<>regional_slug)
);
revoke all on public.vendor_market, public.vendor_market_key, public.vendor_market_membership,
  public.vendor_market_regional_reference from public, carr_reader, carr_writer, carr_jobs, carr_authority, dot_reader;
grant select on public.vendor_market, public.vendor_market_key, public.vendor_market_membership,
  public.vendor_market_regional_reference to carr_reader, carr_writer;

create function public.vendor_market_canonical_key() returns trigger
language plpgsql set search_path=pg_catalog,public as $$
begin
  insert into public.vendor_market_key(key,market_slug) values(new.slug,new.slug);
  return new;
end $$;
create trigger vendor_market_canonical_key after insert on public.vendor_market
for each row execute function public.vendor_market_canonical_key();
