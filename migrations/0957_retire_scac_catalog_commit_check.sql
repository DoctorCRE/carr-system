-- rollback: forward-only — restore source integrity with a new migration; SCAC seals remain retired.
-- PR #1798 retired SCAC successor seals. Deferred epoch refresh must validate
-- doctrine, rule delivery and immutable ledger history without sealing verbs.
-- The v118 fields retain the existing epoch format; they confer no authority
-- over the current runtime catalog, whose generated registry is authoritative.

create or replace function ops.policy_integrity_snapshot()
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops,pg_temp as $fn$
declare doctrine_gen bigint; doctrine_digest text; rule_digest text; schema_count integer; schema_highest text; schema_digest text; delivery_health record;
begin
  select generation into doctrine_gen from public.doctrine_meta where id=1;
  if doctrine_gen is null then raise exception 'doctrine generation singleton is unavailable'; end if;
  doctrine_digest:=ops.scac_doctrine_projection_digest();
  if doctrine_digest is null then raise exception 'doctrine projection is unavailable'; end if;

  if (select count(*) from ops.rule_delivery_policy where singleton)<>1 then
    raise exception 'rule delivery policy singleton is unavailable';
  end if;
  select * into delivery_health from ops.rule_delivery_audit_counts(35);
  if delivery_health.total<1 or delivery_health.untagged<>0 or delivery_health.orphaned<>0
     or delivery_health.wildcarded<>0 or delivery_health.packless<>0
     or delivery_health.packs<1 or delivery_health.emptypack<>0
     or delivery_health.scope_mismatch<>0
     or delivery_health.layer0_shared>delivery_health.layer0_shared_cap
     or delivery_health.mode not in ('shadow','enforced') then
    raise exception 'rule delivery projection is not activation-safe: %',to_jsonb(delivery_health);
  end if;

  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'active_rules',coalesce((select jsonb_agg(jsonb_build_object(
      'id',r.id,'statement_digest','sha256:'||encode(public.digest(convert_to(r.statement,'UTF8'),'sha256'),'hex'),
      'rule_scope',r.scope,'personal_to',r.personal_to,'short_id',l.short_id,
      'load_layer',l.load_layer,'packs',l.packs,'scope',l.scope,
      'map_digest',l.map_digest) order by r.id::text collate "C")
      from public.rule r join ops.rule_load_layer l on l.rule_id=r.id where r.status='active'),'[]'::jsonb),
    'packs',coalesce((select jsonb_agg(jsonb_build_object('pack',pack,
      'title_digest','sha256:'||encode(public.digest(convert_to(title,'UTF8'),'sha256'),'hex'),
      'description_digest','sha256:'||encode(public.digest(convert_to(description,'UTF8'),'sha256'),'hex'),
      'triggers',triggers,'source_digest','sha256:'||encode(public.digest(convert_to(source,'UTF8'),'sha256'),'hex')) order by pack collate "C")
      from ops.rule_pack),'[]'::jsonb),
    'delivery_mode',(select mode from ops.rule_delivery_policy where singleton=true)
  )),'UTF8'),'sha256'),'hex') into rule_digest;
  if rule_digest is null then raise exception 'rule delivery projection is unavailable'; end if;

  select count(*)::integer,max(filename collate "C") collate "default",
    'sha256:'||encode(public.digest(coalesce(string_agg(convert_to(filename,'UTF8')||decode('00','hex')||convert_to(sha256,'UTF8')||decode('0a','hex'),''::bytea order by filename collate "C"),''::bytea),'sha256'),'hex')
    into schema_count,schema_highest,schema_digest from public.schema_migrations;
  if schema_count<1 or schema_highest is null then raise exception 'schema migration ledger is unavailable'; end if;
  return jsonb_build_object('registry_version','scac-mutation-registry.v118','registry_digest','sha256:560e16ad4c5383414b36b74da507481d9e840d8c00e45ced6eed6bce2ac3447d',
    'doctrine_generation',doctrine_gen,'doctrine_projection_digest',doctrine_digest,
    'rule_projection_digest',rule_digest,'schema_applied_count',schema_count,
    'schema_highest_migration',schema_highest,'schema_ledger_digest',schema_digest);
end $fn$;

