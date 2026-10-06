-- Exact referral attribution: one recorded relationship may send several deals.
-- Existing graph edges never imply that every deal for a practice was referred.
-- Each row keeps the referrer and date accepted when that deal was attached, so
-- a later broker or date backfill on the shared relationship never rewrites the
-- attribution of a deal recorded before it.
create table public.party_link_deal (
  link_id uuid not null references public.party_link(id),
  deal_id uuid not null references public.deal(id),
  referred_by uuid not null references public.party(id),
  occurred_on date,
  note text not null check(length(trim(note)) > 0),
  created_at timestamptz not null default now(),
  created_by uuid not null references public.actor(id),
  primary key(link_id,deal_id)
);
grant select on table public.party_link_deal to carr_reader;
grant select,insert on table public.party_link_deal to carr_writer;
grant select(id,party_id,registry_ref,segment,owner_id,notes,suppressed,stage) on table public.lead to carr_reader;

create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.party_link_deal for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.party_link_deal for each statement execute function ops.scac_reference_monitor_guard();
