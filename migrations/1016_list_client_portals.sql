-- rollback: drop public.v_client_portal_list and the timestamp trigger and column.
-- expand-contract: expand — add portal metadata and a safe partner-facing projection.
-- lock-review: adds one timestamp column and trigger to the small portal table.
alter table ops.client_portal add column updated_at timestamptz;
alter table ops.client_portal alter column updated_at set default now();

create function ops.touch_client_portal() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  new.updated_at := clock_timestamp();
  return new;
end $$;
create trigger client_portal_updated before update on ops.client_portal
for each row execute function ops.touch_client_portal();

create view public.v_client_portal_list as
select p.slug, p.client_id, party.name as client_name, p.deal_id, d.name as deal_name,
       'https://share.doctorcre.com/' || p.slug as client_portal_url,
       case when p.disabled_at is null then 'active' else 'disabled' end as status,
       p.created_at as registered_at, p.updated_at,
       (p.failed_attempts >= 5 and p.attempt_window_started_at > now() - interval '15 minutes') as locked
from ops.client_portal p
join public.deal d on d.id=p.deal_id
join public.client cl on cl.id=p.client_id
join public.party party on party.id=cl.party_id;
revoke all on public.v_client_portal_list from public, dot_reader;
grant select on public.v_client_portal_list to carr_reader, carr_writer;
