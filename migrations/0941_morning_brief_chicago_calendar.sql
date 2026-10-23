-- lock-review: Replace four read views in place; no business rows or existing grants change.
-- Slice D / F5: timestamp-derived days and current-day predicates use Chicago.
begin;

create or replace function public.carr_business_date(instant timestamptz default current_timestamp)
returns date language sql stable strict parallel safe
set search_path = pg_catalog
as $$ select (instant at time zone 'America/Chicago')::date $$;

comment on function public.carr_business_date(timestamptz) is
  'CARR calendar day in America/Chicago, independent of session timezone. Date-only facts stay dates.';
revoke all on function public.carr_business_date(timestamptz) from public;
grant execute on function public.carr_business_date(timestamptz)
  to carr_reader, carr_writer, carr_jobs, carr_exporter, carr_authority;

create or replace view v_today_triage as
  select 'next_action'::text as item_kind, na.id, na.subject_type, na.subject_id,
         owner.slug as owner, na.description as what, na.due_on,
         coalesce(r.display_name, r.org_name) as subject_name,
         r.ref as subject_ref,
         carr_business_days(na.due_on, public.carr_business_date()) as business_days_overdue
    from next_action na
    join actor owner on owner.id = na.owner_id
    left join v_ref_index r on r.subject_id = na.subject_id and r.subject_type = na.subject_type
   where na.status = 'open' and na.due_on is not null and na.due_on <= public.carr_business_date()
     and (na.hold_until is null or na.hold_until <= public.carr_business_date())
union all
  select 'post_call_action'::text, pca.id, 'deal'::text, pca.deal_id,
         owner.slug, pca.description, pca.due_on,
         coalesce(r.display_name, r.org_name), r.ref,
         carr_business_days(pca.due_on, public.carr_business_date())
    from capture_post_call_action pca
    join actor owner on owner.id = pca.owner_id
    left join v_ref_index r on r.subject_id = pca.deal_id and r.subject_type = 'deal'
   where pca.status = 'open' and pca.due_on is not null and pca.due_on <= public.carr_business_date()
union all
  select 'critical_date'::text, cd.id, 'deal'::text, cd.deal_id, null::text,
         cd.kind || coalesce(': ' || cd.note, ''), cd.due_on,
         coalesce(r.display_name, r.org_name), r.ref,
         carr_business_days(cd.due_on, public.carr_business_date())
    from critical_date cd
    left join v_ref_index r on r.subject_id = cd.deal_id and r.subject_type = 'deal'
   where cd.status = 'open' and cd.due_on <= (public.carr_business_date() + 14)
union all
  select 'ingest'::text, i.id, 'inbox'::text, i.id, null::text,
         coalesce(nullif(trim(i.payload->>'summary'), ''), nullif(trim(i.payload->>'title'), ''),
                  nullif(trim(i.payload->>'subject'), ''), i.source || ' item awaiting triage'),
         public.carr_business_date(i.received_at), nullif(trim(i.payload->>'organizer'), ''), null::text,
         carr_business_days(public.carr_business_date(i.received_at), public.carr_business_date())
    from ingest_inbox i where i.status = 'new';

create or replace view v_claim_card as
select cp.id                          as pool_id,
       cp.version                     as base_version,
       cp.source                      as lane,
       cp.name                        as display_name,
       cp.org_name,
       cp.vertical,
       cp.city,
       cp.county,
       cp.state,
       cp.segment,
       cp.segment_play,
       cp.score,
       cp.score_basis,
       cp.est_lease_event,
       cp.est_basis,
       cp.dup_tier,
       cp.dup_ref,
       cp.dup_basis,
       (cp.email is not null and cp.email <> '')
         or (cp.phone is not null and cp.phone <> '')      as has_channel,
       not ((cp.email is not null and cp.email <> '')
         or (cp.phone is not null and cp.phone <> ''))     as needs_contact,
       -- Passed windows remain claimable with an intentional negative age.
       (cp.est_lease_event - public.carr_business_date())                 as days_to_window,
       cp.created_at
  from candidate_pool cp
 where cp.status = 'pool'
   and not cp.dup_do_not_contact;

create or replace view v_renewal_decision_queue as
select p.name as display_name,
       org.name as org_name,
       c.vertical,
       p.city,
       null::text as county,
       p.state,
       l.expiration_on as est_lease_event,
       't1'::text as tier_status,
       'clear'::text as flag_status,
       ((p.email is not null and p.email<>'') or
        (p.phone is not null and p.phone<>'')) as has_channel,
       count(*) over(partition by owner.slug)::integer as decision_count,
       greatest(l.created_at,l.updated_at) as source_observed_at,
       'ready'::text as freshness_state,
       owner.slug as owner_slug
  from lease l
  join deal d on d.id=l.deal_id and d.client_id=l.client_id
  join client c on c.id=l.client_id
  join party p on p.id=c.party_id and p.merged_into is null and p.deleted_at is null
  left join party org on org.id=p.org_id and org.merged_into is null and org.deleted_at is null
  join actor owner on owner.id=coalesce(
    (select dp.actor_id from deal_participant dp
      where dp.deal_id=d.id and dp.role='lead' and dp.to_at is null limit 1),
    c.created_by) and owner.slug in ('joe','dell')
 where l.status='current'
   and l.expiration_on between public.carr_business_date()-90 and public.carr_business_date()+548
   and l.evidence_kind in ('executed_lease','lease_amendment','lease_abstract')
   and nullif(btrim(l.evidence_ref),'') is not null
   and nullif(btrim(l.source),'') is not null;

create or replace view v_last_touch as
with contact as (
  select a.*
    from activity a
    join activity_kind k on k.slug = a.kind
   where k.is_contact
      or (a.kind = 'note' and a.summary = 'last-touch stamp (imported)')
      or (a.kind = 'note' and a.summary like 'Last touch carried from%')
),
-- a deal's touches: its own rows, PLUS its client's rows
deal_contact as (
  select d.id as deal_id, c.occurred_at
    from deal d join contact c on c.deal_id = d.id
  union all
  select d.id, c.occurred_at
    from deal d
    join client cl on cl.id = d.client_id
    join contact c on c.client_id = cl.id
)
select 'deal'::text as subject_type, deal_id as subject_id, public.carr_business_date(max(occurred_at)) as last_touch
  from deal_contact group by deal_id
union all
select 'client'::text, client_id, public.carr_business_date(max(occurred_at))
  from contact where client_id is not null group by client_id
union all
select 'lead'::text, lead_id, public.carr_business_date(max(occurred_at))
  from contact where lead_id is not null group by lead_id
union all
select 'vendor'::text, vendor_id, public.carr_business_date(max(occurred_at))
  from contact where vendor_id is not null group by vendor_id;

commit;
