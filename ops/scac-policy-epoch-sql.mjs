const CHARTER_DIGEST = "sha256:473b7b1cd2ea975ba118f05406b35f4affdda0cb61f4487c252db129a882151c";

function registryDigestFromGeneratedSql(sql) {
  const pattern = new RegExp(
    `'scac-mutation-registry\\.v2','carr-system-integrity-elimination-v1','11','${CHARTER_DIGEST}','(sha256:[0-9a-f]{64})'`,
  );
  const match = sql.match(pattern);
  if (!match) throw new Error("generated SIEP-12 registry SQL lacks one exact v2 digest");
  return match[1];
}

export function renderPolicyEpochMigration(registrySql, { v1Seal, dbCatalogBaseline }) {
  const registryDigest = registryDigestFromGeneratedSql(registrySql);
  return `-- SIEP-12 / SCAC-02: monotonic transactional policy epoch and compatibility state.
-- Source/test implementation only. Production application remains Joe go/no-go gated.
-- Intentionally no BEGIN/COMMIT: tools/migrate.py owns the transaction and records
-- this file's exact SHA before commit, when the deferred epoch trigger snapshots it.

do $guard$
declare v ops.scac_mutation_registry_version%rowtype; actual_count integer; actual_set text; bad_hash boolean;
begin
  select * into v from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v1';
  select count(*),'sha256:'||encode(public.digest(convert_to(coalesce(string_agg(entry_digest,',' order by ingress_key collate "C"),''),'UTF8'),'sha256'),'hex'),
         coalesce(bool_or(entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex')),false)
    into actual_count,actual_set,bad_hash from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v1';
  if v.registry_digest<>'${v1Seal.digest}'
     or actual_count<>${v1Seal.entryCount} or actual_set is distinct from v.entry_set_digest or bad_hash then
    raise exception 'SIEP-12 refuses because sealed mutation registry v1 is not exact';
  end if;
end $guard$;

create table ops.scac_policy_epoch (
  epoch bigint primary key check(epoch>0),
  epoch_digest text not null unique check(epoch_digest~'^sha256:[0-9a-f]{64}$'),
  previous_epoch bigint,
  previous_epoch_digest text,
  program_key text not null check(program_key='carr-system-integrity-elimination-v1'),
  tenant_scope text not null check(tenant_scope='carr-internal'),
  policy_domain text not null check(policy_domain='scac-core'),
  registry_version text not null check(registry_version='scac-mutation-registry.v2'),
  registry_digest text not null check(registry_digest='${registryDigest}'),
  doctrine_generation bigint not null check(doctrine_generation>=0),
  doctrine_projection_digest text not null check(doctrine_projection_digest~'^sha256:[0-9a-f]{64}$'),
  rule_projection_digest text not null check(rule_projection_digest~'^sha256:[0-9a-f]{64}$'),
  schema_applied_count integer not null check(schema_applied_count>0),
  schema_highest_migration text not null check(schema_highest_migration~'^[0-9]{4}[a-z]?_[a-z0-9_]+\\.sql$'),
  schema_ledger_digest text not null check(schema_ledger_digest~'^sha256:[0-9a-f]{64}$'),
  source_digest text not null check(source_digest~'^sha256:[0-9a-f]{64}$'),
  source_session_user text not null check(btrim(source_session_user)<>'' and char_length(source_session_user)<=100),
  source_relation text not null check(source_relation in ('public.schema_migrations','public.doctrine_meta','public.doctrine_document','public.doctrine_slug_alias','public.doctrine_section','public.doctrine_revision','public.doctrine_edge_type','public.doctrine_edge','public.doctrine_link','public.doctrine_review_policy','public.doctrine_snapshot','public.doctrine_gate_check','public.doctrine_concept_mapping','public.rule','ops.rule_pack','ops.rule_load_layer','ops.rule_delivery_policy','ops.scac_mutation_registry_version')),
  created_at timestamptz not null default clock_timestamp(),
  atomic_database_mediation_operational boolean not null default false check(not atomic_database_mediation_operational),
  production_enforcement_active boolean not null default false check(not production_enforcement_active),
  unique(epoch,epoch_digest),
  foreign key(previous_epoch,previous_epoch_digest) references ops.scac_policy_epoch(epoch,epoch_digest) on delete restrict,
  check((epoch=1 and previous_epoch is null and previous_epoch_digest is null)
     or (epoch>1 and previous_epoch=epoch-1 and previous_epoch_digest is not null))
);

comment on table ops.scac_policy_epoch is
  'SIEP-12 sole policy epoch authority: append-only hashes over existing registry, doctrine, rule-delivery, and schema ledgers. It does not authorize mutations, attest workloads/artifacts, or activate Production enforcement.';

create or replace function ops.scac_policy_epoch_append_only() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SCAC policy epoch is append-only'; end $fn$;

create or replace function ops.scac_policy_source_truncate_refused() returns trigger
language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SCAC policy source truncation is refused'; end $fn$;

create or replace function ops.scac_doctrine_projection_digest()
returns text language sql stable security definer set search_path=pg_catalog,public,ops as $fn$
  with facts(table_name,row_value) as (
    select 'doctrine_document',to_jsonb(x) from public.doctrine_document x union all
    select 'doctrine_slug_alias',to_jsonb(x) from public.doctrine_slug_alias x union all
    select 'doctrine_section',to_jsonb(x) from public.doctrine_section x union all
    select 'doctrine_revision',to_jsonb(x) from public.doctrine_revision x union all
    select 'doctrine_edge_type',to_jsonb(x) from public.doctrine_edge_type x union all
    select 'doctrine_edge',to_jsonb(x) from public.doctrine_edge x union all
    select 'doctrine_link',to_jsonb(x) from public.doctrine_link x union all
    select 'doctrine_review_policy',to_jsonb(x) from public.doctrine_review_policy x union all
    select 'doctrine_snapshot',to_jsonb(x) from public.doctrine_snapshot x union all
    select 'doctrine_gate_check',to_jsonb(x) from public.doctrine_gate_check x union all
    select 'doctrine_concept_mapping',to_jsonb(x) from public.doctrine_concept_mapping x
  )
  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(
    jsonb_agg(jsonb_build_object('table',table_name,'row',row_value)
      order by table_name collate "C",ops.scac_canonical_json(row_value) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex')
  from facts
$fn$;

create or replace function ops.scac_mutation_catalog_v2_current()
returns boolean language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare observed_count integer; observed_digest text;
begin
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),
  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),
  observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,
    jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row
    from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>${dbCatalogBaseline.secdef_execute.count} or observed_digest<>'${dbCatalogBaseline.secdef_execute.digest}' then return false; end if;

  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')),
  observed as (select 'db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,
    jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row
    from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>${dbCatalogBaseline.relation_dml.count} or observed_digest<>'${dbCatalogBaseline.relation_dml.digest}' then return false; end if;

  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(a.attacl) acl where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0 and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')),
  observed as (select 'db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,
    jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row
    from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>${dbCatalogBaseline.column_dml.count} or observed_digest<>'${dbCatalogBaseline.column_dml.digest}' then return false; end if;

  with recursive connected(oid) as (
    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci'
      and not rolcanlogin and not rolsuper
    union
    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid
      join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end
     where other.rolname~'^carr_' and other.rolname<>'carr_ci'
       and not other.rolcanlogin and not other.rolsuper
  ), role_rows as (
    select 'db-role:'||r.rolname ingress_key,jsonb_build_object(
      'ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,
      'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,
      'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,
      'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row
      from pg_roles r where r.oid in(select oid from connected)
  ), membership_rows as (
    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,
      jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,
        'row_kind','membership','role',role.rolname,'member',member.rolname,
        'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row
      from pg_auth_members m join pg_roles role on role.oid=m.roleid
      join pg_roles member on member.oid=m.member
     where m.roleid in(select oid from connected) and m.member in(select oid from connected)
  ), ownership_rows as (
    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,
      jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,
        'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row
      from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner
     where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')
       and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
    union all
    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,
      jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,
        'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname)
      from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner
     where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')
       and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(
    coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex')
    into observed_count,observed_digest from observed;
  if exists (select 1 from pg_auth_members m join pg_roles g on g.oid=m.roleid
    join pg_roles mem on mem.oid=m.member
    where mem.rolname~'^carr_' and (g.rolsuper or g.rolname~'^(neon_|pg_)')) then return false; end if;
  return observed_count=${dbCatalogBaseline.role_authority.count} and observed_digest='${dbCatalogBaseline.role_authority.digest}';
end $fn$;

create or replace function ops.scac_policy_epoch_snapshot()
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare registry_ok jsonb; doctrine_gen bigint; doctrine_digest text; rule_digest text; schema_count integer; schema_highest text; schema_digest text; delivery_health record;
begin
  registry_ok:=ops.scac_mutation_registration_v2('${registryDigest}','mcp-tool:standing-context');
  if coalesce((registry_ok->>'registered')::boolean,false) is not true then
    raise exception 'sealed SCAC mutation registry v2 is unavailable or corrupt: %',registry_ok->>'reason';
  end if;
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
  return jsonb_build_object('registry_version','scac-mutation-registry.v2','registry_digest','${registryDigest}',
    'doctrine_generation',doctrine_gen,'doctrine_projection_digest',doctrine_digest,
    'rule_projection_digest',rule_digest,'schema_applied_count',schema_count,
    'schema_highest_migration',schema_highest,'schema_ledger_digest',schema_digest);
end $fn$;

create or replace function ops.scac_policy_epoch_digest(
  p_epoch bigint,p_previous_epoch bigint,p_previous_digest text,p_source_digest text,p_created_at timestamptz
) returns text language sql immutable strict set search_path=pg_catalog,ops as $fn$
  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-policy-epoch.v1','program_key','carr-system-integrity-elimination-v1',
    'tenant_scope','carr-internal','policy_domain','scac-core','epoch',p_epoch,
    'previous_epoch',p_previous_epoch,'previous_epoch_digest',p_previous_digest,
    'source_digest',p_source_digest,'created_at',p_created_at)),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_policy_epoch_chain_state()
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare r ops.scac_policy_epoch%rowtype; expected bigint:=1; prior_digest text:=null; recomputed_source text; recomputed_epoch text; source jsonb; latest ops.scac_policy_epoch%rowtype;
begin
  for r in select * from ops.scac_policy_epoch order by epoch loop
    if r.epoch<>expected or (expected=1 and (r.previous_epoch is not null or r.previous_epoch_digest is not null))
       or (expected>1 and (r.previous_epoch<>expected-1 or r.previous_epoch_digest is distinct from prior_digest)) then
      return jsonb_build_object('valid',false,'reason','epoch_chain_gap_or_fork');
    end if;
    recomputed_source:='sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
      'registry_version',r.registry_version,'registry_digest',r.registry_digest,
      'doctrine_generation',r.doctrine_generation,'doctrine_projection_digest',r.doctrine_projection_digest,
      'rule_projection_digest',r.rule_projection_digest,
      'schema_applied_count',r.schema_applied_count,'schema_highest_migration',r.schema_highest_migration,
      'schema_ledger_digest',r.schema_ledger_digest)),'UTF8'),'sha256'),'hex');
    recomputed_epoch:=ops.scac_policy_epoch_digest(r.epoch,coalesce(r.previous_epoch,0),coalesce(r.previous_epoch_digest,'bootstrap'),recomputed_source,r.created_at);
    if r.registry_version<>'scac-mutation-registry.v2' or r.registry_digest<>'${registryDigest}'
       or r.source_digest is distinct from recomputed_source or r.epoch_digest is distinct from recomputed_epoch
       or r.created_at>clock_timestamp()+interval '1 minute' then
      return jsonb_build_object('valid',false,'reason','epoch_digest_or_source_corrupt');
    end if;
    latest:=r; prior_digest:=r.epoch_digest; expected:=expected+1;
  end loop;
  if expected=1 then return jsonb_build_object('valid',false,'reason','epoch_ledger_unavailable'); end if;
  begin source:=ops.scac_policy_epoch_snapshot(); exception when others then
    return jsonb_build_object('valid',false,'reason','live_source_unavailable'); end;
  return jsonb_build_object('valid',true,'reason','valid','current_epoch',latest.epoch,
    'current_epoch_digest',latest.epoch_digest,'current_source_digest',latest.source_digest,
    'live_source_digest','sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(source),'UTF8'),'sha256'),'hex'),
    'registry_version',latest.registry_version,'registry_digest',latest.registry_digest,
    'schema_highest_migration',latest.schema_highest_migration);
end $fn$;

create or replace function ops.scac_policy_epoch_refresh() returns trigger
language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare source jsonb; source_digest text; prior ops.scac_policy_epoch%rowtype; next_epoch bigint; created timestamptz:=clock_timestamp(); epoch_digest text; prefix_digest text; bootstrap_health record;
begin
  perform pg_advisory_xact_lock(hashtextextended('carr-internal:scac-core:policy-epoch',0));
  select * into prior from ops.scac_policy_epoch order by epoch desc limit 1 for update;
  if prior.epoch is null then
    select * into bootstrap_health from ops.rule_delivery_audit_counts(35);
    if bootstrap_health.total=0 and bootstrap_health.packs=0 and bootstrap_health.untagged=0
       and bootstrap_health.orphaned=0 and bootstrap_health.wildcarded=0
       and bootstrap_health.packless=0 and bootstrap_health.emptypack=0
       and bootstrap_health.scope_mismatch=0 and bootstrap_health.mode='shadow' then
      -- A freshly reconstructed database has no rule source until the canonical
      -- control-plane sync.  Keep the epoch absent (therefore incompatible)
      -- rather than blessing an empty policy.  The sync's deferred triggers
      -- bootstrap epoch 1 from its final coherent projection.
      return null;
    end if;
  end if;
  source:=ops.scac_policy_epoch_snapshot();
  source_digest:='sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(source),'UTF8'),'sha256'),'hex');
  if prior.epoch is not null and prior.source_digest=source_digest then return null; end if;
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
  return null;
end $fn$;

create or replace function ops.scac_policy_epoch_status(p_request_epoch bigint,p_request_epoch_digest text)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare chain jsonb; current_epoch bigint; current_digest text; request_digest text; live_snapshot jsonb; live_digest text; epoch_state text; compatibility_state text:='incompatible'; reason_id text:='scac.refusal.epoch_incompatible';
begin
  chain:=ops.scac_policy_epoch_chain_state();
  if coalesce((chain->>'valid')::boolean,false) is not true then
    return jsonb_build_object('schema_version','scac-policy-epoch-status.v1','current_epoch',null,'request_epoch',p_request_epoch,
      'epoch_state',null,'compatibility_state','incompatible','reason_id','scac.refusal.epoch_incompatible',
      'current_entry_digest',null,'registry_version','scac-mutation-registry.v2','registry_digest','${registryDigest}',
      'compatibility_authority','fact_only_not_enforcement');
  end if;
  current_epoch:=(chain->>'current_epoch')::bigint; current_digest:=chain->>'current_epoch_digest';
  live_snapshot:=ops.scac_policy_epoch_snapshot();
  live_digest:='sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(live_snapshot),'UTF8'),'sha256'),'hex');
  if live_digest is distinct from chain->>'current_source_digest' then epoch_state:=null;
  elsif p_request_epoch is null or p_request_epoch<1 or p_request_epoch_digest is null then epoch_state:=null;
  elsif p_request_epoch>current_epoch then epoch_state:='future';
  elsif p_request_epoch<current_epoch then epoch_state:='stale';
  else
    select epoch_digest into request_digest from ops.scac_policy_epoch where epoch=p_request_epoch;
    if request_digest is distinct from p_request_epoch_digest then epoch_state:='rolled_back';
    else epoch_state:='current'; compatibility_state:='compatible'; reason_id:=null; end if;
  end if;
  return jsonb_build_object('schema_version','scac-policy-epoch-status.v1','current_epoch',current_epoch,'request_epoch',p_request_epoch,
    'epoch_state',epoch_state,'compatibility_state',compatibility_state,'reason_id',reason_id,
    'current_entry_digest',current_digest,'registry_version',chain->>'registry_version',
    'registry_digest',chain->>'registry_digest','compatibility_authority','fact_only_not_enforcement');
end $fn$;

-- Exact-v2 lookup: never latest/max, caller-selected version, or fallback to v1.
create or replace function ops.scac_mutation_registration_v2(p_expected_digest text,p_ingress_key text)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops as $fn$
declare v ops.scac_mutation_registry_version%rowtype; e ops.scac_mutation_registry_entry%rowtype; actual_count integer; actual_set text; bad_hash boolean;
begin
  select * into v from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v2';
  if v.registry_version is null then return jsonb_build_object('registered',false,'reason','registry_unavailable'); end if;
  select count(*),'sha256:'||encode(public.digest(convert_to(coalesce(string_agg(entry_digest,',' order by ingress_key collate "C"),''),'UTF8'),'sha256'),'hex'),
    coalesce(bool_or(entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex')),false)
    into actual_count,actual_set,bad_hash from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v2';
  if actual_count<>v.entry_count or actual_set is distinct from v.entry_set_digest or bad_hash or not ops.scac_mutation_catalog_v2_current() then return jsonb_build_object('registered',false,'reason','registry_corrupt','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;
  if p_expected_digest is distinct from '${registryDigest}' or v.registry_digest is distinct from '${registryDigest}' then return jsonb_build_object('registered',false,'reason','digest_mismatch','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;
  if p_ingress_key is null or p_ingress_key!~'^[a-z][a-z0-9_-]+:' or p_ingress_key~E'[\\n\\r\\t]' or char_length(p_ingress_key)>1000 then return jsonb_build_object('registered',false,'reason','malformed_ingress','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;
  select * into e from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v2' and ingress_key=p_ingress_key;
  if e.ingress_key is null then return jsonb_build_object('registered',false,'reason','unknown_ingress','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;
  return jsonb_build_object('registered',true,'reason','registered_inventory','registry_version',v.registry_version,'registry_digest',v.registry_digest,'ingress_key',e.ingress_key,'ingress_kind',e.ingress_kind,'effect_class',e.effect_class,'entry_digest',e.entry_digest,'atomic_database_mediation_operational',false);
end $fn$;

revoke all on ops.scac_policy_epoch from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.scac_policy_epoch_append_only(),ops.scac_policy_source_truncate_refused(),ops.scac_doctrine_projection_digest(),ops.scac_mutation_catalog_v2_current(),ops.scac_policy_epoch_snapshot(),ops.scac_policy_epoch_digest(bigint,bigint,text,text,timestamptz),ops.scac_policy_epoch_chain_state(),ops.scac_policy_epoch_refresh(),ops.scac_policy_epoch_status(bigint,text),ops.scac_mutation_registration_v2(text,text),ops.scac_mutation_registration(text,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.scac_policy_epoch_status(bigint,text),ops.scac_mutation_registration_v2(text,text) to carr_reader,carr_writer,carr_jobs,carr_authority;

create trigger scac_policy_epoch_immutable before update or delete on ops.scac_policy_epoch for each row execute function ops.scac_policy_epoch_append_only();
create trigger scac_policy_epoch_truncate_refused before truncate on ops.scac_policy_epoch for each statement execute function ops.scac_policy_source_truncate_refused();

${registrySql}

-- Deferred source triggers see the final transaction state. The migration runner's
-- schema_migrations insert after this file creates epoch 1 and binds this file's SHA.
create constraint trigger scac_epoch_schema_ledger after insert or update or delete on public.schema_migrations deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_generation after insert or update or delete on public.doctrine_meta deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_document after insert or update or delete on public.doctrine_document deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_slug_alias after insert or update or delete on public.doctrine_slug_alias deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_section after insert or update or delete on public.doctrine_section deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_revision after insert or update or delete on public.doctrine_revision deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_edge_type after insert or update or delete on public.doctrine_edge_type deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_edge after insert or update or delete on public.doctrine_edge deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_link after insert or update or delete on public.doctrine_link deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_review_policy after insert or update or delete on public.doctrine_review_policy deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_snapshot after insert or update or delete on public.doctrine_snapshot deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_gate_check after insert or update or delete on public.doctrine_gate_check deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_doctrine_concept_mapping after insert or update or delete on public.doctrine_concept_mapping deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_rule after insert or update or delete on public.rule deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_rule_pack after insert or update or delete on ops.rule_pack deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_rule_load_layer after insert or update or delete on ops.rule_load_layer deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_rule_delivery_policy after insert or update or delete on ops.rule_delivery_policy deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();
create constraint trigger scac_epoch_registry_version after insert or update or delete on ops.scac_mutation_registry_version deferrable initially deferred for each row execute function ops.scac_policy_epoch_refresh();

create trigger scac_epoch_schema_truncate_refused before truncate on public.schema_migrations for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_truncate_refused before truncate on public.doctrine_meta for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_document_truncate_refused before truncate on public.doctrine_document for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_slug_alias_truncate_refused before truncate on public.doctrine_slug_alias for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_section_truncate_refused before truncate on public.doctrine_section for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_revision_truncate_refused before truncate on public.doctrine_revision for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_edge_type_truncate_refused before truncate on public.doctrine_edge_type for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_edge_truncate_refused before truncate on public.doctrine_edge for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_link_truncate_refused before truncate on public.doctrine_link for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_review_policy_truncate_refused before truncate on public.doctrine_review_policy for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_snapshot_truncate_refused before truncate on public.doctrine_snapshot for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_gate_check_truncate_refused before truncate on public.doctrine_gate_check for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_doctrine_concept_mapping_truncate_refused before truncate on public.doctrine_concept_mapping for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_rule_truncate_refused before truncate on public.rule for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_rule_pack_truncate_refused before truncate on ops.rule_pack for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_rule_load_layer_truncate_refused before truncate on ops.rule_load_layer for each statement execute function ops.scac_policy_source_truncate_refused();
create trigger scac_epoch_rule_delivery_policy_truncate_refused before truncate on ops.rule_delivery_policy for each statement execute function ops.scac_policy_source_truncate_refused();

comment on function ops.scac_policy_epoch_status(bigint,text) is
  'Read-only SIEP-12 compatibility fact. Exact current epoch+digest is compatible; stale/future/equivocated/live-drifted facts deny. SIEP-04/18 own runtime enforcement.';
`;
}
