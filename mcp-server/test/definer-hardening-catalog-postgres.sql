-- Hardening must leave the live policy epoch readable, retain every historic
-- seal, and seal the new metadata without accepting subsequent drift.
\set ON_ERROR_STOP on
begin;
-- Snapshot-only rebuilds have no business/rule content. Supply a bounded,
-- rollback-only projection so the live policy API reaches its catalog guard.
insert into public.actor(id,slug,kind,display_name) values
  ('30000000-0000-4000-8000-000000000001','catalog-fixture','human','Catalog fixture');
alter table public.rule disable trigger user;
insert into public.rule(id,statement,taught_by,status,activated_by) values
  ('30000000-0000-4000-8000-000000000002','Synthetic catalog fixture',
   '30000000-0000-4000-8000-000000000001','active','30000000-0000-4000-8000-000000000001');
alter table public.rule enable trigger user;
insert into ops.rule_pack(pack,title,description,triggers,source) values
  ('catalog-fixture','Catalog fixture','Synthetic fixture',array['catalog'],'ops/config/rule-enforcement-map.json');
insert into ops.rule_load_layer(rule_id,short_id,load_layer,packs,scope,why,source,map_digest) values
  ('30000000-0000-4000-8000-000000000002','30000000','pack',array['catalog-fixture'],
   'shared','Synthetic fixture','ops/config/rule-enforcement-map.json',repeat('0',64));
do $coherence$
declare snapshot jsonb; version text; head text; ok boolean;
begin
  -- The head is derived from the registry chain itself, so a renumber or a
  -- later successor needs no edit here; the live epoch must still equal it.
  select registry_version into head from ops.scac_mutation_registry_version
   order by substring(registry_version from '[0-9]+$')::int desc limit 1;
  snapshot:=ops.scac_policy_epoch_snapshot();
  if snapshot->>'registry_version' is distinct from head then
    raise exception 'hardening successor is not the current policy registry';
  end if;
  foreach version in array array(select registry_version from ops.scac_mutation_registry_version) loop
    if not ops.scac_mutation_registry_seal_valid(version) then
      raise exception 'registry seal invalid: %',version;
    end if;
  end loop;
  execute format('select ops.%I()','scac_mutation_catalog_'||substring(head from '[^.]+$')||'_current') into ok;
  if not ok then
    raise exception 'hardening successor does not match the live catalog';
  end if;
end $coherence$;
-- An unrelated search path change must still invalidate the current seal.
alter function ops.engineering_admission_source(text) set search_path=pg_catalog,ops,public;
do $drift$
declare head text; ok boolean;
begin
  select registry_version into head from ops.scac_mutation_registry_version
   order by substring(registry_version from '[0-9]+$')::int desc limit 1;
  execute format('select ops.%I()','scac_mutation_catalog_'||substring(head from '[^.]+$')||'_current') into ok;
  if ok then
    raise exception 'hardening successor accepted unsealed metadata';
  end if;
  begin
    perform ops.scac_policy_epoch_snapshot();
    raise exception 'policy snapshot accepted unsealed metadata';
  exception when others then
    if sqlerrm not like 'live SCAC % mutation catalog drifted' then raise; end if;
  end;
end $drift$;
rollback;
