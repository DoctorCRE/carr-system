-- rollback: forward-only, preserve the immutable applied ledger and use a new migration for repairs.
-- lock-review: schema_migrations is a small metadata ledger; the lock serializes adoption with migration inserts.
-- Existing epoch hashes remain unchanged. New rows extend a set; row guards
-- preserve history without requiring filename order to equal arrival order.

lock table public.schema_migrations in share row exclusive mode;
select pg_advisory_xact_lock(hashtextextended('carr-internal:scac-core:policy-epoch',0));

do $adopt$
declare prior ops.scac_policy_epoch%rowtype; prefix_digest text;
begin
  select * into prior from ops.scac_policy_epoch order by epoch desc limit 1;
  if prior.epoch is not null then
    select 'sha256:'||encode(public.digest(coalesce(string_agg(convert_to(filename,'UTF8')||decode('00','hex')||convert_to(sha256,'UTF8')||decode('0a','hex'),''::bytea order by filename collate "C"),''::bytea),'sha256'),'hex') into prefix_digest
      from (select filename,sha256 from public.schema_migrations order by filename collate "C" limit prior.schema_applied_count) prefix;
    if prefix_digest is distinct from prior.schema_ledger_digest then
      raise exception 'schema ledger adoption refused: prior applied history differs';
    end if;
  end if;
end $adopt$;

create function ops.schema_migrations_immutable() returns trigger
language plpgsql security definer set search_path=pg_catalog,public,ops,pg_temp as $guard$
begin
  raise exception 'applied schema migration rows are immutable';
end $guard$;
revoke all on function ops.schema_migrations_immutable() from public,carr_reader,carr_writer,carr_jobs,carr_authority;
create trigger schema_migrations_immutable before update or delete on public.schema_migrations
for each row execute function ops.schema_migrations_immutable();

create or replace function ops.scac_policy_epoch_refresh() returns trigger
language plpgsql security definer set search_path=pg_catalog,public,ops,pg_temp as $fn$
declare source jsonb; source_digest text; prior ops.scac_policy_epoch%rowtype; next_epoch bigint; created timestamptz:=clock_timestamp(); epoch_digest text; bootstrap_health record; settlement_key text;
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
