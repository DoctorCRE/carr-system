-- rollback: remove the fulfillment trigger and restore notification_visible from 1114; retain terminal delivery receipts.
-- lock-review: extends the delivery state check and serializes fulfillment with checklist pings by deal.
-- expand-contract: suppressed_fulfilled is a terminal delivery state; historical delivery evidence is retained.
alter table ops.notification_delivery drop constraint notification_delivery_state_check;
alter table ops.notification_delivery add constraint notification_delivery_state_check
  check (state in ('pending','delivered','suppressed_quiet_hours','failed','held_until_morning','suppressed_fulfilled'));

create or replace function ops.notification_visible(p_notification uuid)
returns boolean language sql stable set search_path=pg_catalog,ops as $$
  select not exists (select 1 from ops.notification_delivery h
    where h.notification_id=p_notification and
      (h.state='suppressed_fulfilled' or (h.state='held_until_morning' and h.held_until>now())))
$$;

create function ops.suppress_fulfilled_checklist_reminders()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops,public,pg_temp as $$
begin
  perform pg_advisory_xact_lock(hashtextextended('deal-checklist:' || new.deal_id, 0));
  update ops.notification_delivery d
    set state='suppressed_fulfilled',settled_at=now(),held_until=null
    from ops.notification n
    where d.notification_id=n.id and d.state in ('pending','held_until_morning')
      and n.subject_type='deal' and n.subject_ref=new.deal_id::text
      and starts_with(n.dedupe_key,'deal-checklist:' || new.deal_id || ':' || new.row_key || ':');
  return new;
end $$;
revoke all on function ops.suppress_fulfilled_checklist_reminders() from public;
create trigger requirement_fulfilled_reminders before update of fulfilled_at on ops.requirement_request
  for each row when (old.fulfilled_at is null and new.fulfilled_at is not null)
  execute function ops.suppress_fulfilled_checklist_reminders();

update ops.notification_delivery d
  set state='suppressed_fulfilled',settled_at=now(),held_until=null
  from ops.notification n,ops.requirement_request r
  where d.notification_id=n.id and d.state in ('pending','held_until_morning')
    and r.fulfilled_at is not null and n.subject_type='deal' and n.subject_ref=r.deal_id::text
    and starts_with(n.dedupe_key,'deal-checklist:' || r.deal_id || ':' || r.row_key || ':');
