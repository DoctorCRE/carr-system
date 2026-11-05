-- rollback: forward-only; retain compensation and original evidence.
-- expand-contract: expand; existing activities default to uncorrected.
-- lock-review: activity nullable column and event partial indexes; runner timeouts apply.
alter table public.activity add column touch_correction_key uuid;
comment on column public.activity.touch_correction_key is 'Permanent compensation key. Excluded from contact-date readers; original activity remains in history.';
create unique index event_email_touch_correction_key on public.event(idempotency_key) where verb='correct-email-touch';
create unique index event_email_touch_correction_activity on public.event(subject_id) where verb='correct-email-touch';
create or replace function public.email_touch_application_immutable() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  if (old.verb='log-activity' and old.new_value ? 'email_application') or old.verb='correct-email-touch' then
    raise exception 'email_touch_application_immutable' using errcode='23514';
  end if;
  if tg_op='DELETE' then return old; end if;
  return new;
end $$;
create or replace view v_last_touch as
with contact as (
  select a.*
    from activity a
    join activity_kind k on k.slug = a.kind
   where a.touch_correction_key is null and (k.is_contact
      or (a.kind = 'note' and a.summary = 'last-touch stamp (imported)')
      or (a.kind = 'note' and a.summary like 'Last touch carried from%'))
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
