-- Hardening must leave the live catalog sealed and must not accept
-- subsequent drift.
\set ON_ERROR_STOP on
begin;
do $coherence$
declare head text; ok boolean;
begin
  -- Migration 1033 left one registry version: the frontier the generated
  -- registry binds to. Its catalog check must hold on the rebuilt database.
  if (select count(*) from ops.scac_mutation_registry_version) <> 1 then
    raise exception 'historical registry versions are installed again';
  end if;
  select registry_version into head from ops.scac_mutation_registry_version;
  execute format('select ops.%I()','scac_mutation_catalog_'||substring(head from '[^.]+$')||'_current') into ok;
  if not ok then
    raise exception 'hardening successor does not match the live catalog';
  end if;
end $coherence$;
savepoint assignment_grant;
revoke update on public.deal_vendor_assignment from carr_writer;
do $assignment_grant_drift$
begin
  if ops.scac_mutation_catalog_v118_current() then
    raise exception 'catalog accepted a missing reviewed assignment grant';
  end if;
end $assignment_grant_drift$;
rollback to savepoint assignment_grant;
do $assignment_grant_access$
begin
  if has_table_privilege('carr_reader','public.deal_vendor_assignment','select') then
    raise exception 'reader can bypass assignment projection';
  end if;
end $assignment_grant_access$;
-- An unrelated search path change must still invalidate the current seal.
alter function ops.engineering_admission_source(text) set search_path=pg_catalog,ops,public;
do $drift$
declare head text; ok boolean;
begin
  select registry_version into head from ops.scac_mutation_registry_version;
  execute format('select ops.%I()','scac_mutation_catalog_'||substring(head from '[^.]+$')||'_current') into ok;
  if ok then
    raise exception 'hardening successor accepted unsealed metadata';
  end if;
end $drift$;
rollback;
