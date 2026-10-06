-- Expose aggregate rule-delivery health to the unattended jobs role without
-- granting that role rule text or actor-table reads.

begin;

create or replace function ops.rule_delivery_audit_counts(
    p_layer0_shared_cap integer default 35)
returns table(
  total bigint,
  untagged bigint,
  orphaned bigint,
  layer0 bigint,
  control bigint,
  pack bigint,
  layer0_shared bigint,
  layer0_shared_cap integer,
  wildcarded bigint,
  packless bigint,
  packs bigint,
  emptypack bigint,
  scope_mismatch bigint,
  mode text)
language sql stable security definer
set search_path = pg_catalog, public, ops
as $function$
  select
    (select count(*) from public.rule r where r.status = 'active'),
    (select count(*)
       from public.rule r
       left join ops.rule_load_layer l on l.rule_id = r.id
      where r.status = 'active' and l.rule_id is null),
    (select count(*)
       from ops.rule_load_layer l
      where not exists (
        select 1 from public.rule r
         where r.id = l.rule_id and r.status = 'active')),
    (select count(*)
       from ops.rule_load_layer l
       join public.rule r on r.id = l.rule_id and r.status = 'active'
      where l.load_layer = 'layer0'),
    (select count(*)
       from ops.rule_load_layer l
       join public.rule r on r.id = l.rule_id and r.status = 'active'
      where l.load_layer = 'control'),
    (select count(*)
       from ops.rule_load_layer l
       join public.rule r on r.id = l.rule_id and r.status = 'active'
      where l.load_layer = 'pack'),
    (select count(*)
       from ops.rule_load_layer l
       join public.rule r on r.id = l.rule_id and r.status = 'active'
      where l.load_layer = 'layer0' and l.scope = 'shared'),
    p_layer0_shared_cap,
    (select count(*)
       from ops.rule_load_layer l
       join public.rule r on r.id = l.rule_id and r.status = 'active'
      where '*' = any(l.packs)),
    (select count(*)
       from ops.rule_load_layer l
       join public.rule r on r.id = l.rule_id and r.status = 'active'
      where l.load_layer = 'pack' and cardinality(l.packs) = 0),
    (select count(*) from ops.rule_pack),
    (select count(*)
       from ops.rule_pack p
      where not exists (
        select 1
          from ops.rule_load_layer l
          join public.rule r on r.id = l.rule_id and r.status = 'active'
         where p.pack = any(l.packs))),
    (select count(*)
       from public.rule r
       join ops.rule_load_layer l on l.rule_id = r.id
       left join public.actor owner on owner.id = r.personal_to
      where r.status = 'active'
        and l.scope is distinct from coalesce(owner.slug, 'shared')),
    coalesce(
      (select p.mode from ops.rule_delivery_policy p limit 1),
      '(none)')
$function$;

comment on function ops.rule_delivery_audit_counts(integer) is
  'Aggregate delivery coverage, layer, scope and policy health. SECURITY DEFINER '
  'so carr_jobs can detect missing current rules and personal-scope drift without '
  'receiving SELECT on rule text or actor rows.';

revoke all on function ops.rule_delivery_audit_counts(integer) from public;
grant execute on function ops.rule_delivery_audit_counts(integer)
  to carr_reader, carr_writer;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'carr_jobs') then
    execute 'grant execute on function ops.rule_delivery_audit_counts(integer) '
            'to carr_jobs';
  end if;
end;
$$;

commit;

do $$
declare health record;
begin
  if to_regprocedure('ops.rule_delivery_audit_counts(integer)') is null then
    raise exception '0316 FAILED: aggregate delivery audit function is missing';
  end if;
  select * into health from ops.rule_delivery_audit_counts(35);
  if health.total is null or health.scope_mismatch is null or health.mode is null then
    raise exception '0316 FAILED: aggregate delivery audit returned an incomplete row';
  end if;
  if exists (select 1 from pg_roles where rolname = 'carr_jobs')
     and not has_function_privilege(
       'carr_jobs', 'ops.rule_delivery_audit_counts(integer)', 'execute') then
    raise exception '0316 FAILED: carr_jobs cannot execute the aggregate audit';
  end if;
  if exists (select 1 from pg_roles where rolname = 'carr_jobs')
     and has_table_privilege('carr_jobs', 'public.rule', 'select') then
    raise exception '0316 FAILED: audit migration widened carr_jobs to rule text';
  end if;
end;
$$;
