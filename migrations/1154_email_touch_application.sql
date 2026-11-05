-- rollback: forward-only; retain application proof with its activity lineage.
-- expand-contract: expand; existing log-activity callers remain unchanged.
-- lock-review: partial indexes scan event; runner lock and statement timeouts apply.
create unique index event_email_touch_original_key on public.event(idempotency_key)
where verb='log-activity' and new_value ? 'email_application';
create unique index event_email_touch_target on public.event(actor_id,
  (public.digest(new_value#>>'{email_application,binding,source_id}','sha256')),
  (public.digest(new_value#>>'{email_application,binding,source_version}','sha256')),
  (new_value#>>'{email_application,binding,target_type}'),
  (new_value#>>'{email_application,binding,target_id}'))
where verb='log-activity' and new_value ? 'email_application';

create function public.email_touch_application_immutable() returns trigger
language plpgsql set search_path=pg_catalog,pg_temp as $$
begin
  if old.verb='log-activity' and old.new_value ? 'email_application' then
    raise exception 'email_touch_application_immutable' using errcode='23514';
  end if;
  if tg_op='DELETE' then return old; end if;
  return new;
end $$;
revoke all on function public.email_touch_application_immutable() from public;
create trigger event_email_touch_immutable before update or delete on public.event
for each row execute function public.email_touch_application_immutable();
