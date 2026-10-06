-- 0463 / SIEP finding elimination: a retired rule must not leave a live delivery row.
-- Source/test implementation only. Production application remains Joe-gated.

create or replace function ops.retired_rule_delivery_cleanup()
returns trigger
language plpgsql security definer
set search_path=pg_catalog,public,ops
as $fn$
begin
  if old.status in ('proposed','active') and new.status='retired' then
    delete from ops.rule_load_layer where rule_id=new.id;
  end if;
  return new;
end
$fn$;

revoke all on function ops.retired_rule_delivery_cleanup()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create trigger retired_rule_delivery_cleanup
after update of status on public.rule
for each row
when (old.status is distinct from new.status)
execute function ops.retired_rule_delivery_cleanup();

comment on function ops.retired_rule_delivery_cleanup() is
  'Forward SIEP repair: retirement atomically removes the no-longer-deliverable rule layer so deferred policy-epoch readback cannot retain an orphan.';

do $assert$
begin
  if not exists (
    select 1 from pg_trigger t
    join pg_class c on c.oid=t.tgrelid
    join pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relname='rule'
      and t.tgname='retired_rule_delivery_cleanup' and not t.tgisinternal
  ) then
    raise exception '0352 FAILED: retired rule delivery cleanup trigger is missing';
  end if;
  if has_function_privilege('public',
       'ops.retired_rule_delivery_cleanup()'::regprocedure,'execute')
     or has_function_privilege('carr_reader',
       'ops.retired_rule_delivery_cleanup()'::regprocedure,'execute')
     or has_function_privilege('carr_writer',
       'ops.retired_rule_delivery_cleanup()'::regprocedure,'execute')
     or has_function_privilege('carr_jobs',
       'ops.retired_rule_delivery_cleanup()'::regprocedure,'execute')
     or has_function_privilege('carr_authority',
       'ops.retired_rule_delivery_cleanup()'::regprocedure,'execute') then
    raise exception '0352 FAILED: cleanup trigger function is directly executable';
  end if;
end
$assert$;
