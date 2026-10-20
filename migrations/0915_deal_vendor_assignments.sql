-- rollback: remove the views and assignment table only after exporting attributed assignment history
-- lock-review: new table and views only; no existing rows rewritten
create table public.deal_vendor_assignment (
  deal_id uuid not null references public.deal(id),
  party_id uuid not null references public.party(id),
  role text not null check (role ~ '^[a-z][a-z0-9_]{1,49}$' and role not in ('vendor','worked','referred','other','unknown')),
  company_party_id uuid not null references public.party(id),
  contact_party_id uuid not null references public.party(id),
  active boolean not null default true,
  note text,
  updated_by uuid not null references public.actor(id),
  updated_at timestamptz not null default now(),
  primary key (deal_id,party_id,role),
  unique (deal_id,role,contact_party_id)
);

create view public.v_deal_room_vendor as
select a.deal_id,a.party_id,a.role,
  jsonb_build_object('id',company.id,'name',company.name) as company,
  jsonb_build_object('id',contact.id,'name',contact.name,'email',contact.email,'phone',contact.phone) as contact,
  a.note,to_jsonb(a.updated_at)#>>'{}' as updated_at
from public.deal_vendor_assignment a
join public.party p on p.id=a.party_id and p.merged_into is null and p.deleted_at is null
join public.party company on company.id=a.company_party_id and company.merged_into is null and company.deleted_at is null
join public.party contact on contact.id=a.contact_party_id and contact.merged_into is null and contact.deleted_at is null
where a.active and exists (select 1 from public.vendor v where v.party_id=a.party_id and v.merged_into is null);

grant select on public.v_deal_room_vendor to carr_reader,carr_writer;
grant select,insert,update on public.deal_vendor_assignment to carr_writer;

create view public.v_vendor_assignment_context as
select v.id,v.vendor_ref as ref,v.version as base_version,v.party_id,
  case when company.id is not null then jsonb_build_object('id',company.id,'name',company.name) end as company,
  coalesce((select jsonb_agg(jsonb_build_object('id',contact.id,'name',contact.name) order by contact.name,contact.id)
    from public.party contact where contact.kind='person' and contact.merged_into is null and contact.deleted_at is null
    and ((p.kind='person' and contact.id=p.id) or (p.kind='org' and contact.org_id=p.id))),'[]'::jsonb) as contacts
from public.vendor v join public.party p on p.id=v.party_id
left join public.party company on company.id=case when p.kind='org' then p.id else p.org_id end
  and company.kind='org' and company.merged_into is null and company.deleted_at is null
where v.merged_into is null and p.merged_into is null and p.deleted_at is null;
grant select on public.v_vendor_assignment_context to carr_reader,carr_writer;

create view public.v_vendor_duplicate_report as
with live as (
  select v.id,v.party_id,p.org_id,
    nullif(lower(btrim(p.email)),'') as email,
    nullif(lower(regexp_replace(btrim(p.name),'\s+',' ','g')),'') as name
  from public.vendor v join public.party p on p.id=v.party_id
  where v.merged_into is null and p.merged_into is null and p.deleted_at is null
), same_party as (
  select count(*) as n from live group by party_id having count(*)>1
), email_match as (
  select email from live where email is not null group by email having count(distinct party_id)>1
), name_company_match as (
  select name,org_id from live where name is not null and org_id is not null
  group by name,org_id having count(distinct party_id)>1
)
select (select count(*)::int from live) as live_vendor_rows,
  (select count(*)::int from same_party) as same_party_groups,
  (select coalesce(sum(n-1),0)::int from same_party) as extra_vendor_rows,
  (select count(*)::int from email_match) as email_match_groups,
  (select count(*)::int from name_company_match) as name_company_match_groups;
grant select on public.v_vendor_duplicate_report to carr_reader,carr_writer;