revoke all on function ops.policy_integrity_snapshot() from public,carr_reader,carr_writer,carr_jobs,carr_authority;
comment on function ops.policy_integrity_snapshot() is
  'Deferred source integrity without retired SCAC seal or mutation-catalog validation. Registry fields retain historical epoch format only.';

create or replace function ops.scac_policy_epoch_refresh() returns trigger
language plpgsql security definer set search_path=pg_catalog,public,ops,pg_temp as $fn$
declare source jsonb; source_digest text; prior ops.scac_policy_epoch%rowtype; next_epoch bigint; created timestamptz:=clock_timestamp(); epoch_digest text; prefix_digest text; bootstrap_health record; settlement_key text;
begin
  settlement_key:=txid_current()::text||':'||extract(epoch from statement_timestamp())::text;
  if current_setting('carr.scac_epoch_settled',true)=settlement_key then return null; end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-internal:scac-core:policy-epoch',0));
  select * into prior from ops.scac_policy_epoch order by epoch desc limit 1 for update;
  if prior.epoch is null then
    select * into bootstrap_health from ops.rule_delivery_audit_counts(35);
    if bootstrap_health.total=0 and bootstrap_health.packs=0 and bootstrap_health.untagged=0
       and bootstrap_health.orphaned=0 and bootstrap_health.wildcarded=0
       and bootstrap_health.packless=0 and bootstrap_health.emptypack=0
       and bootstrap_health.scope_mismatch=0 and bootstrap_health.mode='shadow' then
      perform set_config('carr.scac_epoch_settled',settlement_key,true);
      return null;
    end if;
  end if;
  source:=ops.policy_integrity_snapshot();
  source_digest:='sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(source),'UTF8'),'sha256'),'hex');
  if prior.epoch is not null and prior.source_digest=source_digest then
    perform set_config('carr.scac_epoch_settled',settlement_key,true);
    return null;
  end if;
  if prior.epoch is not null then
    if (source->>'doctrine_generation')::bigint<prior.doctrine_generation then raise exception 'doctrine generation rollback refused'; end if;
    if (source->>'schema_applied_count')::integer<prior.schema_applied_count then raise exception 'schema ledger rollback refused'; end if;
    select 'sha256:'||encode(public.digest(coalesce(string_agg(convert_to(filename,'UTF8')||decode('00','hex')||convert_to(sha256,'UTF8')||decode('0a','hex'),''::bytea order by filename collate "C"),''::bytea),'sha256'),'hex') into prefix_digest
      from (select filename,sha256 from public.schema_migrations order by filename collate "C" limit prior.schema_applied_count) prefix;
    if prefix_digest is distinct from prior.schema_ledger_digest then raise exception 'schema ledger prefix rewrite refused'; end if;
  end if;
  next_epoch:=coalesce(prior.epoch,0)+1;
  epoch_digest:=ops.scac_policy_epoch_digest(next_epoch,coalesce(prior.epoch,0),coalesce(prior.epoch_digest,'bootstrap'),source_digest,created);
  insert into ops.scac_policy_epoch(epoch,epoch_digest,previous_epoch,previous_epoch_digest,program_key,tenant_scope,policy_domain,
    registry_version,registry_digest,doctrine_generation,doctrine_projection_digest,rule_projection_digest,schema_applied_count,schema_highest_migration,
    schema_ledger_digest,source_digest,source_session_user,source_relation,created_at)
  values(next_epoch,epoch_digest,prior.epoch,prior.epoch_digest,'carr-system-integrity-elimination-v1','carr-internal','scac-core',
    source->>'registry_version',source->>'registry_digest',(source->>'doctrine_generation')::bigint,source->>'doctrine_projection_digest',source->>'rule_projection_digest',
    (source->>'schema_applied_count')::integer,source->>'schema_highest_migration',source->>'schema_ledger_digest',source_digest,
    session_user,TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,created);
  perform set_config('carr.scac_epoch_settled',settlement_key,true);
  return null;
end $fn$;

revoke all on function ops.scac_policy_epoch_refresh() from public,carr_reader,carr_writer,carr_jobs,carr_authority;
comment on function ops.scac_policy_epoch_refresh() is
  'Deferred doctrine, rule delivery and ledger integrity; historical SCAC catalog audit does not gate commits.';
