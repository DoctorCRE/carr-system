-- 0738: tenant-scoped healthcare CRE industry events.
-- This is a record contract for conferences, association meetings, trade shows,
-- and networking events. It stores event facts and attendance intent only; it
-- does not register attendance, send invitations, or wire the DoctorCRE app.

create table public.industry_event (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null check (length(btrim(organization_tenant_id)) > 0),
  title text not null check (length(btrim(title)) > 0 and length(title) <= 500),
  organizer text not null check (length(btrim(organizer)) > 0 and length(organizer) <= 500),
  kind text not null check (kind in ('conference','association_meeting','trade_show','networking')),
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  location text check (location is null or length(location) <= 500),
  is_virtual boolean not null default false,
  url text check (url is null or url ~ '^https?://'),
  relevance_note text check (relevance_note is null or length(relevance_note) <= 2000),
  attendance_intent text not null default 'considering'
    check (attendance_intent in ('considering','plan_to_attend','not_attending')),
  owner_partner text not null check (owner_partner in ('joe','dell')),
  status text not null default 'planned'
    check (status in ('planned','attended','skipped','cancelled')),
  source text not null check (length(btrim(source)) > 0 and length(source) <= 2000),
  version bigint not null default 1 check (version > 0),
  created_by_actor_id uuid not null references public.actor(id),
  updated_by_actor_id uuid not null references public.actor(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (ends_at > starts_at)
);

create index industry_event_tenant_start_idx
  on public.industry_event (organization_tenant_id, starts_at, id);

revoke all on public.industry_event from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on public.industry_event to carr_reader;
grant select, insert, update on public.industry_event to carr_writer;

create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.industry_event for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.industry_event for each statement execute function ops.scac_reference_monitor_guard();
