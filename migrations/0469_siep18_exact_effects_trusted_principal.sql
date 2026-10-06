-- 0469 / SIEP-18 bounded successor: exact reviewed effects and trusted principal binding.
--
-- This successor is additive and source/test-only. It creates no reviewed
-- operation rows, changes no runtime grants, and does not activate the monitor.
-- An ingress remains denied until Joe records a closed static contract. The
-- server principal is derived before handler dispatch from authenticated grant
-- props plus live session_user/current_user/backend readback; tool arguments
-- are never an identity input.
-- Intentionally no BEGIN/COMMIT: tools/migrate.py owns the transaction.

create table ops.scac_exact_effect_contract (
  registry_version text not null,
  ingress_key text not null,
  registry_entry_digest text not null check (registry_entry_digest~'^sha256:[0-9a-f]{64}$'),
  contract jsonb not null check (jsonb_typeof(contract)='object'),
  contract_digest text not null unique check (contract_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_key uuid not null unique,
  recorded_by text not null check (recorded_by='joe'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  primary key (registry_version,ingress_key),
  foreign key (registry_version,ingress_key,registry_entry_digest)
    references ops.scac_mutation_registry_entry(registry_version,ingress_key,entry_digest)
    on delete restrict
);

create table ops.scac_trusted_principal_binding (
  binding_id uuid primary key default gen_random_uuid(),
  principal_manifest jsonb not null check (jsonb_typeof(principal_manifest)='object'),
  principal_digest text not null check (principal_digest~'^sha256:[0-9a-f]{64}$'),
  actor_id uuid not null references public.actor(id) on delete restrict,
  actor_slug text not null,
  session_principal text not null check (session_principal in
    ('carr_writer','carr_jobs','carr_authority_joe','carr_authority_dell')),
  privilege_bundle text not null check (privilege_bundle in
    ('carr_writer','carr_jobs','carr_authority')),
  backend_pid integer not null check (backend_pid>0),
  transaction_id bigint not null check (transaction_id>0),
  binding_digest text not null unique check (binding_digest~'^sha256:[0-9a-f]{64}$'),
  idempotency_key uuid not null unique,
  source_contract text not null check (source_contract='mcp-server/src/scac-exact-effects.js'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_enforcement_active boolean not null default false
    check (not production_enforcement_active),
  unique (binding_id,binding_digest)
);

comment on table ops.scac_exact_effect_contract is
  'SIEP-18 immutable reviewed logical effects. No row means default deny; write labels, prose, grants, opaque functions, and dynamic SQL are never effect authority.';
comment on table ops.scac_trusted_principal_binding is
  'SIEP-18 same-backend/transaction binding between a server-authenticated actor manifest and actual database session readback. Source/test only and non-authorizing.';

create or replace function ops.scac_register_exact_effect_contract(
  p_registry_version text,p_ingress_key text,p_registry_entry_digest text,
  p_contract jsonb,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare re ops.scac_mutation_registry_entry%rowtype;
        prior ops.scac_exact_effect_contract%rowtype;
        normalized_effects jsonb; normalized_delegates jsonb; contract_digest text;
        resolved_registry_delegates jsonb; registry_delegates_exact boolean;
begin
  if session_user<>'carr_authority_joe' then
    raise exception 'SIEP-18 exact-effect review authority is Joe-only'; end if;
  if coalesce(p_registry_version,'')!~'^scac-mutation-registry\.v[1-9][0-9]*$' or
     coalesce(p_ingress_key,'')!~'^[a-z][a-z0-9_-]+:' or p_ingress_key~E'[\n\r\t]' or
     char_length(p_ingress_key)>1000 or
     coalesce(p_registry_entry_digest,'')!~'^sha256:[0-9a-f]{64}$' or
     p_idempotency_key is null or
     not ops.scac_json_has_exact_keys(p_contract,array['schema_version','ingress_key',
       'direct_effects','delegates_to','sql_state','integration_state']) or
     p_contract->>'schema_version'<>'scac-exact-effect-contract.v1' or
     p_contract->>'ingress_key'<>p_ingress_key or
     p_contract->>'sql_state'<>'static_reviewed' or
     p_contract->>'integration_state'<>'reviewed_source_test' or
     jsonb_typeof(p_contract->'direct_effects')<>'array' or
     jsonb_typeof(p_contract->'delegates_to')<>'array' then
    raise exception 'SIEP-18 exact-effect contract malformed or non-static';
  end if;
  -- This is the SQL spelling of scac-exact-effects.js effectKey(): kind,
  -- exact target, then the already-canonical finite column list.
  select coalesce(jsonb_agg(e order by (case when e->>'kind'='execute'
      then 'execute:'||coalesce(e->>'function_signature','')
      else coalesce(e->>'kind','')||':'||coalesce(e->>'relation','')||':'||
        coalesce((select string_agg(c,',' order by ord) from
          jsonb_array_elements_text(coalesce(e->'columns','[]'::jsonb))
          with ordinality as column_value(c,ord)),'') end) collate "C"),'[]'::jsonb)
    into normalized_effects from jsonb_array_elements(p_contract->'direct_effects') e;
  select coalesce(jsonb_agg(to_jsonb(d) order by d collate "C"),'[]'::jsonb)
    into normalized_delegates from jsonb_array_elements_text(p_contract->'delegates_to') d;
  if p_contract->'direct_effects'<>normalized_effects or
     (select count(*) from jsonb_array_elements(p_contract->'direct_effects'))<>
       (select count(distinct ops.scac_canonical_json(e)) from jsonb_array_elements(p_contract->'direct_effects') e) or
     p_contract->'delegates_to'<>normalized_delegates or
     (select count(*) from jsonb_array_elements_text(p_contract->'delegates_to'))<>
       (select count(distinct d) from jsonb_array_elements_text(p_contract->'delegates_to') d) or
     exists(select 1 from jsonb_array_elements_text(p_contract->'delegates_to') d
       where d!~'^[a-z][a-z0-9_-]+:' or d~E'[\n\r\t*]' or char_length(d)>1000) or
     (jsonb_array_length(p_contract->'direct_effects')=0 and
      jsonb_array_length(p_contract->'delegates_to')=0) then
    raise exception 'SIEP-18 exact-effect contract is empty, duplicate, wildcard, or non-canonical';
  end if;
  if exists(
    select 1 from jsonb_array_elements(p_contract->'direct_effects') e where
      case e->>'kind'
        when 'execute' then
          not ops.scac_json_has_exact_keys(e,array['kind','function_signature']) or
          coalesce(e->>'function_signature','')!~'^[a-z_][a-z0-9_$]*\.[a-z_][a-z0-9_$]*\([^;\n\r]*\)$'
        when 'insert' then
          not ops.scac_json_has_exact_keys(e,array['kind','relation','columns']) or
          coalesce(e->>'relation','')!~'^[a-z_][a-z0-9_$]*\.[a-z_][a-z0-9_$]*$' or
          jsonb_typeof(e->'columns')<>'array' or jsonb_array_length(e->'columns')=0 or
          exists(select 1 from jsonb_array_elements_text(e->'columns') c where c!~'^[a-z_][a-z0-9_$]*$') or
          (select jsonb_agg(to_jsonb(c) order by c collate "C") from jsonb_array_elements_text(e->'columns') c)<>e->'columns' or
          (select count(*) from jsonb_array_elements_text(e->'columns'))<>(select count(distinct c) from jsonb_array_elements_text(e->'columns') c)
        when 'update' then
          not ops.scac_json_has_exact_keys(e,array['kind','relation','columns']) or
          coalesce(e->>'relation','')!~'^[a-z_][a-z0-9_$]*\.[a-z_][a-z0-9_$]*$' or
          jsonb_typeof(e->'columns')<>'array' or jsonb_array_length(e->'columns')=0 or
          exists(select 1 from jsonb_array_elements_text(e->'columns') c where c!~'^[a-z_][a-z0-9_$]*$') or
          (select jsonb_agg(to_jsonb(c) order by c collate "C") from jsonb_array_elements_text(e->'columns') c)<>e->'columns' or
          (select count(*) from jsonb_array_elements_text(e->'columns'))<>(select count(distinct c) from jsonb_array_elements_text(e->'columns') c)
        when 'delete' then
          not ops.scac_json_has_exact_keys(e,array['kind','relation','columns']) or
          coalesce(e->>'relation','')!~'^[a-z_][a-z0-9_$]*\.[a-z_][a-z0-9_$]*$' or
          jsonb_typeof(e->'columns')<>'array' or jsonb_array_length(e->'columns')=0 or
          exists(select 1 from jsonb_array_elements_text(e->'columns') c where c!~'^[a-z_][a-z0-9_$]*$') or
          (select jsonb_agg(to_jsonb(c) order by c collate "C") from jsonb_array_elements_text(e->'columns') c)<>e->'columns' or
          (select count(*) from jsonb_array_elements_text(e->'columns'))<>(select count(distinct c) from jsonb_array_elements_text(e->'columns') c)
        else true end
  ) then raise exception 'SIEP-18 exact effect target or finite column set malformed'; end if;
  select * into re from ops.scac_mutation_registry_entry
    where registry_version=p_registry_version and ingress_key=p_ingress_key for key share;
  if re.ingress_key is null or re.entry_digest<>p_registry_entry_digest or
     re.effect_class='read_only' or
     coalesce((re.contract->>'classification_authorizing')::boolean,true) then
    raise exception 'scac.refusal.mutation_unregistered: SIEP-18 registry/effect contract mismatch';
  end if;
  -- Registry delegates are sealed operation names; exact-effect contracts use
  -- full ingress keys. Resolve each name through the same sealed version and
  -- require exactly one candidate. Prefix guessing and wildcard expansion are
  -- forbidden, so cross-surface edges stay explicit and ambiguity stays deny.
  with raw(d) as (
    select value from jsonb_array_elements_text(coalesce(re.contract->'delegates_to','[]'::jsonb))
  ), candidates as (
    select raw.d,array_agg(de.ingress_key order by de.ingress_key collate "C") keys
      from raw left join ops.scac_mutation_registry_entry de
        on de.registry_version=p_registry_version and
          (de.ingress_key=raw.d or de.contract->>'operation'=raw.d)
     group by raw.d
  )
  select coalesce(jsonb_agg(to_jsonb(keys[1]) order by keys[1] collate "C"),'[]'::jsonb),
         coalesce(bool_and(cardinality(keys)=1),true)
    into resolved_registry_delegates,registry_delegates_exact from candidates;
  if not registry_delegates_exact or
     resolved_registry_delegates<>p_contract->'delegates_to' then
    raise exception 'scac.refusal.mutation_unregistered: SIEP-18 registry/effect delegate mismatch';
  end if;
  contract_digest:=ops.scac_reference_monitor_sha256(p_contract);
  perform pg_advisory_xact_lock(hashtextextended('carr-siep18-exact-effects',0));
  select * into prior from ops.scac_exact_effect_contract where idempotency_key=p_idempotency_key;
  if prior.contract_digest is not null then
    if prior.registry_version<>p_registry_version or prior.ingress_key<>p_ingress_key or
       prior.registry_entry_digest<>p_registry_entry_digest or prior.contract<>p_contract then
      raise exception 'SIEP-18 exact-effect idempotency mismatch'; end if;
    return jsonb_build_object('contract_digest',prior.contract_digest,
      'contract_state','reviewed_source_test','production_enforcement_active',false);
  end if;
  insert into ops.scac_exact_effect_contract
    (registry_version,ingress_key,registry_entry_digest,contract,contract_digest,
     idempotency_key,recorded_by)
  values (p_registry_version,p_ingress_key,p_registry_entry_digest,p_contract,
    contract_digest,p_idempotency_key,'joe');
  return jsonb_build_object('contract_digest',contract_digest,
    'contract_state','reviewed_source_test','production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_exact_effect_union(
  p_registry_version text,p_ingress_key text
) returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public,ops as $fn$
declare result jsonb;
begin
  with recursive walk(ingress_key,path,cycle) as (
    select p_ingress_key,array[p_ingress_key]::text[],false
    union all
    select d,w.path||d,d=any(w.path)
    from walk w join ops.scac_exact_effect_contract c
      on c.registry_version=p_registry_version and c.ingress_key=w.ingress_key
    cross join lateral jsonb_array_elements_text(c.contract->'delegates_to') d
    where not w.cycle
  ), visited as (
    select distinct ingress_key from walk
  ), checked as (
    select count(*) filter(where c.ingress_key is null) missing_count,
      count(*) filter(where c.contract->>'sql_state'<>'static_reviewed' or
        c.contract->>'integration_state'<>'reviewed_source_test') invalid_count
    from visited v left join ops.scac_exact_effect_contract c
      on c.registry_version=p_registry_version and c.ingress_key=v.ingress_key
  ), effects as (
    select distinct e from visited v join ops.scac_exact_effect_contract c
      on c.registry_version=p_registry_version and c.ingress_key=v.ingress_key
    cross join lateral jsonb_array_elements(c.contract->'direct_effects') e
  )
  select jsonb_build_object('cycle',coalesce((select bool_or(cycle) from walk),false),
    'missing_count',(select missing_count from checked),
    'invalid_count',(select invalid_count from checked),
    'effects',coalesce((select jsonb_agg(e order by (case when e->>'kind'='execute'
      then 'execute:'||coalesce(e->>'function_signature','')
      else coalesce(e->>'kind','')||':'||coalesce(e->>'relation','')||':'||
        coalesce((select string_agg(c,',' order by ord) from
          jsonb_array_elements_text(coalesce(e->'columns','[]'::jsonb))
          with ordinality as column_value(c,ord)),'') end) collate "C")
      from effects),'[]'::jsonb))
    into result;
  if (result->>'cycle')::boolean or (result->>'missing_count')::integer<>0 or
     (result->>'invalid_count')::integer<>0 or jsonb_array_length(result->'effects')=0 then
    raise exception 'scac.refusal.mutation_unregistered: SIEP-18 exact recursive effect union unavailable';
  end if;
  return jsonb_build_object('schema_version','scac-exact-effect-union.v1',
    'registry_version',p_registry_version,'ingress_key',p_ingress_key,
    'effects',result->'effects','effect_set_digest',
      ops.scac_reference_monitor_sha256(result->'effects'),
    'production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_bind_trusted_principal(
  p_server_principal jsonb,p_idempotency_key uuid
) returns jsonb language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare actor_row public.actor%rowtype; prior ops.scac_trusted_principal_binding%rowtype;
        expected_bundle text; manifest jsonb; principal_digest text; binding_digest text; binding uuid;
        expected_keys constant text[]:=array['actor_id','actor_kind','actor_slug','authorization_class',
          'authority_sponsor_slug','backend_pid','client_id','human','native_agent_verified',
          'organization_tenant_id','principal_digest','privilege_bundle','production_enforcement_active',
          'schema_version','session_principal','source','sponsoring_human_slug','via'];
begin
  expected_bundle:=ops.scac_runtime_privilege_bundle();
  if expected_bundle is null then
    raise exception 'scac.refusal.identity_unverified: SIEP-18 runtime session refused'; end if;
  if p_idempotency_key is null or not ops.scac_json_has_exact_keys(p_server_principal,expected_keys) or
     jsonb_typeof(p_server_principal->'actor_id') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'actor_kind') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'actor_slug') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'authorization_class') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'organization_tenant_id') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'principal_digest') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'privilege_bundle') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'schema_version') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'session_principal') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'source') is distinct from 'string' or
     jsonb_typeof(p_server_principal->'backend_pid') is distinct from 'number' or
     jsonb_typeof(p_server_principal->'human') is distinct from 'boolean' or
     jsonb_typeof(p_server_principal->'native_agent_verified') is distinct from 'boolean' or
     jsonb_typeof(p_server_principal->'production_enforcement_active') is distinct from 'boolean' or
     coalesce(jsonb_typeof(p_server_principal->'authority_sponsor_slug') not in ('string','null'),true) or
     coalesce(jsonb_typeof(p_server_principal->'client_id') not in ('string','null'),true) or
     coalesce(jsonb_typeof(p_server_principal->'sponsoring_human_slug') not in ('string','null'),true) or
     coalesce(jsonb_typeof(p_server_principal->'via') not in ('string','null'),true) or
     p_server_principal->>'schema_version' is distinct from 'scac-trusted-principal.v1' or
     p_server_principal->>'organization_tenant_id' is distinct from 'carr-internal' or
     p_server_principal->>'source' is distinct from 'server_authenticated_actor_plus_database_readback' or
     p_server_principal->'production_enforcement_active' is distinct from 'false'::jsonb or
     coalesce(p_server_principal->>'actor_id','')!~
       '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' or
     p_server_principal->>'actor_kind' not in ('human','automation','system') or
     p_server_principal->>'session_principal' is distinct from session_user or
     p_server_principal->>'privilege_bundle' is distinct from expected_bundle or
     p_server_principal->'backend_pid' is distinct from to_jsonb(pg_backend_pid()) or
     coalesce(p_server_principal->>'principal_digest','')!~'^sha256:[0-9a-f]{64}$' then
    raise exception 'scac.refusal.identity_unverified: SIEP-18 trusted principal malformed or session-mismatched';
  end if;
  manifest:=p_server_principal-'principal_digest'-'source'-'production_enforcement_active';
  principal_digest:=ops.scac_reference_monitor_sha256(manifest);
  if principal_digest<>p_server_principal->>'principal_digest' then
    raise exception 'scac.refusal.identity_unverified: SIEP-18 trusted principal digest mismatch'; end if;
  select * into actor_row from public.actor
    where id=(p_server_principal->>'actor_id')::uuid
      and slug=p_server_principal->>'actor_slug' and active for key share;
  if actor_row.id is null then
    raise exception 'scac.refusal.identity_unverified: SIEP-18 authenticated actor unavailable'; end if;
  if actor_row.kind is distinct from p_server_principal->>'actor_kind' or
     p_server_principal->'human' is distinct from to_jsonb(actor_row.kind='human') or
     (actor_row.kind='human' and
       (actor_row.slug not in ('joe','dell') or
        p_server_principal->>'sponsoring_human_slug' is distinct from actor_row.slug or
        p_server_principal->>'authorization_class' is distinct from 'verified_partner' or
        p_server_principal->'native_agent_verified' is distinct from 'false'::jsonb)) or
     (actor_row.kind='automation' and
       (p_server_principal->>'authorization_class' is distinct from case
          when p_server_principal->>'sponsoring_human_slug' in ('joe','dell')
            then 'sponsored_agent' else 'unsponsored_agent' end or
        (p_server_principal->>'sponsoring_human_slug' is not null and not (
          (actor_row.slug in ('codex','claude') and
            p_server_principal->>'sponsoring_human_slug' in ('joe','dell')) or
          (actor_row.slug='joe-local' and p_server_principal->>'sponsoring_human_slug'='joe') or
          (actor_row.slug='dell-local' and p_server_principal->>'sponsoring_human_slug'='dell'))))) or
     actor_row.kind='system' or
     (expected_bundle='carr_authority' and
       (p_server_principal->>'authority_sponsor_slug' is null or
        p_server_principal->>'authority_sponsor_slug' not in ('joe','dell') or
        p_server_principal->>'authority_sponsor_slug' is distinct from
          p_server_principal->>'sponsoring_human_slug' or
        session_user is distinct from
          'carr_authority_'||(p_server_principal->>'authority_sponsor_slug') or
        (actor_row.kind='human' and actor_row.slug is distinct from
          p_server_principal->>'authority_sponsor_slug') or
        (actor_row.kind='automation' and
          (actor_row.slug not in ('codex','claude','joe-local','dell-local') or
           p_server_principal->'native_agent_verified' is distinct from 'true'::jsonb)))) or
     (expected_bundle<>'carr_authority' and
       p_server_principal->>'authority_sponsor_slug' is not null) then
    raise exception 'scac.refusal.identity_unverified: SIEP-18 actor kind, sponsor, or authority semantics mismatched';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('carr-siep18-trusted-principal',0));
  select * into prior from ops.scac_trusted_principal_binding where idempotency_key=p_idempotency_key;
  if prior.binding_id is not null then
    if prior.principal_manifest<>manifest or prior.principal_digest<>principal_digest or
       prior.session_principal<>session_user or prior.backend_pid<>pg_backend_pid() or
       prior.transaction_id<>txid_current() then
      raise exception 'scac.refusal.replay: SIEP-18 trusted-principal idempotency mismatch'; end if;
    return jsonb_build_object('binding_id',prior.binding_id::text,
      'binding_digest',prior.binding_digest,'binding_state','current_source_test',
      'production_enforcement_active',false);
  end if;
  binding:=gen_random_uuid();
  binding_digest:=ops.scac_reference_monitor_sha256(jsonb_build_object(
    'schema_version','scac-trusted-principal-binding.v1','binding_id',binding::text,
    'principal_digest',principal_digest,'session_principal',session_user,
    'privilege_bundle',expected_bundle,'backend_pid',pg_backend_pid(),
    'transaction_id',txid_current(),'idempotency_key',p_idempotency_key::text));
  insert into ops.scac_trusted_principal_binding
    (binding_id,principal_manifest,principal_digest,actor_id,actor_slug,session_principal,
     privilege_bundle,backend_pid,transaction_id,binding_digest,idempotency_key,source_contract)
  values (binding,manifest,principal_digest,actor_row.id,actor_row.slug,session_user,
    expected_bundle,pg_backend_pid(),txid_current(),binding_digest,p_idempotency_key,
    'mcp-server/src/scac-exact-effects.js');
  return jsonb_build_object('binding_id',binding::text,'binding_digest',binding_digest,
    'binding_state','current_source_test','production_enforcement_active',false);
end $fn$;

create trigger scac_exact_effect_contract_immutable before update or delete
  on ops.scac_exact_effect_contract for each row execute function ops.scac_siep18_append_only_guard();
create trigger scac_exact_effect_contract_no_truncate before truncate
  on ops.scac_exact_effect_contract for each statement execute function ops.scac_siep18_truncate_guard();
create trigger scac_trusted_principal_binding_immutable before update or delete
  on ops.scac_trusted_principal_binding for each row execute function ops.scac_siep18_append_only_guard();
create trigger scac_trusted_principal_binding_no_truncate before truncate
  on ops.scac_trusted_principal_binding for each statement execute function ops.scac_siep18_truncate_guard();

revoke all on table ops.scac_exact_effect_contract,ops.scac_trusted_principal_binding
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.scac_register_exact_effect_contract(text,text,text,jsonb,uuid),
  ops.scac_exact_effect_union(text,text),ops.scac_bind_trusted_principal(jsonb,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
-- No runtime EXECUTE grant is introduced by this bounded successor. Granting
-- any helper would change the sealed v9 capability census and is reserved for
-- a later forward registry plus source-test cutover reviewed as one boundary.
