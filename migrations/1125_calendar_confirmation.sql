-- rollback: forward-only; retain recorded meeting confirmations.
-- expand-contract: nullable key keeps existing activity writes compatible.
-- lock-review: adding a nullable column avoids a table rewrite; index creation scans activity.
alter table public.activity add column calendar_event_key text;
create unique index activity_calendar_event_deal_uidx on public.activity (calendar_event_key, deal_id)
  where calendar_event_key is not null;
